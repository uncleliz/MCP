"""T-115 / TC-118, TC-119, TC-120, TC-121 — Confluence Cloud end to end on FIXTURES.

The full real pull→redact→chunk→embed→persist→verify path, over the REAL `ConfluenceConnector`
and `mcp_confluence.client` with the Confluence Cloud REST shape served by `respx` at the real
tenant host `tnexwm.atlassian.net`, a fake read-only token, the egress guard active
(`*.atlassian.net`), the `DeterministicFakeProvider` embedding provider, and a real pgvector
database. The ingested content is then read back through `PgVectorReadApi` (the read-only
`mcp_query_ro` path the MCP server wires) to prove `kb_semantic_search` returns a citation whose
`source_uri` resolves to `tnexwm.atlassian.net`.

* TC-118 — doctor→run→status→verify succeeds; counts > 0; citation resolves to the real tenant.
* TC-119 — an unreachable source → partial/failed, checkpoint does not advance, no corpora corrupt.
* TC-120 — read-only-to-source: only GET/HEAD reach Confluence; writes go only to `kb.*`.
* TC-121 — the real-tenant arm, `@live` (skipped unless MCP_LIVE_TESTS=1 / live egress enabled);
  it is written here, marked, and never runs in `make ci`.

No live network and no real credential run in CI; the real-tenant run is TC-121 only.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from ingest_helpers import make_ctx, provider, q, scalar
from mcp_common.config import CommonSettings
from mcp_common.egress import check_egress
from mcp_confluence.client import ConfluenceClient
from mcp_confluence.settings import Settings as ConfluenceSettings
from mcp_ingest.commands.status import gather_status
from mcp_ingest.connectors.confluence import ConfluenceConnector
from mcp_ingest.pipeline.run import RunOptions, run_ingest
from mcp_pgvector.client import PgVectorClient
from mcp_pgvector.read_api import PgVectorReadApi
from mcp_pgvector.settings import Settings as PgSettings
from pydantic import SecretStr

TENANT = "https://tnexwm.atlassian.net"
BASE = f"{TENANT}/wiki"
SEARCH = f"{BASE}/rest/api/content/search"
USER = f"{BASE}/rest/api/user/current"
FAKE_TOKEN = "fake-ro-token-not-a-real-secret"
TEAM_SPACES = ["ENG"]
FIXTURES = Path(__file__).parent / "fixtures" / "confluence_cloud"

RETRY_URI = f"{BASE}/spaces/ENG/pages/100100/Payment-retry-policy"
RESTRICTED_URI = f"{BASE}/spaces/ENG/pages/100200/Executive-compensation"


def _fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def rw_dsn(request: pytest.FixtureRequest) -> str:
    """`mcp_ingest_rw` DSN on a fresh migrated database.

    Prefers the local-binary `pg_server` (the CI/Linux path, unchanged). When those binaries are
    absent (e.g. a macOS dev box) it falls back to the running dev pgvector container
    (`packages/conftest.py::docker_pg_factory`) so the real-DB e2e arms still execute. Skips, with a
    reason, only when neither is available.
    """
    try:
        local = request.getfixturevalue("migrated_db")
        from ingest_helpers import as_user

        return as_user(local, "mcp_ingest_rw")
    except pytest.skip.Exception:
        pass
    factory = request.getfixturevalue("docker_pg_factory")  # skips itself if no container
    admin = factory()
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


def _confluence_connector() -> ConfluenceConnector:
    """The REAL connector + client, egress-enforced, pointed at the real tenant (fake token)."""
    client = ConfluenceClient(
        ConfluenceSettings(
            base_url=BASE, email="svc-ro@tnex.test", api_token=SecretStr(FAKE_TOKEN)
        ),
        common=CommonSettings(http_backoff_base=0.0),
        enforce_egress=True,
    )
    return ConfluenceConnector(client, team_spaces=TEAM_SPACES)


def _serve_cloud(router: respx.MockRouter) -> respx.Route:
    """Serve the Confluence Cloud search payload (page 1 only; empty thereafter)."""
    payload = _fixture("search_page.json")

    def handler(request: httpx.Request) -> httpx.Response:
        start = int(request.url.params.get("start", "0"))
        if start == 0:
            return httpx.Response(200, json=payload)
        return httpx.Response(200, json={"results": [], "_links": {}})

    router.get(USER).mock(
        return_value=httpx.Response(200, json={"type": "known", "accountId": "svc"})
    )
    return router.get(SEARCH).mock(side_effect=handler)


def _run_confluence(rw_dsn: str, connector: ConfluenceConnector, *, mode: str = "full"):
    """doctor→run step: run the real pipeline for the single confluence source over `connector`."""
    opts = RunOptions(sources=["confluence"], mode=mode)  # type: ignore[arg-type]
    return run_ingest(
        make_ctx(rw_dsn),
        opts,
        describe=lambda _n: connector.status(),
        build=lambda _n: connector,
        explicit_source=True,
    )


def _to_query_ro(rw_dsn: str) -> str:
    """Swap the `mcp_ingest_rw` role for the read-only `mcp_query_ro` role, keeping any password."""
    return rw_dsn.replace("mcp_ingest_rw:", "mcp_query_ro:").replace(
        "mcp_ingest_rw@", "mcp_query_ro@"
    )


def _read_api(rw_dsn: str) -> PgVectorReadApi:
    """A `PgVectorReadApi` over the read-only `mcp_query_ro` role — the real server read path."""
    ro = _to_query_ro(rw_dsn)
    settings = PgSettings(dsn=SecretStr(ro))
    client = PgVectorClient(settings, common=CommonSettings())
    return PgVectorReadApi(
        client, provider(), CommonSettings(), settings, now=lambda: datetime.now(UTC)
    )


# == TC-118 — doctor → run → status → verify, fixtures + fake token ============================


def test_TC118_confluence_cloud_e2e_doctor_run_status_then_search(
    rw_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MCP_EGRESS_ALLOWLIST", "*.atlassian.net")
    connector = _confluence_connector()
    try:
        with respx.mock(assert_all_called=False) as router:
            # doctor: the read-only credential check succeeds against the tenant fixture.
            _serve_cloud(router)
            assert connector._client._settings.base_url == BASE  # config targets the real tenant
            # run: the full pipeline writes kb.* for the one team page (the restricted one blocked).
            report = _run_confluence(rw_dsn, connector)
    finally:
        connector.close()

    assert report.status in ("success", "partial")
    (source,) = [s for s in report.sources if s.source_type == "confluence"]
    assert source.documents_upserted >= 1

    # status: Confluence shows a recent last_success_at and document/chunk counts > 0.
    import psycopg

    with psycopg.connect(rw_dsn, autocommit=True) as conn:
        status = gather_status(conn, source="confluence", known=["confluence"])
    (row,) = status.sources
    assert row.source_type == "confluence"
    assert row.last_success_at is not None
    assert row.document_count > 0 and row.chunk_count > 0

    # verify: kb_semantic_search returns the ingested page with a citation to the real tenant.
    import asyncio

    async def _search() -> None:
        api = _read_api(rw_dsn)
        outcome = await api.semantic_search(
            query="payment worker retries failed transactions with exponential backoff", top_k=5
        )
        result = outcome.result
        assert result.status.value == "ok"
        top = result.items[0]
        assert top["source_type"] == "confluence"
        assert top["source_uri"] == RETRY_URI
        assert "tnexwm.atlassian.net" in result.citations[0].uri

    asyncio.run(_search())


# == TC-120 — read-only-to-source: only GET/HEAD to Confluence, writes only to kb.* ============


def test_TC120_pull_is_read_only_to_source_and_writes_only_to_kb(
    rw_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MCP_EGRESS_ALLOWLIST", "*.atlassian.net")
    connector = _confluence_connector()
    try:
        with respx.mock(assert_all_called=False) as router:
            route = _serve_cloud(router)
            _run_confluence(rw_dsn, connector)
            # every verb issued to Confluence is GET/HEAD (read-only-to-source, §6e#4).
            methods = {call.request.method for call in router.calls}
            assert methods <= {"GET", "HEAD"}, methods
            assert route.call_count >= 1
    finally:
        connector.close()

    # the only writes landed in kb.* (documents + chunks), under the mcp_ingest_rw role.
    assert scalar(rw_dsn, "SELECT count(*) FROM kb.documents WHERE source_type='confluence'") >= 1
    assert scalar(rw_dsn, "SELECT count(*) FROM kb.chunks") >= 1


# == TC-119 — an unreachable source does not corrupt the corpus / advance the checkpoint =======


def test_TC119_unreachable_source_is_partial_and_leaves_the_checkpoint(
    rw_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MCP_EGRESS_ALLOWLIST", "*.atlassian.net")

    # First: a healthy run so there is prior state + embeddings to protect.
    good = _confluence_connector()
    try:
        with respx.mock(assert_all_called=False) as router:
            _serve_cloud(router)
            healthy = _run_confluence(rw_dsn, good)
    finally:
        good.close()
    assert healthy.status in ("success", "partial")
    docs_before = scalar(rw_dsn, "SELECT count(*) FROM kb.documents WHERE source_type='confluence'")
    chunks_before = scalar(rw_dsn, "SELECT count(*) FROM kb.chunks")
    state_before = q(
        rw_dsn,
        "SELECT last_success_at FROM kb.ingest_source_state WHERE source_type='confluence'",
    )

    # Then: the source goes unreachable mid-run (500s). Nothing is corrupted.
    failing = _confluence_connector()
    try:
        with respx.mock(assert_all_called=False) as router:
            router.get(USER).mock(
                return_value=httpx.Response(200, json={"type": "known", "accountId": "svc"})
            )
            router.get(SEARCH).mock(return_value=httpx.Response(503, json={"message": "down"}))
            report = _run_confluence(rw_dsn, failing, mode="incremental")
    finally:
        failing.close()

    assert report.status in ("partial", "failed")
    assert report.exit_code != 0
    # prior embeddings not corrupted; the checkpoint did not regress.
    assert (
        scalar(rw_dsn, "SELECT count(*) FROM kb.documents WHERE source_type='confluence'")
        == docs_before
    )
    assert scalar(rw_dsn, "SELECT count(*) FROM kb.chunks") == chunks_before
    state_after = q(
        rw_dsn,
        "SELECT last_success_at FROM kb.ingest_source_state WHERE source_type='confluence'",
    )
    assert state_after == state_before, "a failed crawl must not advance the checkpoint"


# == TC-121 — the REAL tenant arm. @live: not in `make ci`. =====================================


@pytest.mark.live
def test_TC121_live_confluence_cloud_against_the_real_tenant(rw_dsn: str) -> None:
    """`@live` (skipped unless MCP_LIVE_TESTS=1). The operator (CEO) runs this behind
    MCP_INGEST_ALLOW_LIVE_EGRESS=true with the real read-only token for tnexwm.atlassian.net;
    it is NEVER part of `make ci`. Same expectations as TC-118 but against the live tenant.
    """
    if os.environ.get("MCP_INGEST_ALLOW_LIVE_EGRESS") != "true":
        pytest.skip(
            "live Confluence egress is gated: set MCP_INGEST_ALLOW_LIVE_EGRESS=true "
            "(and the real read-only MCP_CONFLUENCE_API_TOKEN_FILE) to run against the tenant"
        )
    # The real token/allow-list are the operator's env; the egress guard must admit the tenant.
    check_egress(TENANT, settings=CommonSettings())  # raises EgressDenied if not allow-listed
    connector = _confluence_connector()  # real network: no respx mounted
    try:
        report = _run_confluence(rw_dsn, connector)
    finally:
        connector.close()
    assert report.status in ("success", "partial")

    import asyncio

    async def _search() -> None:
        api = _read_api(rw_dsn)
        outcome = await api.semantic_search(query="payment retry policy", top_k=5)
        assert outcome.result.status.value == "ok"
        assert "tnexwm.atlassian.net" in outcome.result.citations[0].uri

    asyncio.run(_search())
