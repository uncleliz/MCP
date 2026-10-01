"""Live integration tests against the compose Postgres (`pgvector/pgvector:pg16`, pgvector >= 0.8).

Skipped by default (see packages/conftest.py): they need a running Docker daemon. Run with
`docker compose -f infra/docker-compose.yml up -d postgres`, then
`MCP_LIVE_TESTS=1 uv run pytest packages/mcp_pgvector -m live`.

The superuser `mcp_admin` migrates and seeds a throw-away database (deterministic fake embedder,
never a real model), sets test-only passwords on the two roles, then the server is exercised as
`mcp_query_ro`. Differs from `test_db_integration.py` in what it can prove: the
`hnsw.iterative_scan = relaxed_order` path of ADR-0011 A3 (needs pgvector >= 0.8) and password
authentication of the real roles.
"""

from __future__ import annotations

import os
import uuid

import psycopg
import pytest
from mcp_common.config import CommonSettings
from mcp_ingest.db import upgrade
from mcp_pgvector.client import PgVectorClient
from mcp_pgvector.settings import Settings
from pg_helpers import provider, real_api, seed
from pydantic import SecretStr

pytestmark = pytest.mark.live

ADMIN = os.environ.get(
    "MCP_LIVE_PG_ADMIN_DSN", "postgresql://mcp_admin:mcp_admin_dev_password@localhost:5432/postgres"
)


@pytest.fixture
def live_dsns():
    name = f"live_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(ADMIN, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}"')
    base = ADMIN.rsplit("/", 1)[0]
    admin = f"{base}/{name}"
    try:
        with psycopg.connect(admin, autocommit=True) as conn:
            upgrade(conn)
            conn.execute("ALTER ROLE mcp_query_ro PASSWORD 'live-ro-test'")
            conn.execute("ALTER ROLE mcp_ingest_rw PASSWORD 'live-rw-test'")
        seed(admin, provider(1024))
        scheme_host = base.split("@", 1)[1]
        yield {
            "ro": f"postgresql://mcp_query_ro:live-ro-test@{scheme_host}/{name}",
            "rw": f"postgresql://mcp_ingest_rw:live-rw-test@{scheme_host}/{name}",
        }
    finally:
        with psycopg.connect(ADMIN, autocommit=True) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.mark.asyncio
async def test_live_iterative_scan_filtered_search_and_role_refusal(live_dsns) -> None:
    common = CommonSettings()
    api = real_api(live_dsns["ro"], common)
    caps = await api._client.capabilities()  # noqa: SLF001
    assert caps.supports_iterative_scan, f"compose image must ship pgvector >= 0.8, got {caps}"
    ok = (await api.semantic_search(query="payment worker retry backoff")).result
    assert ok.status.value == "ok"
    gitlab = (
        await api.semantic_search(query="payment worker retry backoff", source_types=["gitlab"])
    ).result
    assert gitlab.status.value in {"ok", "empty"}
    if gitlab.status.value == "empty":
        assert "filters may have excluded matches" in gitlab.meta.warnings[0]
    rw = PgVectorClient(Settings(dsn=SecretStr(live_dsns["rw"])), common=common)
    try:
        report = await rw.verify_credentials(provider(1024))
        assert report.fatal and "INSERT on kb.chunks" in " ".join(report.reasons)
    finally:
        await rw.aclose()
        await api._client.aclose()  # noqa: SLF001
