"""T-079: `status` + `sources` (freshness, NFR-004).

AC: FR-012/AC-002. Numbers only: the freshness bound is an open PO question (# THRESHOLD TBD).
"""

from __future__ import annotations

import json
from datetime import timedelta

import psycopg
from ingest_helpers import FakeConnector, assert_valid, doc, q, run, scalar
from mcp_ingest.commands.sources import list_sources
from mcp_ingest.commands.status import failure_counts, gather_status
from mcp_ingest.settings import Settings


def test_NFR_004_staleness_is_computed_from_last_success(rw_dsn: str) -> None:
    run(rw_dsn, {"confluence": FakeConnector("confluence", [doc("1"), doc("2", minutes=1)])})
    with psycopg.connect(rw_dsn, autocommit=True) as conn:
        last = conn.execute("SELECT last_success_at FROM kb.ingest_source_state").fetchone()[0]
        report = gather_status(conn, now=last + timedelta(hours=26, minutes=54))
    (row,) = [r for r in report.sources if r.source_type == "confluence"]
    assert row.staleness_hours == 26.9
    assert (row.document_count, row.last_run_status) == (2, "success") and row.chunk_count >= 2
    assert row.last_success_at == last and row.last_run_at is not None


def test_status_json_validates_against_the_contract(rw_dsn: str) -> None:
    run(rw_dsn, {"confluence": FakeConnector("confluence", [doc("1")])})
    with psycopg.connect(rw_dsn, autocommit=True) as conn:
        report = gather_status(conn, known=["confluence", "gitlab", "opensearch"])
    payload = json.loads(report.model_dump_json())
    assert_valid(payload, operation_id="ingest_status")
    names = [r["source_type"] for r in payload["sources"]]
    assert names == ["confluence", "gitlab", "opensearch"]
    never = next(r for r in payload["sources"] if r["source_type"] == "gitlab")
    assert never["last_success_at"] is None and never["staleness_hours"] is None
    assert never["document_count"] == 0 and never["last_run_status"] is None


def test_status_counts_only_live_documents_and_their_chunks(rw_dsn: str) -> None:
    run(rw_dsn, {"confluence": FakeConnector("confluence", [doc("1"), doc("2", minutes=1)])})
    q(
        rw_dsn,
        "DELETE FROM kb.chunks WHERE document_id = (SELECT id FROM kb.documents WHERE source_id = '2')",  # noqa: E501
    )
    q(rw_dsn, "UPDATE kb.documents SET deleted_at = now() WHERE source_id = '2'")
    with psycopg.connect(rw_dsn, autocommit=True) as conn:
        (row,) = gather_status(conn, source="confluence").sources
    assert row.document_count == 1
    assert row.chunk_count == scalar(rw_dsn, "SELECT count(*) FROM kb.chunks")


def test_status_reports_failures_and_the_latest_run_status(rw_dsn: str) -> None:
    from ingest_helpers import PoisonProvider, make_ctx

    run(rw_dsn, {"confluence": FakeConnector("confluence", [doc("1", content="<p>POISON</p>")])},
        ctx=make_ctx(rw_dsn, prov=PoisonProvider(), max_retries=0))  # fmt: skip
    with psycopg.connect(rw_dsn, autocommit=True) as conn:
        (row,) = gather_status(conn, source="confluence").sources
        assert failure_counts(conn) == {"confluence": 1}
    assert row.last_run_status == "partial"


def test_FR_012_AC_002_sources_lists_every_connector_with_missing_env(monkeypatch) -> None:
    for name in ("MCP_CONFLUENCE_BASE_URL", "MCP_GITLAB_BASE_URL", "MCP_INGEST_OPENSEARCH_INDICES"):
        monkeypatch.delenv(name, raising=False)
    report = list_sources(Settings())
    payload = json.loads(report.model_dump_json())
    assert_valid(payload, operation_id="ingest_sources")
    rows = {r["source_type"]: r for r in payload["connectors"]}
    assert set(rows) == {"confluence", "gitlab", "opensearch", "jira"}
    assert rows["opensearch"]["enabled"] is False  # ADR-0012 A5: off by default
    assert "MCP_INGEST_OPENSEARCH_INDICES" in rows["opensearch"]["missing_env"]
    assert rows["confluence"]["configured"] is False
    assert "MCP_CONFLUENCE_API_TOKEN" in rows["confluence"]["missing_env"]
    assert rows["gitlab"]["connector"] == "GitLabConnector"
    assert rows["jira"]["connector"] == "JiraConnector"


def test_sources_reports_configured_when_everything_is_set(monkeypatch) -> None:
    monkeypatch.setenv("MCP_CONFLUENCE_BASE_URL", "https://x.atlassian.net/wiki")
    monkeypatch.setenv("MCP_CONFLUENCE_EMAIL", "svc@x.test")
    monkeypatch.setenv("MCP_CONFLUENCE_API_TOKEN", "t")
    monkeypatch.setenv("MCP_INGEST_CONFLUENCE_TEAM_SPACES", "PAY, OPS")
    monkeypatch.setenv("MCP_OPENSEARCH_HOSTS", "https://os:9200")
    monkeypatch.setenv("MCP_INGEST_OPENSEARCH_INDICES", "postmortems")
    rows = {r.source_type: r for r in list_sources(Settings()).connectors}
    assert rows["confluence"].configured and rows["confluence"].missing_env == []
    assert rows["opensearch"].enabled and rows["opensearch"].configured
