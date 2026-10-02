"""Context-pack assembler skeleton (T-098, ADR-0020, ADR-0018 §2, ADR-0021 §2).

This is the **single structural seam** that later becomes the grounding gate (choke point #2, E8,
T-106). At E3 it does NOT assign a grounding verdict — it only gathers claims + provenance from the
compressed candidates. The design makes the two choke points explicit and *separate* so neither is
duplicated (L-001):

* ``permission_filter`` — choke point #1 (E6, ``enforce_permission``). Runs **first**, default-deny.
  At E3 the default is the identity filter (a clearly-marked seam, not an enforcement). E6 replaces
  it with the real default-deny filter; this assembler never retrieves — it only receives already
  permitted candidates, so permission is enforced *before* assembly, never inside it.
* ``grounding_gate`` — choke point #2 (E8, the verdict). At E3 the default gathers claims with no
  verdict (``RawClaim``). E8 replaces it with the deterministic verdict assignment.

There is exactly **one** path from candidates to a context-pack (``assemble``); a structural test
(E8/T-100) asserts no bypass. Keeping both hooks on this one object is what guarantees "one choke
point per guarantee, not two gates".
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from mcp_common.envelope import Claim

from mcp_knowledge.pack.compress import CompressedChunk
from mcp_knowledge.retrieval.hybrid import Candidate

__all__ = [
    "RawClaim",
    "ContextPack",
    "ContextPackAssembler",
    "PermissionFilter",
    "GroundingGate",
    "identity_permission_filter",
]


@dataclass(frozen=True)
class RawClaim:
    """A claim candidate BEFORE a grounding verdict is assigned (E3 skeleton).

    Carries the full provenance the E8 gate will grade: the source chunk's text and every
    provenance-bearing field from the backing document. E8 turns this into a graded
    :class:`mcp_common.envelope.Claim` with a verdict; E3 never guesses a verdict.
    """

    text: str
    source_type: str
    source_uri: str
    document_id: str
    chunk_id: int
    source_updated_at: object | None
    author: str | None
    heading_path: str | None


@dataclass(frozen=True)
class ContextPack:
    """The assembled context-pack: raw claims + their compressed evidence, ready for the gate.

    ``reranker_status`` is threaded through so the eventual ``grounding_summary.reranker`` reports
    the RRF-only fallback transparently (L-002). ``verdicts_assigned`` is False at E3 — the gate
    (E8) flips it when it grades claims. ``graded_claims`` carries the E8 gate's graded
    :class:`~mcp_common.envelope.Claim`s (verdict + provenance); it is empty until the gate runs,
    so the E3/E4 skeleton path is unchanged.
    """

    claims: list[RawClaim]
    chunks: list[CompressedChunk]
    reranker_status: str = "enabled"
    verdicts_assigned: bool = False
    notes: list[str] = field(default_factory=list)
    graded_claims: list[Claim] = field(default_factory=list)


class PermissionFilter(Protocol):
    """Choke point #1 (E6). Given candidates, return only those the caller may see.

    Default-deny: a candidate absent from the grants is dropped.
    """

    def __call__(self, candidates: Sequence[Candidate]) -> list[Candidate]: ...


class GroundingGate(Protocol):
    """Choke point #2 (E8). Given compressed chunks, produce the graded context-pack."""

    def __call__(self, chunks: Sequence[CompressedChunk]) -> ContextPack: ...


def identity_permission_filter(candidates: Sequence[Candidate]) -> list[Candidate]:
    """E3 seam for choke point #1. Returns candidates unchanged.

    NOT an enforcement — E6 (T-104) replaces this with the real default-deny
    ``enforce_permission``. It exists so the single choke-point location is already wired and E6
    only swaps the implementation, never adds a second filtering site.
    """
    return list(candidates)


def _skeleton_grounding_gate(chunks: Sequence[CompressedChunk]) -> ContextPack:
    """E3 seam for choke point #2. Gathers raw claims + provenance; assigns NO verdict.

    E8 (T-106) replaces this with the deterministic verdict gate. Keeping the signature identical
    means E8 is a drop-in at the same single choke point.
    """
    claims = [
        RawClaim(
            text=chunk.content,
            source_type=chunk.candidate.source_type,
            source_uri=chunk.candidate.source_uri,
            document_id=chunk.candidate.document_id,
            chunk_id=chunk.candidate.chunk_id,
            source_updated_at=chunk.candidate.source_updated_at,
            author=chunk.candidate.author,
            heading_path=chunk.candidate.heading_path,
        )
        for chunk in chunks
    ]
    return ContextPack(claims=claims, chunks=list(chunks), verdicts_assigned=False)


class ContextPackAssembler:
    """The one place candidates become a context-pack. Permission (#1) runs before the gate (#2)."""

    def __init__(
        self,
        *,
        permission_filter: PermissionFilter | None = None,
        grounding_gate: GroundingGate | None = None,
        reranker_status: str = "enabled",
    ) -> None:
        self._permission_filter = permission_filter or identity_permission_filter
        self._grounding_gate = grounding_gate or _skeleton_grounding_gate
        self._reranker_status = reranker_status

    def assemble(
        self,
        candidates: Sequence[Candidate],
        chunks: Sequence[CompressedChunk],
    ) -> ContextPack:
        """Assemble a context-pack from permitted, compressed candidates.

        ``candidates`` is used to run the permission choke point (#1); ``chunks`` are the compressed
        evidence (already ordered). Permission is applied to the candidate set and the surviving
        chunks are passed to the grounding gate (#2) — permission strictly before grounding.
        """
        permitted = self._permission_filter(candidates)
        permitted_ids = {c.chunk_id for c in permitted}
        kept_chunks = [ch for ch in chunks if ch.candidate.chunk_id in permitted_ids]
        pack = self._grounding_gate(kept_chunks)
        # Thread the reranker status through so the summary can report RRF-only fallback (L-002).
        return ContextPack(
            claims=pack.claims,
            chunks=pack.chunks,
            reranker_status=self._reranker_status,
            verdicts_assigned=pack.verdicts_assigned,
            notes=pack.notes,
            graded_claims=pack.graded_claims,
        )
