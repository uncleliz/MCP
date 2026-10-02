"""Deterministic confidence for the grounding gate (ADR-0018 §3 + D1, T-106/T-108).

``confidence`` is the **strength of the evidence** behind a claim — labelled exactly that
(:data:`mcp_common.envelope.CONFIDENCE_BASIS`), **not** P(claim true) (L-002). It is a pure,
deterministic function of three factors in ``[0, 1]`` combined **geometrically** (ADR-0018 D1 — the
geometric form lets one very weak factor, e.g. a long-stale fact, pull the whole score down):

    confidence = retrieval^w_r · agreement^w_a · freshness^w_f

* **retrieval**  — rerank/similarity strength, normalised to ``[0, 1]`` over the candidate set.
* **agreement**  — ``1 − 1/(1 + n_independent)``: one source → ~0.5, more agreeing independent
  sources → →1 (ADR-0018 D1).
* **freshness**  — :func:`mcp_knowledge.liveness.freshness.assess_freshness` factor (fresh → 1.0,
  stale → reduced-but-never-zero).

Weights default to ``0.5 / 0.3 / 0.2`` seeded in migration 0008 (``kb.confidence_weights``), read
from the DB — never hardcoded in a prompt (spec §42). The weights **sum to 1** so the geometric mean
is well-defined; a safe fallback mirrors the migration seed when the config row is absent.

No LLM, no egress, no wall-clock in the formula: the same inputs always give the same score.

## Thresholds τ — TBD (do NOT invent a number)
The FACT↔LOW_CONFIDENCE threshold is only meaningful once calibrated on a real company golden-set,
which is blocked by HF egress (NFR-003/NFR-010 UNVERIFIED, L-002/E-004). The starter constants below
exist **only so the infrastructure runs**; the envelope always reports
``calibration_status="uncalibrated"`` and the verdict-direction invariants (no-evidence ⇒ UNKNOWN,
confidence never rescues a source-less claim) hold **independently of τ**.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "TAU_FACT",
    "TAU_LOW",
    "ConfidenceWeights",
    "DEFAULT_WEIGHTS",
    "agreement_factor",
    "compute_confidence",
    "normalise_retrieval",
]

# -- τ thresholds: STARTER values for infrastructure tests ONLY, NOT calibrated ------------------
# THRESHOLD TBD (NFR-010, L-002) — chốt khi gỡ egress HF chạy golden-set thật
TAU_FACT = 0.6
# THRESHOLD TBD (NFR-010, L-002) — chốt khi gỡ egress HF chạy golden-set thật
TAU_LOW = 0.3


@dataclass(frozen=True)
class ConfidenceWeights:
    """The three deterministic confidence weights (geometric exponents), ADR-0018 D1.

    Loaded from ``kb.confidence_weights`` (migration 0008); the default mirrors that seed exactly so
    an absent config row degrades gracefully instead of inventing weights.
    """

    retrieval: float = 0.5
    agreement: float = 0.3
    freshness: float = 0.2

    def __post_init__(self) -> None:
        for name in ("retrieval", "agreement", "freshness"):
            value = getattr(self, name)
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"confidence weight {name!r} must be in [0, 1], got {value}")
        if round(self.retrieval + self.agreement + self.freshness, 3) != 1.0:
            raise ValueError(
                "confidence weights must sum to 1.0 "
                f"(got {self.retrieval}+{self.agreement}+{self.freshness})"
            )


#: Mirrors the ``kb.confidence_weights`` 'default' seed in migration 0008 (0.5 / 0.3 / 0.2).
DEFAULT_WEIGHTS = ConfidenceWeights()


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def normalise_retrieval(score: float, *, lo: float, hi: float) -> float:
    """Min-max normalise a retrieval score into ``[0, 1]`` over the candidate set's ``[lo, hi]``.

    When every candidate shares one score (``hi == lo``) the range is degenerate; we return 1.0 so
    a single, equally-ranked result is not spuriously penalised (the ordering already stands in for
    quality). The result is always clamped to ``[0, 1]`` so a stray score cannot escape the range.
    """
    if hi <= lo:
        return 1.0
    return _clamp01((score - lo) / (hi - lo))


def agreement_factor(n_independent: int) -> float:
    """``1 − 1/(1 + n)`` for ``n`` independent agreeing sources (ADR-0018 D1).

    ``n_independent`` counts distinct independent sources that assert the claim (0 sources → 0.0,
    1 → 0.5, 2 → ~0.667, growing toward 1.0). Clamped so a negative count cannot occur.
    """
    n = max(0, n_independent)
    return n / (1.0 + n)


def compute_confidence(
    *,
    retrieval: float,
    agreement: float,
    freshness: float,
    weights: ConfidenceWeights = DEFAULT_WEIGHTS,
) -> float:
    """Combine the three factors geometrically: ``retrieval^w_r · agreement^w_a · freshness^w_f``.

    Every factor is clamped to ``[0, 1]`` first. The geometric form means a single near-zero factor
    (e.g. a very stale fact) drags the whole score down, which matches the ADR-0018 intuition. The
    result is a deterministic evidence-strength in ``[0, 1]`` — NOT a probability the claim is true.
    """
    r = _clamp01(retrieval)
    a = _clamp01(agreement)
    f = _clamp01(freshness)
    score = (r**weights.retrieval) * (a**weights.agreement) * (f**weights.freshness)
    return round(_clamp01(score), 6)
