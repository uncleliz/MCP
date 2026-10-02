"""Freshness model (ADR-0018 §4, FR-018/AC-003, migration 0008 ``kb.freshness_horizon``).

A fact of a given type stays "fresh" for ``horizon_hours``. Past that horizon its freshness
*factor* is **reduced** (the claim is down-weighted in the deterministic confidence
``retrieval × agreement × freshness``) — but the claim is **never made false by age alone**, and
never dropped: an old fact is still a fact, just less confidently current. The staleness is always
observable in ``meta.data_freshness`` so Claude can say so (spec §35).

The exact down-weight *curve* and the FACT↔LOW_CONFIDENCE threshold τ are **TBD** — they are only
meaningful once calibrated on a real golden-set, which is blocked by HF egress (NFR-010, L-002,
E-004). This module therefore commits only to the *direction* the contract and TC-088 assert:

* fresh (within horizon)            ⇒ factor == 1.0
* stale (past horizon)              ⇒ 0 < factor < 1.0  (reduced, never 0, never negative)
* no horizon configured (NULL)      ⇒ factor == 1.0 (never considered stale)
* unknown age (no timestamp)        ⇒ factor == FRESHNESS_UNKNOWN (a mild, bounded discount)

A monotone decay past the horizon (older ⇒ smaller, bounded below by ``FRESHNESS_FLOOR``) satisfies
"reduced, not zeroed" without inventing a calibrated number; the floor keeps an ancient fact from
collapsing to zero (which would effectively make it false).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

__all__ = [
    "FRESHNESS_FLOOR",
    "FRESHNESS_UNKNOWN",
    "FreshnessAssessment",
    "assess_freshness",
]

#: Lower bound on the freshness factor. An arbitrarily old fact is still down-weighted, never
#: zeroed — a factor of 0 would make it effectively false, which §4 forbids. NOT a calibrated τ.
FRESHNESS_FLOOR = 0.25

#: Factor applied when a fact carries no usable timestamp at all: a mild, bounded discount (we
#: cannot confirm it is current, but absence of a date is not evidence of staleness).
FRESHNESS_UNKNOWN = 0.75


@dataclass(frozen=True)
class FreshnessAssessment:
    """The freshness verdict for one fact.

    ``factor`` multiplies into the deterministic confidence (ADR-0018 D1); ``staleness_hours`` is
    surfaced in ``meta.data_freshness``; ``is_stale`` is True once the fact is past its horizon.
    ``horizon_hours`` is echoed back so the caller can explain *why* a fact is stale.
    """

    factor: float
    staleness_hours: float | None
    is_stale: bool
    horizon_hours: int | None


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _hours_between(updated: datetime, now: datetime) -> float:
    delta = _as_utc(now) - _as_utc(updated)
    return round(delta.total_seconds() / 3600.0, 1)


def assess_freshness(
    *,
    updated_time: datetime | None,
    horizon_hours: int | None,
    now: datetime | None = None,
) -> FreshnessAssessment:
    """Assess how fresh a fact is given its ``updated_time`` and configured ``horizon_hours``.

    ``horizon_hours is None`` means "never considered stale" (migration 0008 allows a NULL
    horizon). ``updated_time is None`` means we cannot date the fact — a mild unknown discount,
    not a staleness claim. Otherwise the factor is 1.0 within the horizon and decays monotonically
    (bounded below by :data:`FRESHNESS_FLOOR`) once past it.
    """
    now = now or datetime.now(UTC)
    if updated_time is None:
        return FreshnessAssessment(
            factor=FRESHNESS_UNKNOWN,
            staleness_hours=None,
            is_stale=False,
            horizon_hours=horizon_hours,
        )

    staleness = max(_hours_between(updated_time, now), 0.0)

    if horizon_hours is None:
        return FreshnessAssessment(
            factor=1.0, staleness_hours=staleness, is_stale=False, horizon_hours=None
        )

    if staleness <= horizon_hours:
        return FreshnessAssessment(
            factor=1.0, staleness_hours=staleness, is_stale=False, horizon_hours=horizon_hours
        )

    # Past the horizon: decay as horizon/age (so at exactly the horizon factor≈1.0 and it falls off
    # for older facts), clamped to the floor. This is a *direction*, not a calibrated curve
    # (NFR-010 TBD) — it only guarantees "reduced, >0, <1".
    decay = horizon_hours / staleness if staleness > 0 else 1.0
    factor = max(FRESHNESS_FLOOR, min(decay, 1.0))
    return FreshnessAssessment(
        factor=factor, staleness_hours=staleness, is_stale=True, horizon_hours=horizon_hours
    )
