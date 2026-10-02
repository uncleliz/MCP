"""Context-compression that NEVER drops provenance (T-098, ADR-0018 §6 GT-7, ADR-0020).

The pack must fit a token budget before the grounding gate, but compression is **extractive and
rule-based** — no LLM, no egress. The invariant that makes GT-7 pass: compressing a candidate may
shorten its *content* but must preserve every provenance field the grounding gate needs. A
candidate is dropped **whole** when the budget is exhausted (it never silently loses its evidence);
a kept candidate keeps all 6 provenance fields.

Determinism: the input order (the fused/reranked order) is preserved exactly; compression is a pure
function of the input, so the same candidates always compress the same way (no wall-clock, no RNG).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, replace

from mcp_knowledge.retrieval.hybrid import Candidate

__all__ = ["CompressedChunk", "compress_candidates", "estimate_tokens"]

_WORD = re.compile(r"\S+")


def estimate_tokens(text: str) -> int:
    """A cheap, deterministic token estimate (whitespace words). No tokenizer download needed."""
    return len(_WORD.findall(text))


@dataclass(frozen=True)
class CompressedChunk:
    """A candidate after compression. ``candidate`` keeps every provenance-bearing field intact
    (document_id, source_uri, source_updated_at, author, chunk_id via the Candidate); ``content``
    is the compressed excerpt; ``token_count`` is the excerpt's estimate; ``truncated`` records
    whether its content was shortened."""

    candidate: Candidate
    content: str
    token_count: int
    truncated: bool


def _compress_text(text: str, max_tokens: int) -> tuple[str, bool]:
    """Keep the first ``max_tokens`` whitespace tokens; report whether anything was cut."""
    words = _WORD.findall(text)
    if len(words) <= max_tokens:
        return text, False
    return " ".join(words[:max_tokens]), True


def compress_candidates(
    candidates: Sequence[Candidate],
    *,
    token_budget: int = 2048,
    per_chunk_max_tokens: int = 512,
) -> list[CompressedChunk]:
    """Compress candidates to fit ``token_budget`` while preserving every candidate's provenance.

    Walks candidates in input order, compresses each to at most ``per_chunk_max_tokens``, and keeps
    it only if the running total stays within ``token_budget``. A candidate that would overflow is
    **dropped whole** (its evidence is never partially retained in a way that loses provenance).
    """
    if token_budget <= 0:
        raise ValueError(f"token_budget must be positive, got {token_budget}")

    kept: list[CompressedChunk] = []
    used = 0
    for candidate in candidates:
        content, truncated = _compress_text(candidate.content, per_chunk_max_tokens)
        count = estimate_tokens(content)
        if used + count > token_budget:
            continue  # drop whole; never keep a provenance-less fragment
        used += count
        # `replace` keeps the Candidate (and thus all provenance fields) intact; we carry the
        # compressed content alongside rather than mutating the candidate's own content.
        kept.append(
            CompressedChunk(
                candidate=replace(candidate, content=content),
                content=content,
                token_count=count,
                truncated=truncated,
            )
        )
    return kept
