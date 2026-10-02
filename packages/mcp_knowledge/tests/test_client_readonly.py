"""T-095 — KnowledgeRetrievalClient read-only guards (no database needed).

Exercises the read-only enforcement that does not require a live connection: only named statements
are allowed, only the two HNSW GUCs may be `SET LOCAL`, and the SQL set contains no write/DDL
keyword. The connection lifecycle is covered end-to-end in `test_integration.py`.
"""

from __future__ import annotations

import re

import pytest
from mcp_common.errors import NotPermittedError
from mcp_knowledge.retrieval.client import (
    ALLOWED_STATEMENTS,
    KnowledgeRetrievalClient,
    ReadTx,
)
from mcp_knowledge.retrieval.sql import ALLOWED_GUCS, STATEMENTS

_WRITE_KEYWORDS = re.compile(
    r"\b(insert|update|delete|drop|truncate|alter|create|grant|revoke|copy)\b", re.IGNORECASE
)


def test_sql_statements_are_read_only() -> None:
    # No statement in the closed set contains a write/DDL keyword (set_config is a SELECT).
    for name, sql in STATEMENTS.items():
        assert not _WRITE_KEYWORDS.search(sql), f"statement {name} contains a write keyword"


def test_allowed_statements_matches_sql_keys() -> None:
    assert set(ALLOWED_STATEMENTS) == set(STATEMENTS)


class _StubConn:
    """A connection stub whose cursor is never actually used (enforce raises first)."""

    def cursor(self, *a, **k):  # pragma: no cover - never reached for a denied name
        raise AssertionError("cursor must not be opened for a denied statement")


async def test_fetch_rejects_unknown_statement_name() -> None:
    tx = ReadTx(_StubConn())  # type: ignore[arg-type]
    with pytest.raises(NotPermittedError):
        await tx.fetch("DROP TABLE kb.chunks")


async def test_set_local_rejects_non_allowlisted_guc() -> None:
    tx = ReadTx(_StubConn())  # type: ignore[arg-type]
    with pytest.raises(NotPermittedError):
        await tx.set_local("work_mem", "1GB")


def test_only_hnsw_gucs_are_tunable() -> None:
    assert ALLOWED_GUCS == {"hnsw.ef_search", "hnsw.iterative_scan"}


async def test_aclose_is_safe_when_never_connected() -> None:
    client = KnowledgeRetrievalClient("postgresql://mcp_query_ro@127.0.0.1:1/none")
    await client.aclose()  # no connection was opened; must not raise
