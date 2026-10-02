"""Grounding telemetry — a deterministic trace of the gate's decision (T-108, squad-observability).

A :class:`GroundingTrace` is a structured, side-effect-free record the server can log to **stderr**
(never stdout — NFR-005) so a grounded answer is observable in production: how many claims reached
each verdict, whether the reranker ran or fell back to RRF-only (L-002), and the gate's latency. It
carries **no** secret and **no** query text at INFO level; the query is only ever logged at DEBUG by
the caller, matching the audit-redaction rule.

The trace is derived purely from a graded :class:`~mcp_common.envelope.GroundedResult`, so it is
deterministic and adds no new decision path — it only *observes* the one the gate already made.

## Recall / τ are NOT reported here
This trace reports **counts and latency**, which are measurable now. Grounding **recall/precision**
and the FACT↔LOW_CONFIDENCE threshold τ are UNVERIFIED until a real golden-set runs with measured
embedding/rerank — blocked by HF egress (NFR-003/NFR-010, L-002). ``calibration_status`` is surfaced
so a reader never mistakes the uncalibrated gate for a measured one.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from mcp_common.envelope import GroundedResult, GroundingVerdict

__all__ = ["GroundingTrace", "trace_from_result"]


@dataclass(frozen=True)
class GroundingTrace:
    """One structured telemetry record for a grounded tool call (safe to emit to stderr).

    ``calibration_status`` is echoed from the summary so the trace is honest about τ being unset;
    ``recall`` is intentionally absent (UNVERIFIED until HF egress — see module docstring).
    """

    event: str
    fact: int
    low_confidence: int
    unknown: int
    conflict: int
    claims_total: int
    reranker: str
    calibration_status: str
    status: str
    elapsed_ms: int

    def to_json(self) -> str:
        """Compact, stable JSON for a structured stderr log line (deterministic key order)."""
        return json.dumps(asdict(self), separators=(",", ":"), sort_keys=True)


def trace_from_result(result: GroundedResult, *, event: str = "grounding") -> GroundingTrace:
    """Build a :class:`GroundingTrace` from a graded result (pure, no I/O).

    Counts are taken from the authoritative per-claim verdicts (not just the summary) so the trace
    cannot drift from what the gate actually produced.
    """
    claims = list(result.claims)
    counts = {v: 0 for v in GroundingVerdict}
    for claim in claims:
        counts[claim.grounding] += 1
    return GroundingTrace(
        event=event,
        fact=counts[GroundingVerdict.FACT],
        low_confidence=counts[GroundingVerdict.LOW_CONFIDENCE],
        unknown=counts[GroundingVerdict.UNKNOWN],
        conflict=counts[GroundingVerdict.CONFLICT],
        claims_total=len(claims),
        reranker=result.grounding_summary.reranker,
        calibration_status=result.grounding_summary.calibration_status,
        status=result.status.value,
        elapsed_ms=result.meta.elapsed_ms,
    )
