"""pytest wiring for the E2E suite (helpers live in harness.py).

Postgres+pgvector fixtures
--------------------------
By default `pg_server`/`pg_database_factory` are the throw-away initdb/pg_ctl cluster re-exported
from `harness` (which proxies `packages/conftest.py`). That path is Debian-only (R-005: it globs
`/usr/lib/postgresql/*/bin/initdb`) and additionally needs the pgvector extension files installed on
the host, so it skips on macOS and any non-Debian box.

When `MCP_E2E_PG_URL` is set (QA re-run R-005 on a Docker host), these two fixtures are *overridden*
to run against an already-running, trust-auth Postgres+pgvector server reachable at that admin DSN
(user `postgres`, no password — matching the `postgres@` the tests string-replace into role DSNs).
Each test still gets a freshly created, isolated database that is dropped on teardown, exactly like
the throw-away cluster. This file lives under e2e/ (QA-owned) and never touches packages/.
"""

from __future__ import annotations

import os
import sys
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from harness import StubHttp  # noqa: E402
from harness import pg_database_factory as _pkg_pg_database_factory  # noqa: E402
from harness import pg_server as _pkg_pg_server  # noqa: E402

_EXTERNAL_PG_URL = os.environ.get("MCP_E2E_PG_URL")


@dataclass(frozen=True)
class _ExternalPgServer:
    """Mirror of harness.PgServer for an already-running external cluster (no process lifecycle)."""

    admin_url: str
    pgvector_version: str

    def dsn(self, database: str = "postgres", user: str = "postgres") -> str:
        parts = urlsplit(self.admin_url)
        host = parts.hostname or "127.0.0.1"
        port = f":{parts.port}" if parts.port else ""
        return f"postgresql://{user}@{host}{port}/{database}"


if _EXTERNAL_PG_URL:

    @pytest.fixture(scope="session")
    def pg_server() -> Iterator[_ExternalPgServer]:
        import psycopg

        with psycopg.connect(_EXTERNAL_PG_URL, autocommit=True) as conn:
            row = conn.execute(
                "SELECT default_version FROM pg_available_extensions WHERE name = 'vector'"
            ).fetchone()
            if not row:
                pytest.skip(f"pgvector not available on {_EXTERNAL_PG_URL}")
            version = row[0]
        yield _ExternalPgServer(admin_url=_EXTERNAL_PG_URL, pgvector_version=version)

    @pytest.fixture
    def pg_database_factory(pg_server: _ExternalPgServer) -> Iterator[Callable[..., str]]:
        """`factory(template=None) -> admin DSN` of a fresh database, dropped on teardown."""
        import psycopg

        created: list[str] = []

        def factory(template: str | None = None) -> str:
            name = f"t_{uuid.uuid4().hex[:12]}"
            with psycopg.connect(pg_server.dsn(), autocommit=True) as conn:
                suffix = f' TEMPLATE "{template}"' if template else ""
                conn.execute(f'CREATE DATABASE "{name}"{suffix}')
            created.append(name)
            return pg_server.dsn(name)

        yield factory
        with psycopg.connect(pg_server.dsn(), autocommit=True) as conn:
            for name in created:
                for _ in range(5):
                    try:
                        conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
                        break
                    except psycopg.Error:  # pragma: no cover - transient
                        time.sleep(0.2)

    _ = (urlunsplit,)  # keep import referenced for future helpers
else:
    pg_server = _pkg_pg_server
    pg_database_factory = _pkg_pg_database_factory


@pytest.fixture
def stub_http() -> Iterator[Callable[..., StubHttp]]:
    created: list[StubHttp] = []

    def factory(routes: Callable[..., tuple[int, Any]]) -> StubHttp:
        stub = StubHttp(routes)
        created.append(stub)
        return stub

    yield factory
    for stub in created:
        stub.close()
