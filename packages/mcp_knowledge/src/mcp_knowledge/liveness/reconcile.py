"""Reconcile a knowledge snapshot against a live source (ADR-0018 §4, spec §35/§41/§42, FR-018).

This is the heart of E5. Given a claim that has a value from the local **snapshot** (knowledge) and
a value from a **live** source (Jira), it decides:

* **agree** (same value) ⇒ one grounded claim carrying provenance from **both** sides, with a
  ``data_freshness``/staleness indicator — not a conflict (FR-018/AC-001);
* **differ** ⇒ a ``CONFLICT`` claim exposing **both** positions with full provenance and an
  ``authority_note`` derived from the configured source authority — nothing silently merged or
  chosen (FR-018/AC-002, spec §41/§42);
* **live unavailable** (Jira down / not configured) ⇒ the snapshot value alone, marked "live
  verification unavailable" — never pretending the snapshot is realtime (spec §35).

Freshness (``mcp_knowledge.liveness.freshness``) down-weights a stale side's confidence without
making it false (FR-018/AC-003). The authority config (``mcp_knowledge.liveness.authority``) is
read from the DB, never hardcoded in a prompt (spec §42).

The reconciler produces the *graded* :class:`mcp_common.envelope.Claim` directly for the live-vs-
knowledge case, because the live-vs-knowledge comparison is exactly the one place a verdict is
decided by *values agreeing/differing* rather than by the retrieval-only gate (E8). The single
grounding gate (E8) still owns the no-evidence⇒UNKNOWN / source⇒FACT path for the snapshot-only
claims; this module only closes the extra CONFLICT/agreement decision that needs the live side.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol

from mcp_common.envelope import (
    Claim,
    ClaimEvidence,
    ClaimPosition,
    ClaimProvenance,
    GroundingVerdict,
    SourceType,
)

from mcp_knowledge.liveness.authority import SourceAuthorityConfig
from mcp_knowledge.liveness.freshness import FreshnessAssessment, assess_freshness

__all__ = [
    "JiraLiveProvider",
    "LiveValue",
    "ReconcileOutcome",
    "ReconcileStatus",
    "SnapshotValue",
    "live_unavailable_warning",
    "reconcile_claim",
]

#: The fixed marker that goes on an answer served from the snapshot when the live check could not
#: run (spec §35: "Do not pretend the local snapshot is realtime").
LIVE_UNAVAILABLE_MARKER = "live verification unavailable"


class ReconcileStatus(StrEnum):
    """How snapshot and live compared."""

    AGREE = "agree"
    CONFLICT = "conflict"
    SNAPSHOT_ONLY = "snapshot_only"


@dataclass(frozen=True)
class SnapshotValue:
    """A claim value from the local knowledge snapshot, with its provenance fields."""

    value: str
    source: SourceType
    document_id: str
    chunk_id: str
    updated_time: datetime | None = None
    source_version: str | None = None
    source_uri: str | None = None
    synced_at: datetime | None = None


@dataclass(frozen=True)
class LiveValue:
    """A claim value fetched live (e.g. a Jira issue field), with its provenance fields."""

    value: str
    source: SourceType
    external_id: str
    url: str | None = None
    updated_time: datetime | None = None
    source_version: str | None = None
    fetched_at: datetime | None = None


@dataclass(frozen=True)
class ReconcileOutcome:
    """The reconciled claim plus the metadata the caller surfaces in ``meta``."""

    status: ReconcileStatus
    claim: Claim
    snapshot_freshness: FreshnessAssessment
    live_available: bool
    warnings: list[str] = field(default_factory=list)
    authority_note: str | None = None


class JiraLiveProvider(Protocol):
    """The read-only live-Jira capability the reconciler needs (ADR-0019).

    Satisfied structurally by :class:`mcp_jira.client.JiraClient` — no import dependency between the
    packages. The reconciler only ever *reads* (``search_issues`` is a GET-only JQL search); there
    is no write path. A missing/failing provider degrades to snapshot-only, never an error.
    """

    async def search_issues(self, jql: str, *, max_results: int) -> Any: ...


def live_unavailable_warning(reason: str | None = None) -> str:
    """The spec-§35 warning string for a snapshot-only answer."""
    return LIVE_UNAVAILABLE_MARKER if not reason else f"{LIVE_UNAVAILABLE_MARKER}: {reason}"


def _norm(value: str) -> str:
    return " ".join(value.strip().casefold().split())


def _snapshot_provenance(snap: SnapshotValue, *, confidence: float) -> ClaimProvenance:
    return ClaimProvenance(
        source=snap.source,
        updated_time=snap.updated_time or datetime(1970, 1, 1, tzinfo=UTC),
        evidence=ClaimEvidence(
            document_id=snap.document_id,
            chunk_id=snap.chunk_id,
            url=snap.source_uri,
            source_uri=snap.source_uri,
        ),
        source_version=snap.source_version,
        synced_at=snap.synced_at,
        confidence=round(min(1.0, max(0.0, confidence)), 6),
    )


def _live_provenance(live: LiveValue, *, confidence: float) -> ClaimProvenance:
    # The live side's "document" is the live record itself (e.g. the Jira issue key); its evidence
    # is the issue, resolvable via its URL. updated_time is the live record's last-updated. A live
    # record is current by definition, so its provenance confidence is the full 1.0 unless capped.
    return ClaimProvenance(
        source=live.source,
        updated_time=live.updated_time or datetime(1970, 1, 1, tzinfo=UTC),
        evidence=ClaimEvidence(
            document_id=live.external_id,
            chunk_id=live.external_id,
            url=live.url,
            source_uri=live.url,
        ),
        source_version=live.source_version,
        synced_at=live.fetched_at,
        confidence=round(min(1.0, max(0.0, confidence)), 6),
    )


def reconcile_claim(
    *,
    text: str,
    fact_type: str,
    snapshot: SnapshotValue,
    live: LiveValue | None,
    config: SourceAuthorityConfig,
    now: datetime | None = None,
    live_error: str | None = None,
) -> ReconcileOutcome:
    """Reconcile a snapshot value against a (possibly absent) live value for one claim.

    * ``live is None`` ⇒ snapshot-only, marked "live verification unavailable" (spec §35). The
      claim is **not** a CONFLICT and is **not** FACT here — the snapshot still flows through the
      E8 grounding gate for its FACT/UNKNOWN verdict; this outcome only records that the live check
      did not run so ``get_jira_context`` can warn honestly.
    * values **agree** ⇒ a FACT claim with provenance from both sides (FR-018/AC-001).
    * values **differ** ⇒ a CONFLICT claim with both positions + ``authority_note`` (FR-018/AC-002).

    Freshness down-weights the snapshot side's confidence when it is past its horizon; it never
    makes the claim false (FR-018/AC-003).
    """
    now = now or datetime.now(UTC)
    horizon = config.horizon_for(fact_type)
    snap_fresh = assess_freshness(
        updated_time=snapshot.updated_time, horizon_hours=horizon, now=now
    )

    if live is None:
        warnings = [live_unavailable_warning(live_error)]
        # Snapshot-only: surface the snapshot value as a LOW_CONFIDENCE claim (it has evidence, but
        # we could not verify it is current). confidence carries the freshness down-weight; it is
        # never promoted to FACT without a live confirmation here (E8 owns snapshot-only FACT).
        claim = Claim(
            text=text[:4096],
            grounding=GroundingVerdict.LOW_CONFIDENCE,
            confidence=round(snap_fresh.factor, 6),
            confidence_basis=(
                "evidence-strength (retrieval×agreement×freshness), NOT P(claim true)"
            ),
            provenance=[_snapshot_provenance(snapshot, confidence=snap_fresh.factor)],
        )
        return ReconcileOutcome(
            status=ReconcileStatus.SNAPSHOT_ONLY,
            claim=claim,
            snapshot_freshness=snap_fresh,
            live_available=False,
            warnings=warnings,
        )

    if _norm(snapshot.value) == _norm(live.value):
        # Agree: one claim, provenance from both sides, freshness-weighted confidence (0..1).
        confidence = round(min(1.0, snap_fresh.factor), 6)
        claim = Claim(
            text=text[:4096],
            grounding=GroundingVerdict.FACT,
            confidence=confidence,
            confidence_basis=(
                "evidence-strength (retrieval×agreement×freshness), NOT P(claim true)"
            ),
            provenance=[
                _snapshot_provenance(snapshot, confidence=snap_fresh.factor),
                _live_provenance(live, confidence=1.0),
            ],
        )
        return ReconcileOutcome(
            status=ReconcileStatus.AGREE,
            claim=claim,
            snapshot_freshness=snap_fresh,
            live_available=True,
        )

    # Differ: CONFLICT, expose BOTH positions with full provenance + the configured authority_note.
    note = config.authority_note(fact_type)
    claim = Claim(
        text=text[:4096],
        grounding=GroundingVerdict.CONFLICT,
        positions=[
            ClaimPosition(
                value=snapshot.value[:2048],
                provenance=[_snapshot_provenance(snapshot, confidence=snap_fresh.factor)],
            ),
            ClaimPosition(
                value=live.value[:2048],
                provenance=[_live_provenance(live, confidence=1.0)],
            ),
        ],
        authority_note=note,
    )
    return ReconcileOutcome(
        status=ReconcileStatus.CONFLICT,
        claim=claim,
        snapshot_freshness=snap_fresh,
        live_available=True,
        authority_note=note,
    )
