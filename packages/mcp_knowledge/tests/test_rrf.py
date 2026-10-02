"""T-095 — Reciprocal Rank Fusion: deterministic, k=60, stable tie-break."""

from __future__ import annotations

import pytest
from mcp_knowledge.retrieval.rrf import DEFAULT_RRF_K, RankedLeg, rrf_fuse


def test_default_k_is_60() -> None:
    # AC: RRF k=60 is the ADR-0020 constant the engine fuses with.
    assert DEFAULT_RRF_K == 60


def test_rrf_fuse_is_deterministic_fixed_input_fixed_output() -> None:
    # AC (TC-078): RRF produces a deterministic ranking — same inputs, same output order, every run.
    vector = RankedLeg("vector", [10, 20, 30])
    keyword = RankedLeg("keyword", [30, 40, 10])
    first = rrf_fuse([vector, keyword], k=60)
    for _ in range(20):
        assert rrf_fuse([vector, keyword], k=60) == first


def test_candidate_in_both_legs_outranks_single_leg() -> None:
    # A chunk surfaced by BOTH the vector and keyword legs fuses higher than a single-leg one.
    vector = RankedLeg("vector", [1, 2, 3])
    keyword = RankedLeg("keyword", [3, 9, 8])
    fused = rrf_fuse([vector, keyword], k=60)
    ids = [cid for cid, _ in fused]
    # id=3 appears in both legs -> its fused score is highest.
    assert ids[0] == 3


def test_rrf_score_matches_the_formula() -> None:
    # Explicit 1/(k+rank) accumulation, so the number is auditable (not a black box).
    fused = dict(rrf_fuse([RankedLeg("a", [7]), RankedLeg("b", [7])], k=60))
    assert fused[7] == pytest.approx(2 * (1.0 / (60 + 1)))


def test_tie_break_is_by_identity_not_insertion_order() -> None:
    # Two chunks with identical fused score must order by identity, independent of leg order.
    forward = rrf_fuse([RankedLeg("x", [5, 8])], k=60)
    reversed_leg = rrf_fuse([RankedLeg("x", [8, 5])], k=60)
    # rank-1 vs rank-1 differ, so this checks the within-equal-score path:
    both_rank1 = rrf_fuse([RankedLeg("a", [5]), RankedLeg("b", [8])], k=60)
    assert [cid for cid, _ in both_rank1] == [5, 8]  # equal score 1/61, 5 < 8
    assert forward[0][0] == 5 and reversed_leg[0][0] == 8


def test_limit_cuts_fused_output() -> None:
    fused = rrf_fuse([RankedLeg("v", [1, 2, 3, 4, 5])], k=60, limit=2)
    assert len(fused) == 2


def test_empty_legs_fuse_to_empty() -> None:
    assert rrf_fuse([RankedLeg("v", []), RankedLeg("k", [])], k=60) == []


def test_non_positive_k_rejected() -> None:
    with pytest.raises(ValueError, match="positive"):
        rrf_fuse([RankedLeg("v", [1])], k=0)
