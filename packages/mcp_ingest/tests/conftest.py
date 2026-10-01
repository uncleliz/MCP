"""Fixtures for mcp-ingest tests (throw-away local Postgres+pgvector: packages/conftest.py)."""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterator
from pathlib import Path

import psycopg
import pytest
from mcp_ingest.db import upgrade

sys.path.insert(0, str(Path(__file__).parent))

from ingest_helpers import as_user  # noqa: E402


@pytest.fixture
def fresh_db(pg_database_factory: Callable[..., str]) -> str:
    """Admin DSN of an empty database (no `kb` schema yet)."""
    return pg_database_factory()


@pytest.fixture(scope="session")
def migrated_template(pg_server) -> Iterator[str]:
    """A database migrated once to the latest version; tests clone it with TEMPLATE."""
    name = "mcp_tpl_migrated"
    with psycopg.connect(pg_server.dsn(), autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        conn.execute(f'CREATE DATABASE "{name}"')
    with psycopg.connect(pg_server.dsn(name), autocommit=True) as conn:
        upgrade(conn)
    yield name
    with psycopg.connect(pg_server.dsn(), autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture
def migrated_db(pg_database_factory: Callable[..., str], migrated_template: str) -> str:
    return pg_database_factory(template=migrated_template)


@pytest.fixture
def rw_dsn(migrated_db: str) -> str:
    """DSN of the write-capable `mcp_ingest_rw` role on a fresh migrated database."""
    return as_user(migrated_db, "mcp_ingest_rw")
