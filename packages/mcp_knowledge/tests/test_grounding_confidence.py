"""Deterministic confidence (ADR-0018 D1) — unit tests (T-106).

Confidence is ``retrieval^w_r · agreement^w_a · freshness^w_f`` (geometric), a pure function of its
inputs: same inputs ⇒ same score, no LLM, no wall-clock. These assert the shape and the invariants
that hold independently of the (TBD) τ threshold.
"""

from __future__ import annotations

import pytest
from mcp_knowledge.grounding.confidence import (
    DEFAULT_WEIGHTS,
    TAU_FACT,
    TAU_LOW,
    ConfidenceWeights,
    agreement_factor,
    compute_confidence,
    normalise_retrieval,
)


def test_default_weights_mirror_migration_0008_seed() -> None:
    assert (DEFAULT_WEIGHTS.retrieval, DEFAULT_WEIGHTS.agreement, DEFAULT_WEIGHTS.freshness) == (
        0.5,
        0.3,
        0.2,
    )


def test_weights_must_sum_to_one() -> None:
    with pytest.raises(ValueError, match="sum to 1"):
        ConfidenceWeights(retrieval=0.5, agreement=0.5, freshness=0.5)


def test_weight_out_of_range_rejected() -> None:
    with pytest.raises(ValueError, match="in \\[0, 1\\]"):
        ConfidenceWeights(retrieval=1.5, agreement=-0.3, freshness=-0.2)


def test_agreement_grows_with_independent_sources() -> None:
    assert agreement_factor(0) == 0.0
    assert agreement_factor(1) == 0.5
    assert agreement_factor(2) == pytest.approx(2 / 3)
    assert agreement_factor(10) > agreement_factor(2)
    assert agreement_factor(-5) == 0.0  # clamped


def test_normalise_retrieval_min_max_and_degenerate() -> None:
    assert normalise_retrieval(5.0, lo=0.0, hi=10.0) == 0.5
    assert normalise_retrieval(10.0, lo=0.0, hi=10.0) == 1.0
    assert normalise_retrieval(0.0, lo=0.0, hi=10.0) == 0.0
    # Degenerate range (single candidate / all-equal) ⇒ 1.0, not a divide-by-zero.
    assert normalise_retrieval(3.0, lo=3.0, hi=3.0) == 1.0
    # Out-of-range is clamped.
    assert normalise_retrieval(20.0, lo=0.0, hi=10.0) == 1.0


def test_compute_confidence_is_geometric_and_deterministic() -> None:
    a = compute_confidence(retrieval=1.0, agreement=0.5, freshness=1.0)
    b = compute_confidence(retrieval=1.0, agreement=0.5, freshness=1.0)
    assert a == b  # deterministic
    # retrieval^0.5 · agreement^0.3 · freshness^0.2 = 1 · 0.5^0.3 · 1 ≈ 0.8123
    assert a == pytest.approx(0.5**0.3, abs=1e-6)


def test_a_near_zero_factor_drags_the_whole_score_down() -> None:
    stale = compute_confidence(retrieval=1.0, agreement=1.0, freshness=0.01)
    fresh = compute_confidence(retrieval=1.0, agreement=1.0, freshness=1.0)
    assert stale < fresh
    assert 0.0 <= stale <= 1.0


def test_zero_factor_yields_zero() -> None:
    assert compute_confidence(retrieval=0.0, agreement=1.0, freshness=1.0) == 0.0


def test_result_is_clamped_to_unit_interval() -> None:
    high = compute_confidence(retrieval=2.0, agreement=2.0, freshness=2.0)
    assert high == 1.0  # inputs clamped to 1 before combining


def test_tau_ordering_and_type() -> None:
    # τ are starter infrastructure constants (marked TBD in source); here we only assert ordering.
    assert 0.0 <= TAU_LOW <= TAU_FACT <= 1.0
