"""Shared fixtures for mcp-pgvector tests."""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterator
from pathlib import Path

import psycopg
import pytest
from mcp_common.config import CommonSettings
from mcp_ingest.db import upgrade

sys.path.insert(0, str(Path(__file__).parent))

from pg_helpers import FakeClient, as_user, provider, seed  # noqa: E402


@pytest.fixture
def common() -> CommonSettings:
    return CommonSettings()


@pytest.fixture
def fake_client() -> FakeClient:
    return FakeClient()


# -- real PostgreSQL + pgvector (skipped when not installed, see packages/conftest.py) ---------


@pytest.fixture(scope="session")
def migrated_template(pg_server) -> Iterator[str]:
    name = "mcp_tpl_pgvector"
    with psycopg.connect(pg_server.dsn(), autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        conn.execute(f'CREATE DATABASE "{name}"')
    with psycopg.connect(pg_server.dsn(name), autocommit=True) as conn:
        upgrade(conn)
    yield name
    with psycopg.connect(pg_server.dsn(), autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture
def admin_dsn(pg_database_factory: Callable[..., str], migrated_template: str) -> str:
    """Superuser DSN of a fresh migrated database (for seeding and negative tests)."""
    return pg_database_factory(template=migrated_template)


@pytest.fixture
def ro_dsn(admin_dsn: str) -> str:
    return as_user(admin_dsn, "mcp_query_ro")


@pytest.fixture
def rw_dsn(admin_dsn: str) -> str:
    return as_user(admin_dsn, "mcp_ingest_rw")


@pytest.fixture
def seeded_ids(admin_dsn: str) -> dict[str, str]:
    return seed(admin_dsn, provider(1024))
