"""T-093: Jira connector over the real `mcp_jira.client` with a mocked HTTP layer.

AC: FR-017/AC-004, FR-012 (ADR-0019 / ADR-0012 / ADR-0016; BR-005). Fixtures are hand-written to
the shape of the Jira REST API (no live tenant reachable, spike S1); `@live` tests in mcp_jira
cover the real shape. Both flavors (Cloud `/rest/api/3`, Server/DC `/rest/api/2`) are exercised.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import respx
from mcp_common.config import CommonSettings
from mcp_ingest.connectors import jira as module
from mcp_ingest.connectors.base import Cursor
from mcp_ingest.connectors.jira import JiraConnector
from mcp_ingest.settings import Settings
from mcp_jira.client import ALLOWED_OPERATIONS, JiraClient
from mcp_jira.settings import Settings as JiraSettings
from pydantic import SecretStr

CLOUD_BASE = "https://acme.atlassian.net"
SERVER_BASE = "https://jira.acme.example"


def _search_url(base: str, flavor: str) -> str:
    version = "3" if flavor == "cloud" else "2"
    return f"{base}/rest/api/{version}/search"


def _issue_url(base: str, flavor: str, key: str) -> str:
    version = "3" if flavor == "cloud" else "2"
    return f"{base}/rest/api/{version}/issue/{key}"


def issue(
    key: str = "PAY-1234",
    *,
    project: str = "PAY",
    updated: str = "2026-09-30T10:00:00.000+0000",
    summary: str = "Retry worker fails",
    description: Any = "Three attempts then dead-letter.",
    security: str | None = None,
) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "summary": summary,
        "updated": updated,
        "status": {"name": "In Progress"},
        "issuetype": {"name": "Bug"},
        "assignee": {"displayName": "An Nguyen"},
        "project": {"key": project},
        "description": description,
    }
    if security is not None:
        fields["security"] = {"name": security}
    return {"key": key, "fields": fields}


def _client(flavor: str) -> JiraClient:
    if flavor == "cloud":
        settings = JiraSettings(
            base_url=CLOUD_BASE, flavor="cloud", email="svc@acme.test",
            token=SecretStr("not-real"),
        )  # fmt: skip
    else:
        settings = JiraSettings(
            base_url=SERVER_BASE, flavor="server", token=SecretStr("not-real")
        )
    return JiraClient(settings, common=CommonSettings(http_backoff_base=0.0))


@pytest.fixture(params=["cloud", "server"])
def flavor(request: pytest.FixtureRequest) -> str:
    return request.param


@pytest.fixture
def base(flavor: str) -> str:
    return CLOUD_BASE if flavor == "cloud" else SERVER_BASE


@pytest.fixture
def router():
    with respx.mock(assert_all_called=False) as mock:
        yield mock


@pytest.fixture
def connector(flavor: str, router) -> JiraConnector:
    conn = JiraConnector(_client(flavor), projects=["PAY", "OPS"], team_projects=["PAY", "OPS"])
    yield conn
    conn.close()


def serve_issues(router, base: str, flavor: str, issues: list[dict[str, Any]]) -> respx.Route:
    """Mock the flavored `/search` with the correct pagination style."""

    def handler(request: httpx.Request) -> httpx.Response:
        start = int(request.url.params.get("startAt", "0"))
        page_size = int(request.url.params.get("maxResults", "100"))
        chunk = issues[start : start + page_size]
        body: dict[str, Any] = {"issues": chunk, "total": len(issues)}
        if flavor == "cloud":
            # Cloud: emit a nextPageToken while more remain (encodes the next offset for the mock).
            if start + page_size < len(issues):
                body["nextPageToken"] = str(start + page_size)
            token = request.url.params.get("nextPageToken")
            if token:
                start = int(token)
                chunk = issues[start : start + page_size]
                body["issues"] = chunk
                if start + page_size < len(issues):
                    body["nextPageToken"] = str(start + page_size)
                else:
                    body.pop("nextPageToken", None)
        return httpx.Response(200, json=body)

    return router.get(_search_url(base, flavor)).mock(side_effect=handler)


def test_FR_012_crawl_yields_documents_with_full_citation_metadata(
    router, connector, base, flavor
) -> None:
    serve_issues(router, base, flavor, [issue()])
    (document,) = list(connector.iter_documents(None, "full"))
    assert document.source_type == "jira" and document.source_id == "PAY-1234"
    assert document.source_uri == f"{base}/browse/PAY-1234"  # the original URL (citation)
    assert document.title == "Retry worker fails" and document.container == "PAY"
    assert document.author == "An Nguyen" and document.source_format == "markdown"
    assert document.source_updated_at == datetime(2026, 9, 30, 10, 0, tzinfo=UTC)
    assert "Three attempts" in document.raw_content and document.visibility == "team"


def test_FR_017_AC_004_visibility_is_default_deny(router, connector, base, flavor) -> None:
    issues = [
        issue("PAY-1"),                               # declared team project, no security => team
        issue("PAY-2", security="Developers"),        # issue-level security level => restricted
        issue("HR-9", project="HR"),                  # project not declared team-wide
    ]
    serve_issues(router, base, flavor, issues)
    labels = {d.source_id: d.visibility for d in connector.iter_documents(None, "full")}
    assert labels == {"PAY-1": "team", "PAY-2": "restricted", "HR-9": "restricted"}


def test_cloud_adf_description_is_flattened(router) -> None:
    adf = {
        "type": "doc",
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": "Hello"}]},
            {"type": "paragraph", "content": [{"type": "text", "text": "world"}]},
        ],
    }
    router_ctx = respx.mock(assert_all_called=False)
    with router_ctx as router:
        conn = JiraConnector(_client("cloud"), projects=["PAY"], team_projects=["PAY"])
        serve_issues(router, CLOUD_BASE, "cloud", [issue(description=adf)])
        (document,) = list(conn.iter_documents(None, "full"))
        conn.close()
    assert "Hello" in document.raw_content and "world" in document.raw_content


def test_incremental_jql_watermark_boundary_is_inclusive(router, connector, base, flavor) -> None:
    on_boundary = issue("PAY-1", updated="2026-09-30T10:00:00.000+0000")
    older = issue("PAY-2", updated="2026-09-30T09:59:59.000+0000")
    newer = issue("PAY-3", updated="2026-09-30T10:00:01.000+0000")
    route = serve_issues(router, base, flavor, [older, on_boundary, newer])
    cursor = Cursor(datetime(2026, 9, 30, 10, 0, tzinfo=UTC))

    ids = [d.source_id for d in connector.iter_documents(cursor, "incremental")]
    assert ids == ["PAY-1", "PAY-3"], ">= the watermark: boundary included, older excluded"

    jql = route.calls.last.request.url.params["jql"]
    assert 'project in ("PAY", "OPS")' in jql
    assert 'updated >= "2026-09-30 09:59"' in jql  # widened by the one-minute skew guard
    assert jql.endswith("ORDER BY updated ASC")

    full = [d.source_id for d in connector.iter_documents(cursor, "full")]
    assert full == ["PAY-2", "PAY-1", "PAY-3"]
    assert "updated >=" not in route.calls.last.request.url.params["jql"]


def test_pagination_walks_every_page_and_limit_stops_early(router, connector, base, flavor) -> None:
    issues = [issue(f"PAY-{i}") for i in range(1, 250)]
    serve_issues(router, base, flavor, issues)
    assert len(list(connector.iter_documents(None, "full"))) == 249
    serve_issues(router, base, flavor, issues)
    assert len(list(connector.iter_documents(None, "full", limit=3))) == 3


def test_fetch_documents_skips_issues_deleted_at_the_source(
    router, connector, base, flavor
) -> None:
    router.get(_issue_url(base, flavor, "PAY-10")).mock(
        return_value=httpx.Response(200, json=issue("PAY-10"))
    )
    router.get(_issue_url(base, flavor, "PAY-11")).mock(return_value=httpx.Response(404, json={}))
    assert [d.source_id for d in connector.fetch_documents(["PAY-10", "PAY-11"])] == ["PAY-10"]


def test_only_allowlisted_get_operations_are_used(router, connector, base, flavor) -> None:
    serve_issues(router, base, flavor, [issue()])
    list(connector.iter_documents(None, "full"))
    assert {call.request.method for call in router.calls} == {"GET"}
    assert set(JiraConnector.operations_used) <= set(ALLOWED_OPERATIONS)


def test_connector_does_not_import_read_api() -> None:
    import ast
    from pathlib import Path

    import mcp_ingest.connectors.jira as jira_mod

    tree = ast.parse(Path(jira_mod.__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert not any("read_api" in m for m in imported)
    assert "mcp_jira.client" in imported


def test_a_bad_issue_key_is_rejected_by_identity_rules(router, connector, base, flavor) -> None:
    serve_issues(router, base, flavor, [issue(key="not-a-key")])
    with pytest.raises(ValueError, match="issue key"):
        list(connector.iter_documents(None, "full"))


def test_status_and_build_follow_the_environment(monkeypatch) -> None:
    for name in ("MCP_JIRA_BASE_URL", "MCP_JIRA_EMAIL", "MCP_JIRA_TOKEN", "MCP_JIRA_FLAVOR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("MCP_INGEST_JIRA_PROJECTS", raising=False)
    monkeypatch.delenv("MCP_INGEST_JIRA_TEAM_PROJECTS", raising=False)
    status = module.status(Settings())
    assert status.enabled and not status.configured
    assert "MCP_INGEST_JIRA_PROJECTS" in status.missing_env

    monkeypatch.setenv("MCP_JIRA_BASE_URL", SERVER_BASE)
    monkeypatch.setenv("MCP_JIRA_FLAVOR", "server")
    monkeypatch.setenv("MCP_JIRA_TOKEN", "not-real")
    monkeypatch.setenv("MCP_INGEST_JIRA_PROJECTS", "PAY,OPS")
    monkeypatch.setenv("MCP_INGEST_JIRA_TEAM_PROJECTS", "PAY")
    assert module.status(Settings()).configured
    built = module.build(Settings())
    try:
        assert built.status().configured and built.source_type == "jira"
    finally:
        built.close()
