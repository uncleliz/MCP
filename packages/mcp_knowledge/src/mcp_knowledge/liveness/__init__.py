"""CHG-001 E5 (T-105) — Live-vs-Knowledge decision, freshness, conflict + source-authority.

This package holds the four pieces the plan (T-105) and architecture ("Live-vs-Knowledge &
freshness", spec §35/§36/§37/§41/§42, ADR-0018 §4) ask for, kept *separate* from the grounding
gate (E8) and the permission filter (E6) so the choke points stay single:

* :mod:`mcp_knowledge.liveness.intent` — the knowledge-vs-live decision (does this question need a
  live check at all, or is the local snapshot enough, or both — spec §36/§37).
* :mod:`mcp_knowledge.liveness.freshness` — the freshness factor: a fact older than its configured
  horizon is *down-weighted*, never made false (ADR-0018 §4, FR-018/AC-003).
* :mod:`mcp_knowledge.liveness.authority` — the source-authority config loaded from the DB
  (migration 0008; NOT hardcoded in any prompt — spec §42, FR-018/AC-002) with safe defaults so
  the Live path still works when the config tables are absent.
* :mod:`mcp_knowledge.liveness.reconcile` — reconcile a knowledge snapshot value against a live
  value: agree ⇒ one claim with both-side provenance; differ ⇒ a prepared ``CONFLICT`` with both
  positions and an ``authority_note``; live unavailable ⇒ snapshot only, marked "live verification
  unavailable" (spec §35 — never pretend realtime).
"""

from __future__ import annotations

from mcp_knowledge.liveness.authority import (
    DEFAULT_SOURCE_AUTHORITY,
    SourceAuthorityConfig,
    load_source_authority,
)
from mcp_knowledge.liveness.freshness import FreshnessAssessment, assess_freshness
from mcp_knowledge.liveness.intent import LiveNeed, classify_live_need
from mcp_knowledge.liveness.reconcile import (
    JiraLiveProvider,
    LiveValue,
    ReconcileOutcome,
    ReconcileStatus,
    SnapshotValue,
    reconcile_claim,
)

__all__ = [
    "DEFAULT_SOURCE_AUTHORITY",
    "FreshnessAssessment",
    "JiraLiveProvider",
    "LiveNeed",
    "LiveValue",
    "ReconcileOutcome",
    "ReconcileStatus",
    "SnapshotValue",
    "SourceAuthorityConfig",
    "assess_freshness",
    "classify_live_need",
    "load_source_authority",
    "reconcile_claim",
]
