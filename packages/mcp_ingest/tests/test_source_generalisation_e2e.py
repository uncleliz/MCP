"""T-116 / TC-122, TC-123 — the same doctor→run→status→verify shape generalised to
GitLab / OpenSearch / Jira, each read-only + egress-guarded, with NO per-connector relaxation.

Confluence is the reference end-to-end (test_confluence_cloud_e2e.py). This proves the other three
ingestable connectors run the identical pull→ingest→status→verify path through the one egress
guard, on fixtures + a fake token each:

* GitLab  — repository README ingested; citation resolves to the GitLab blob URL (TC-122/123).
* Jira    — a declared team-project issue ingested; citation resolves to the Jira issue URL.
* OpenSearch — OFF by default (ADR-0012 A5): no allow-listed index ⇒ the connector is disabled and
  crawls nothing; an allow-listed index ingests one document.

Invariants re-asserted for every source (no connector-specific relaxation):
* the pull issues only GET/HEAD to the source (read-only-to-source);
* `build()` enables the egress guard (TC-112 covers the structural no-bypass);
* an unconfigured host stays default-deny (the egress allow-list governs the pull path).

DB-backed arms skip (with a reason) where no local Postgres exists — same as the other pipeline
integration tests; the Confluence reference and these share the one real pipeline.
"""

from __future__ import annotations

import base64
import re
from datetime import UTC, datetime

import httpx
import pytest
import respx
from ingest_helpers import make_ctx, provider, scalar
from mcp_common.config import CommonSettings
from mcp_common.egress import EgressDenied
from mcp_gitlab.client import GitLabClient
from mcp_gitlab.settings import Settings as GitLabSettings
from mcp_ingest.commands.status import gather_status
from mcp_ingest.connectors import gitlab as gitlab_module
from mcp_ingest.connectors import opensearch as opensearch_module
from mcp_ingest.connectors.gitlab import GitLabConnector
from mcp_ingest.connectors.jira import JiraConnector
from mcp_ingest.connectors.opensearch import OpenSearchConnector
from mcp_ingest.pipeline.run import RunOptions, run_ingest
from mcp_jira.client import JiraClient
from mcp_jira.settings import Settings as JiraSettings
from pydantic import SecretStr

FAKE_TOKEN = "fake-ro-token-not-a-real-secret"

GITLAB_BASE = "https://gitlab.tnex.test"
GITLAB_API = f"{GITLAB_BASE}/api/v4"
GITLAB_URI = f"{GITLAB_BASE}/payments/worker/-/blob/main/README.md"

JIRA_BASE = "https://tnexwm.atlassian.net"
JIRA_SEARCH = f"{JIRA_BASE}/rest/api/3/search"
JIRA_URI = f"{JIRA_BASE}/browse/PAY-1234"


@pytest.fixture
def rw_dsn(request: pytest.FixtureRequest) -> str:
    """`mcp_ingest_rw` DSN on a fresh migrated database — local-binary `pg_server` first, else the
    dev pgvector container (`docker_pg_factory`), so the real-DB arms run on CI and on a dev box."""
    try:
        local = request.getfixturevalue("migrated_db")
        from ingest_helpers import as_user

        return as_user(local, "mcp_ingest_rw")
    except pytest.skip.Exception:
        pass
    admin = request.getfixturevalue("docker_pg_factory")()  # skips itself if no container
    import psycopg
    from mcp_ingest.db import upgrade
    from psycopg import sql

    with psycopg.connect(admin, autocommit=True) as conn:
        upgrade(conn)
        conn.execute(sql.SQL("ALTER ROLE mcp_ingest_rw PASSWORD {}").format(sql.Literal("pw")))
        conn.execute(sql.SQL("ALTER ROLE mcp_query_ro PASSWORD {}").format(sql.Literal("pw")))
    scheme, _, rest = admin.partition("://")
    _, _, hostpart = rest.partition("@")
    return f"{scheme}://mcp_ingest_rw:pw@{hostpart}"


def _run(rw_dsn: str, source: str, connector, *, mode: str = "full"):
    opts = RunOptions(sources=[source], mode=mode)  # type: ignore[arg-type]
    return run_ingest(
        make_ctx(rw_dsn),
        opts,
        describe=lambda _n: connector.status(),
        build=lambda _n: connector,
        explicit_source=True,
    )


def _status_row(rw_dsn: str, source: str):
    import psycopg

    with psycopg.connect(rw_dsn, autocommit=True) as conn:
        report = gather_status(conn, source=source, known=[source])
    (row,) = report.sources
    return row


def _search_uri(rw_dsn: str, query: str) -> str:
    import asyncio

    from mcp_pgvector.client import PgVectorClient
    from mcp_pgvector.read_api import PgVectorReadApi
    from mcp_pgvector.settings import Settings as PgSettings

    ro = rw_dsn.replace("mcp_ingest_rw:", "mcp_query_ro:").replace(
        "mcp_ingest_rw@", "mcp_query_ro@"
    )
    settings = PgSettings(dsn=SecretStr(ro))
    api = PgVectorReadApi(
        PgVectorClient(settings, common=CommonSettings()),
        provider(),
        CommonSettings(),
        settings,
        now=lambda: datetime.now(UTC),
    )

    async def go() -> str:
        outcome = await api.semantic_search(query=query, top_k=5)
        assert outcome.result.status.value == "ok"
        return outcome.result.items[0]["source_uri"]

    return asyncio.run(go())


# == GitLab ===================================================================================


def _gitlab_connector() -> GitLabConnector:
    client = GitLabClient(
        GitLabSettings(base_url=GITLAB_BASE, private_token=SecretStr(FAKE_TOKEN)),
        enforce_egress=True,
    )
    return GitLabConnector(
        client,
        projects=["payments/worker"],
        team_projects=["payments"],
        internal_is_team=True,
        file_globs=["*.md"],
        include_mrs_issues=False,  # the README blob is enough to prove the ingest/verify shape
    )


def _serve_gitlab(router: respx.MockRouter) -> None:
    data = {
        "id": 42,
        "path_with_namespace": "payments/worker",
        "web_url": f"{GITLAB_BASE}/payments/worker",
        "visibility": "internal",
        "default_branch": "main",
        "last_activity_at": "2026-09-30T10:00:00.000Z",
        "repository_access_level": "enabled",
        "issues_access_level": "disabled",
        "merge_requests_access_level": "disabled",
        "permissions": {"project_access": {"access_level": 30}},
    }
    router.get(f"{GITLAB_API}/projects/42").mock(return_value=httpx.Response(200, json=data))
    # The connector resolves the project by its configured PATH first (url-encoded), then uses the
    # numeric id for the tree/file calls.
    router.get(url__regex=rf"{re.escape(GITLAB_API)}/projects/payments%2[Ff]worker$").mock(
        return_value=httpx.Response(200, json=data)
    )
    router.get(f"{GITLAB_API}/projects/42/repository/tree").mock(
        return_value=httpx.Response(200, json=[{"type": "blob", "path": "README.md"}])
    )
    body = "# Worker\n\nDeploy the payment worker with the release pipeline tag and runbook."
    payload = {
        "encoding": "base64", "content": base64.b64encode(body.encode()).decode(),
        "size": len(body), "blob_id": "abc",
    }  # fmt: skip
    router.get(url__regex=rf"{re.escape(GITLAB_API)}/projects/42/repository/files/.+").mock(
        return_value=httpx.Response(200, json=payload)
    )


def test_TC122_gitlab_runs_the_same_shape_read_only_through_the_guard(
    rw_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MCP_EGRESS_ALLOWLIST", "gitlab.tnex.test")
    connector = _gitlab_connector()
    try:
        with respx.mock(assert_all_called=False) as router:
            _serve_gitlab(router)
            report = _run(rw_dsn, "gitlab", connector)
            methods = {call.request.method for call in router.calls}
            assert methods <= {"GET", "HEAD"}, methods
    finally:
        connector.close()

    assert report.status in ("success", "partial")
    row = _status_row(rw_dsn, "gitlab")
    assert row.document_count > 0 and row.chunk_count > 0
    assert scalar(rw_dsn, "SELECT count(*) FROM kb.documents WHERE source_type='gitlab'") >= 1
    expected_query = "deploy the payment worker with the release pipeline tag"
    assert _search_uri(rw_dsn, expected_query) == GITLAB_URI


# == Jira =====================================================================================


def _jira_connector() -> JiraConnector:
    client = JiraClient(
        JiraSettings(
            base_url=JIRA_BASE, flavor="cloud", email="svc@tnex.test", token=SecretStr(FAKE_TOKEN)
        ),
        enforce_egress=True,
    )
    return JiraConnector(client, projects=["PAY"], team_projects=["PAY"])


def _serve_jira(router: respx.MockRouter) -> None:
    issue = {
        "key": "PAY-1234",
        "fields": {
            "summary": "Payment retry worker tuning",
            "updated": "2026-09-30T10:00:00.000+0000",
            "status": {"name": "Done"}, "issuetype": {"name": "Task"},
            "assignee": {"displayName": "An Nguyen"}, "project": {"key": "PAY"},
            "description": "Tune the payment retry worker backoff and dead-letter thresholds.",
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        # Jira Cloud: single page of `issues`, no `nextPageToken` ⇒ no next page.
        if request.url.params.get("nextPageToken"):
            return httpx.Response(200, json={"issues": [], "total": 1})
        return httpx.Response(200, json={"issues": [issue], "maxResults": 100, "total": 1})

    router.get(url__startswith=JIRA_SEARCH).mock(side_effect=handler)


def test_TC122_jira_runs_the_same_shape_read_only_through_the_guard(
    rw_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MCP_EGRESS_ALLOWLIST", "*.atlassian.net")
    connector = _jira_connector()
    try:
        with respx.mock(assert_all_called=False) as router:
            _serve_jira(router)
            report = _run(rw_dsn, "jira", connector)
            methods = {call.request.method for call in router.calls}
            assert methods <= {"GET", "HEAD"}, methods
    finally:
        connector.close()

    assert report.status in ("success", "partial")
    row = _status_row(rw_dsn, "jira")
    assert row.document_count > 0 and row.chunk_count > 0
    assert _search_uri(rw_dsn, "tune the payment retry worker backoff and dead-letter") == JIRA_URI


# == OpenSearch (off by default, ADR-0012 A5) =================================================


class _FakeOs:
    def __init__(self, hits: list[dict]) -> None:
        self.hits = hits
        self.calls: list[tuple[str, dict]] = []
        self.closed = False

    async def search(self, index: str, body: dict, *, request_timeout: float | None = None) -> dict:
        from mcp_opensearch.client import assert_body_allowed

        assert_body_allowed(body)
        self.calls.append((index, body))
        rows = self.hits
        after = body.get("search_after")
        if after:
            rows = [h for h in rows if h["sort"] > after]
        return {"hits": {"hits": rows[: body["size"]]}}

    async def aclose(self) -> None:
        self.closed = True


def _os_connector(indices: list[str], hits: list[dict]) -> OpenSearchConnector:
    return OpenSearchConnector(
        _FakeOs(hits),  # type: ignore[arg-type]
        indices=indices,
        base_url="https://os.tnex.test:9200",
    )


def test_TC122_opensearch_is_off_by_default_and_crawls_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADR-0012 A5: no `MCP_INGEST_OPENSEARCH_INDICES` ⇒ disabled; `run --source opensearch`
    crawls nothing and makes no request (default-deny for an unconfigured source)."""
    monkeypatch.delenv("MCP_INGEST_OPENSEARCH_INDICES", raising=False)
    from mcp_ingest.settings import Settings

    status = opensearch_module.status(Settings())
    assert not status.enabled and not status.configured
    one_hit = [{"_index": "x", "_id": "1", "_source": {}, "sort": ["a"]}]
    connector = _os_connector(indices=[], hits=one_hit)
    try:
        assert list(connector.iter_documents(None, "full")) == []
        assert connector._client.calls == []  # type: ignore[attr-defined]
    finally:
        connector.close()


def test_TC123_opensearch_allowlisted_index_ingests_through_the_same_shape(
    rw_dsn: str,
) -> None:
    hits = [
        {
            "_index": "postmortems-000001",
            "_id": "pm1",
            "_source": {
                "@timestamp": "2026-09-30T10:00:00Z",
                "title": "Payment outage postmortem",
                "content": "The payment worker retry storm caused a dead-letter backlog incident.",
            },
            "sort": ["2026-09-30T10:00:00Z", "pm1"],
        }
    ]
    connector = _os_connector(indices=["postmortems"], hits=hits)
    try:
        report = _run(rw_dsn, "opensearch", connector)
    finally:
        connector.close()
    assert report.status in ("success", "partial")
    row = _status_row(rw_dsn, "opensearch")
    assert row.document_count > 0 and row.chunk_count > 0
    uri = _search_uri(rw_dsn, "payment worker retry storm dead-letter backlog incident")
    assert "postmortems" in uri and "os.tnex.test" in uri


# == no per-connector relaxation of the egress guard (host default-deny) ======================


@pytest.mark.asyncio
async def test_TC122_gitlab_pull_to_an_unconfigured_host_is_denied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The GitLab host must be on the allow-list; an empty allow-list denies the pull before any
    socket — the same default-deny the Confluence path has, no per-connector exception."""
    monkeypatch.setenv("MCP_EGRESS_ALLOWLIST", "")  # deny all
    client = GitLabClient(
        GitLabSettings(base_url=GITLAB_BASE, private_token=SecretStr(FAKE_TOKEN)),
        enforce_egress=True,
    )
    try:
        with respx.mock(assert_all_called=False) as router:
            route = router.get(f"{GITLAB_API}/projects/42").mock(
                return_value=httpx.Response(200, json={})
            )
            with pytest.raises(EgressDenied):
                await client.get_project("42")
            assert route.call_count == 0
    finally:
        await client.aclose()


def test_TC122_every_ingestable_build_enables_the_guard_no_relaxation() -> None:
    """Structural: GitLab / OpenSearch builds (like Confluence/Jira) pass enforce_egress=True.
    (TC-112 in test_egress_wired.py covers all four; re-asserted here for the generalisation.)"""
    import inspect

    for module in (gitlab_module, opensearch_module):
        assert "enforce_egress=True" in inspect.getsource(module.build), module.__name__
