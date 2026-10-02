"""Reciprocal Rank Fusion (RRF) — deterministic merge of ranked legs (ADR-0020).

RRF fuses several *ranked* lists without comparing scores across incomparable spaces (cosine
distance vs `ts_rank`): a candidate's fused score is ``sum over legs of 1/(k + rank)`` where
``rank`` is 1-based within each leg. The default ``k=60`` is the value named in ADR-0020.

The fusion is **deterministic**: ties (equal fused score) are broken by a stable key derived only
from the candidate identity, so the same inputs always produce the same output order (contract of
T-095 / TC-078). No wall-clock, no iteration-order, no score from an incomparable space leaks in.
"""

from __future__ import annotations

from collections.abc import Hashable, Sequence
from dataclasses import dataclass

__all__ = ["DEFAULT_RRF_K", "RankedLeg", "rrf_fuse"]

DEFAULT_RRF_K = 60


@dataclass(frozen=True)
class RankedLeg:
    """One ranked leg of the hybrid search (e.g. the vector leg or the keyword leg).

    ``name`` labels the leg (for provenance/telemetry); ``ids`` is the leg's result identities in
    rank order (best first). Identities must be hashable and comparable for the deterministic
    tie-break (chunk ids are integers in practice).
    """

    name: str
    ids: Sequence[Hashable]


def rrf_fuse(
    legs: Sequence[RankedLeg],
    *,
    k: int = DEFAULT_RRF_K,
    limit: int | None = None,
) -> list[tuple[Hashable, float]]:
    """Fuse ranked legs into a single ``[(id, fused_score), ...]`` ordered best-first.

    Deterministic: equal fused scores are broken by the candidate identity (ascending), so the
    result never depends on dict/iteration order. A candidate missing from a leg simply contributes
    nothing from that leg.
    """
    if k <= 0:
        raise ValueError(f"RRF k must be positive, got {k}")

    scores: dict[Hashable, float] = {}
    for leg in legs:
        for rank, identity in enumerate(leg.ids, start=1):
            scores[identity] = scores.get(identity, 0.0) + 1.0 / (k + rank)

    # Stable, fully-determined order: highest fused score first, then by identity ascending so
    # ties never depend on insertion/iteration order.
    ordered = sorted(scores.items(), key=lambda item: (-item[1], _tie_key(item[0])))
    if limit is not None:
        ordered = ordered[:limit]
    return ordered


def _tie_key(identity: Hashable) -> tuple[int, object]:
    """A total order over mixed identity types for a stable tie-break (ints before other types)."""
    if isinstance(identity, bool):  # bool is an int subclass; keep it out of the numeric bucket
        return (1, str(identity))
    if isinstance(identity, int | float):
        return (0, identity)
    return (1, str(identity))
