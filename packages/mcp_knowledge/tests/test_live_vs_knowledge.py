"""T-105 (E5) — Live-vs-Knowledge decision + freshness + conflict + source-authority (FR-018).

Unit tests for the four liveness pieces (intent / freshness / authority / reconcile) and
integration tests driving ``get_jira_context`` through the real MCP server so the reconciled
envelope is contract-valid. Covers TC-086 (agree), TC-087 (conflict + authority_note), TC-088
(freshness down-weight), and the §35 offline-source behaviour (live unavailable → snapshot only,
marked, never faked). Thresholds stay TBD (NFR-010): these assert *direction* and *shape*, not a τ.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from knowledge_helpers import FakeKnowledgeClient, FakeRetriever, make_candidate
from mcp.shared.memory import create_connected_server_and_client_session
from mcp_common.envelope import GroundedStatus, GroundingVerdict, SourceType
from mcp_common.errors import ErrorCode, ToolError
from mcp_knowledge.liveness.authority import (
    DEFAULT_SOURCE_AUTHORITY,
    SourceAuthorityConfig,
    load_source_authority,
)
from mcp_knowledge.liveness.freshness import (
    FRESHNESS_FLOOR,
    FRESHNESS_UNKNOWN,
    assess_freshness,
)
from mcp_knowledge.liveness.intent import LiveNeed, classify_live_need
from mcp_knowledge.liveness.reconcile import (
    LiveValue,
    ReconcileStatus,
    SnapshotValue,
    live_unavailable_warning,
    reconcile_claim,
)
from mcp_knowledge.server import build_server
from mcp_knowledge.tools.read_api import KnowledgeReadApi

NOW = datetime(2026, 10, 2, 0, 0, tzinfo=UTC)


# -- intent (spec §36/§37) -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "question,expected",
    [
        ("How does PaymentService work?", LiveNeed.KNOWLEDGE),
        ("What is the architecture of payment-service?", LiveNeed.KNOWLEDGE),
        ("What happened in PAY-123?", LiveNeed.KNOWLEDGE),
        ("Is PAY-121 still open?", LiveNeed.LIVE),
        ("Who is currently assigned to PAY-9?", LiveNeed.LIVE),
        ("what is the latest commit?", LiveNeed.LIVE),
        ("what is the status today?", LiveNeed.LIVE),
        # spec §38 mixed query: historical + current-state in one question
        ("How does payment timeout work and is PAY-121 still being worked on?", LiveNeed.MIXED),
        # Vietnamese current-state marker
        ("PAY-121 hiện tại còn mở không?", LiveNeed.LIVE),
    ],
)
def test_classify_live_need(question: str, expected: LiveNeed) -> None:
    assert classify_live_need(question) is expected


def test_classify_does_not_trip_on_substring() -> None:
    # "nowhere"/"knowledge" must not match the "now"/"latest" markers (word-boundary).
    assert classify_live_need("there is knowledge nowhere about this") is LiveNeed.KNOWLEDGE


# -- freshness (ADR-0018 §4, FR-018/AC-003) ------------------------------------------------------


def test_freshness_within_horizon_is_full_factor() -> None:
    fresh = assess_freshness(
        updated_time=NOW - timedelta(hours=5), horizon_hours=24, now=NOW
    )
    assert fresh.factor == 1.0 and fresh.is_stale is False
    assert fresh.staleness_hours == 5.0


def test_freshness_past_horizon_is_reduced_but_not_zero_or_false() -> None:
    # TC-088: a fact older than its horizon is down-weighted, never zeroed, never made false.
    stale = assess_freshness(
        updated_time=NOW - timedelta(hours=240), horizon_hours=24, now=NOW
    )
    assert stale.is_stale is True
    assert 0.0 < stale.factor < 1.0  # reduced, not zero (would be "false"), not full
    assert stale.factor >= FRESHNESS_FLOOR
    assert stale.staleness_hours == 240.0  # staleness observable


def test_freshness_floor_holds_for_ancient_fact() -> None:
    ancient = assess_freshness(
        updated_time=NOW - timedelta(days=4000), horizon_hours=24, now=NOW
    )
    assert ancient.factor == FRESHNESS_FLOOR  # clamped, never below


def test_freshness_null_horizon_never_stale() -> None:
    never = assess_freshness(
        updated_time=NOW - timedelta(days=1000), horizon_hours=None, now=NOW
    )
    assert never.is_stale is False and never.factor == 1.0


def test_freshness_unknown_age_mild_discount() -> None:
    unknown = assess_freshness(updated_time=None, horizon_hours=24, now=NOW)
    assert unknown.factor == FRESHNESS_UNKNOWN and unknown.staleness_hours is None


# -- authority config (spec §42, migration 0008; NOT hardcoded) ----------------------------------


class _ConfigTx:
    def __init__(self, authority_rows: list[dict], horizon_rows: list[dict]) -> None:
        self._a = authority_rows
        self._h = horizon_rows

    async def fetch(self, name: str, params: Any = None) -> list[dict]:
        if name == "source_authority_all":
            return list(self._a)
        if name == "freshness_horizon_all":
            return list(self._h)
        return []


@pytest.mark.asyncio
async def test_load_source_authority_from_db() -> None:
    tx = _ConfigTx(
        [{"fact_type": "current_work_status", "authoritative_source": "jira", "rationale": "live"}],
        [{"fact_type": "current_work_status", "horizon_hours": 24}],
    )
    config = await load_source_authority(tx)
    assert config.loaded_from_db is True
    assert config.authoritative_source("current_work_status") == "jira"
    assert config.horizon_for("current_work_status") == 24
    note = config.authority_note("current_work_status")
    assert note is not None and "jira" in note and "current_work_status" in note


@pytest.mark.asyncio
async def test_load_source_authority_falls_back_to_migration_defaults() -> None:
    # Config tables absent (older DB) → defaults that MIRROR migration 0008, not an error.
    class _Boom:
        async def fetch(self, name: str, params: Any = None) -> list[dict]:
            raise RuntimeError("relation does not exist")

    config = await load_source_authority(_Boom())
    assert config.loaded_from_db is False
    assert config.authority == DEFAULT_SOURCE_AUTHORITY
    assert config.authoritative_source("current_work_status") == "jira"


def test_authority_note_none_for_unknown_fact_type() -> None:
    config = SourceAuthorityConfig(
        authority={}, rationale={}, horizon_hours={}, loaded_from_db=True
    )
    assert config.authority_note("something_unmapped") is None


# -- reconcile (FR-018/AC-001..003, spec §35/§41/§42) --------------------------------------------


def _config() -> SourceAuthorityConfig:
    return SourceAuthorityConfig(
        authority=dict(DEFAULT_SOURCE_AUTHORITY),
        rationale={"current_work_status": "Live work status is authoritative in Jira."},
        horizon_hours={"current_work_status": 24},
        loaded_from_db=True,
    )


def _snapshot(value: str, *, hours_old: float = 1.0) -> SnapshotValue:
    return SnapshotValue(
        value=value,
        source=SourceType.CONFLUENCE,
        document_id="3f2504e0-4f89-11d3-9a0c-0305e82c3301",
        chunk_id="3f2504e0-4f89-11d3-9a0c-0305e82c3301#0",
        updated_time=NOW - timedelta(hours=hours_old),
        source_uri="https://wiki/x",
    )


def _live(value: str) -> LiveValue:
    return LiveValue(
        value=value,
        source=SourceType.JIRA,
        external_id="PAY-121",
        url="https://jira/browse/PAY-121",
        updated_time=NOW - timedelta(hours=1),
    )


def test_reconcile_agree_is_single_fact_with_both_provenance() -> None:
    # TC-086: snapshot and live agree → one FACT claim, provenance from BOTH sides, not a conflict.
    out = reconcile_claim(
        text="status", fact_type="current_work_status",
        snapshot=_snapshot("In Progress"), live=_live("In Progress"),
        config=_config(), now=NOW,
    )
    assert out.status is ReconcileStatus.AGREE
    assert out.claim.grounding is GroundingVerdict.FACT
    sources = {p.source for p in out.claim.provenance}
    assert sources == {SourceType.CONFLUENCE, SourceType.JIRA}
    assert out.live_available is True


def test_reconcile_conflict_exposes_both_positions_and_authority_note() -> None:
    # TC-087: snapshot vs live differ → CONFLICT, both positions w/ provenance, authority_note set.
    out = reconcile_claim(
        text="status", fact_type="current_work_status",
        snapshot=_snapshot("Done"), live=_live("In Progress"),
        config=_config(), now=NOW,
    )
    assert out.status is ReconcileStatus.CONFLICT
    assert out.claim.grounding is GroundingVerdict.CONFLICT
    values = {p.value for p in out.claim.positions}
    assert values == {"Done", "In Progress"}
    assert all(p.provenance for p in out.claim.positions)  # full provenance both sides
    assert out.claim.authority_note is not None
    assert "jira" in out.claim.authority_note and "current_work_status" in out.claim.authority_note


def test_reconcile_conflict_without_configured_authority_still_exposes_both() -> None:
    # No authority configured for the fact type → still a CONFLICT exposing both, note is None
    # (never silently chooses a winner — spec §41).
    empty = SourceAuthorityConfig(
        authority={}, rationale={}, horizon_hours={}, loaded_from_db=True
    )
    out = reconcile_claim(
        text="t", fact_type="unmapped", snapshot=_snapshot("A"), live=_live("B"),
        config=empty, now=NOW,
    )
    assert out.status is ReconcileStatus.CONFLICT
    assert out.claim.authority_note is None
    assert len(out.claim.positions) == 2


def test_reconcile_live_unavailable_is_snapshot_only_marked() -> None:
    # spec §35: live down → snapshot only, marked "live verification unavailable", NOT faked live.
    out = reconcile_claim(
        text="status", fact_type="current_work_status",
        snapshot=_snapshot("In Progress"), live=None,
        config=_config(), now=NOW, live_error="upstream_unavailable",
    )
    assert out.status is ReconcileStatus.SNAPSHOT_ONLY
    assert out.live_available is False
    assert out.claim.grounding is GroundingVerdict.LOW_CONFIDENCE  # not a fabricated FACT
    assert any("live verification unavailable" in w for w in out.warnings)


def test_reconcile_stale_snapshot_downweights_confidence_not_truth() -> None:
    # TC-088: a stale snapshot (agree) is still FACT, but confidence is reduced by freshness.
    fresh_out = reconcile_claim(
        text="t", fact_type="current_work_status",
        snapshot=_snapshot("In Progress", hours_old=1), live=_live("In Progress"),
        config=_config(), now=NOW,
    )
    stale_out = reconcile_claim(
        text="t", fact_type="current_work_status",
        snapshot=_snapshot("In Progress", hours_old=500), live=_live("In Progress"),
        config=_config(), now=NOW,
    )
    assert fresh_out.claim.confidence == 1.0
    assert stale_out.snapshot_freshness.is_stale is True
    assert stale_out.claim.confidence is not None and stale_out.claim.confidence < 1.0
    assert stale_out.claim.grounding is GroundingVerdict.FACT  # not made false by age


def test_live_unavailable_warning_shape() -> None:
    assert live_unavailable_warning() == "live verification unavailable"
    assert live_unavailable_warning("down") == "live verification unavailable: down"


# -- integration through the server (contract-valid reconciled envelope) -------------------------

pytestmark_async = pytest.mark.asyncio


class _FakeJiraPage:
    def __init__(self, issues: list[dict]) -> None:
        self.values = issues


def _issue(key: str, status: str, *, updated: str | None = None) -> dict:
    return {
        "key": key,
        "fields": {
            "status": {"name": status},
            "updated": updated or "2026-10-01T23:00:00.000+0000",
        },
        "url": f"https://jira/browse/{key}",
    }


class _JiraOk:
    def __init__(self, issues: list[dict]) -> None:
        self._issues = issues

    async def search_issues(self, jql: str, *, max_results: int) -> Any:
        return _FakeJiraPage(self._issues)


class _JiraDown:
    async def search_issues(self, jql: str, *, max_results: int) -> Any:
        raise ToolError(ErrorCode.UPSTREAM_UNAVAILABLE, "down", "jira", True)


def _api(candidates=None, *, jira=None) -> KnowledgeReadApi:
    return KnowledgeReadApi(FakeKnowledgeClient(), FakeRetriever(candidates or []), jira=jira)


async def _call(server, name: str, args: dict[str, Any]):
    async with create_connected_server_and_client_session(server) as session:
        return await session.call_tool(name, arguments=args)


@pytest.mark.asyncio
async def test_get_jira_context_conflict_surfaces_authority_note_end_to_end() -> None:
    # Snapshot says "Done" (candidate content), live Jira says "In Progress" → CONFLICT exposed.
    snap = make_candidate(content="PAY-121 status: Done", source_type="confluence")
    jira = _JiraOk([_issue("PAY-121", "In Progress")])
    res = await _call(build_server(_api([snap], jira=jira)), "get_jira_context",
                      {"subject": "PAY-121"})  # fmt: skip
    assert not res.isError, res.content
    payload = res.structuredContent
    assert payload["grounding_summary"]["conflict"] == 1
    claim = payload["claims"][0]
    assert claim["grounding"] == GroundingVerdict.CONFLICT.value
    assert claim["authority_note"] and "jira" in claim["authority_note"]
    values = {p["value"] for p in claim["positions"]}
    assert "Done" in " ".join(values) and any("In Progress" in v for v in values)


@pytest.mark.asyncio
async def test_get_jira_context_agree_is_single_fact_with_freshness() -> None:
    snap = make_candidate(content="PAY-121 status: In Progress", source_type="confluence")
    jira = _JiraOk([_issue("PAY-121", "In Progress")])
    res = await _call(build_server(_api([snap], jira=jira)), "get_jira_context",
                      {"subject": "PAY-121"})  # fmt: skip
    payload = res.structuredContent
    assert payload["status"] == GroundedStatus.OK.value
    assert payload["grounding_summary"]["fact"] == 1
    assert payload["meta"]["data_freshness"] is not None


@pytest.mark.asyncio
async def test_get_jira_context_live_down_is_snapshot_only_marked() -> None:
    # spec §35: Jira down → snapshot only, warning present, nothing pretends realtime.
    snap = make_candidate(content="PAY-121 status: Done", source_type="confluence")
    res = await _call(build_server(_api([snap], jira=_JiraDown())), "get_jira_context",
                      {"subject": "PAY-121"})  # fmt: skip
    payload = res.structuredContent
    assert any("live verification unavailable" in w for w in payload["meta"]["warnings"])
    assert payload["claims"][0]["grounding"] != GroundingVerdict.FACT.value


@pytest.mark.asyncio
async def test_get_jira_context_no_snapshot_is_insufficient_evidence() -> None:
    res = await _call(build_server(_api([], jira=_JiraOk([_issue("PAY-1", "Open")]))),
                      "get_jira_context", {"subject": "nothing-indexed"})  # fmt: skip
    payload = res.structuredContent
    assert payload["status"] == GroundedStatus.INSUFFICIENT_EVIDENCE.value
