"""T-098 — context-compression preserves provenance (ADR-0018 §6 GT-7), deterministic, no egress."""

from __future__ import annotations

import pytest
from mcp_knowledge.pack.compress import compress_candidates, estimate_tokens
from mcp_knowledge.retrieval.hybrid import Candidate


def _candidate(cid: int, words: int = 20) -> Candidate:
    return Candidate(
        chunk_id=cid,
        document_id=f"doc-{cid}",
        chunk_index=cid,
        content=" ".join(f"w{i}" for i in range(words)),
        heading_path=f"Section {cid}",
        source_type="confluence",
        source_id=str(cid),
        source_uri=f"https://wiki.example.com/p/{cid}",
        title=f"Doc {cid}",
        container="PAY",
        author="an.nguyen",
        source_updated_at=None,
        ingested_at=None,
        rrf_score=1.0 / cid,
        legs={"vector": cid},
    )


def test_compression_preserves_every_provenance_field_gt7() -> None:
    # GT-7: after compression each kept claim still carries all provenance-bearing fields.
    cands = [_candidate(1), _candidate(2)]
    kept = compress_candidates(cands, token_budget=1000, per_chunk_max_tokens=5)
    assert len(kept) == 2
    for original, chunk in zip(cands, kept, strict=True):
        c = chunk.candidate
        assert c.document_id == original.document_id
        assert c.source_uri == original.source_uri
        assert c.chunk_id == original.chunk_id
        assert c.source_type == original.source_type
        assert c.author == original.author
        assert c.heading_path == original.heading_path
        # content may be shortened, but the provenance stays intact.
        assert chunk.truncated is True
        assert estimate_tokens(chunk.content) <= 5


def test_candidate_overflowing_budget_is_dropped_whole_not_stripped() -> None:
    # A candidate that cannot fit is dropped ENTIRELY — never kept without its provenance.
    big = _candidate(1, words=100)
    small = _candidate(2, words=3)
    kept = compress_candidates([big, small], token_budget=5, per_chunk_max_tokens=100)
    # big (100 tokens) overflows the 5-token budget -> dropped; small (3) fits.
    assert [c.candidate.chunk_id for c in kept] == [2]


def test_input_order_preserved() -> None:
    cands = [_candidate(3), _candidate(1), _candidate(2)]
    kept = compress_candidates(cands, token_budget=1000)
    assert [c.candidate.chunk_id for c in kept] == [3, 1, 2]


def test_compression_is_deterministic() -> None:
    cands = [_candidate(i, words=30) for i in range(1, 6)]
    first = compress_candidates(cands, token_budget=40, per_chunk_max_tokens=10)
    for _ in range(10):
        again = compress_candidates(cands, token_budget=40, per_chunk_max_tokens=10)
        assert [c.candidate.chunk_id for c in again] == [c.candidate.chunk_id for c in first]
        assert [c.content for c in again] == [c.content for c in first]


def test_short_content_not_truncated() -> None:
    kept = compress_candidates([_candidate(1, words=3)], per_chunk_max_tokens=10)
    assert kept[0].truncated is False


def test_non_positive_budget_rejected() -> None:
    with pytest.raises(ValueError, match="token_budget"):
        compress_candidates([_candidate(1)], token_budget=0)
