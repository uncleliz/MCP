"""T-099 (FR-016/AC-004, NFR-006): the startup credential check refuses a write-capable role and
an embedding mismatch — the same guarantee as `mcp_pgvector.client.verify_credentials` (ADR-0003
A1, ADR-0010), so pasting the `mcp_ingest_rw` DSN into `MCP_KNOWLEDGE_DSN` is refused.

Driven with a fake connection (no database): `_verify` runs the named domain probes through
`DomainReadTx`, which we feed canned rows keyed by the SQL text.
"""

from __future__ import annotations

from typing import Any

import pytest
from mcp_ingest.embedding.fake import DeterministicFakeProvider
from mcp_knowledge.client import KnowledgeClient
from mcp_knowledge.domain_sql import DOMAIN_STATEMENTS

pytestmark = pytest.mark.asyncio

PROVIDER = DeterministicFakeProvider(dimensions=1024, model_id="fake/hashed-bow")

# Map a statement NAME to its canned row(s); the fake cursor looks the executed SQL back up to it.
_SQL_TO_NAME = {sql: name for name, sql in DOMAIN_STATEMENTS.items()}


def _rows(**overrides: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    base: dict[str, list[dict[str, Any]]] = {
        "schema_info": [
            {"has_chunks": True, "has_documents": True,
             "has_entities": True, "has_relationships": True}
        ],
        "show_read_only": [{"transaction_read_only": "on"}],
        "role_info": [_role()],
        "stored_models": [{"embedding_model": "fake/hashed-bow"}],
        "embedding_dimensions": [{"dimensions": 1024}],
    }
    base.update(overrides)
    return base


def _role(**over: Any) -> dict[str, Any]:
    role = {
        "role": "mcp_query_ro", "is_superuser": False,
        "chunks_insert": False, "chunks_update": False, "chunks_delete": False,
        "chunks_truncate": False, "documents_insert": False, "documents_update": False,
        "documents_delete": False, "documents_truncate": False, "entities_insert": False,
    }
    role.update(over)
    return role


class _Cursor:
    def __init__(self, rows_by_name: dict[str, list[dict[str, Any]]]) -> None:
        self._rows_by_name = rows_by_name
        self._result: list[dict[str, Any]] = []

    async def __aenter__(self) -> _Cursor:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def execute(self, sql: str, params: Any = None) -> None:
        self._result = self._rows_by_name.get(_SQL_TO_NAME.get(sql, ""), [])

    async def fetchall(self) -> list[dict[str, Any]]:
        return list(self._result)


class _Conn:
    def __init__(self, rows_by_name: dict[str, list[dict[str, Any]]]) -> None:
        self._rows_by_name = rows_by_name

    def cursor(self, *a: Any, **k: Any) -> _Cursor:
        return _Cursor(self._rows_by_name)


async def _verify(rows: dict[str, list[dict[str, Any]]]):
    client = KnowledgeClient("postgresql://mcp_query_ro@127.0.0.1:1/none")
    return await client._verify(_Conn(rows), PROVIDER)  # type: ignore[arg-type]


async def test_read_only_role_is_accepted() -> None:
    report = await _verify(_rows())
    assert report.ok and not report.fatal and report.role == "mcp_query_ro"


async def test_write_capable_role_is_refused_as_fatal() -> None:
    # the mcp_ingest_rw DSN: write privileges present.
    report = await _verify(_rows(role_info=[_role(role="mcp_ingest_rw", chunks_insert=True)]))
    assert not report.ok and report.fatal
    assert any("mcp_ingest_rw" in r or "write privileges" in r for r in report.reasons)


async def test_role_not_read_only_by_default_is_refused() -> None:
    report = await _verify(_rows(show_read_only=[{"transaction_read_only": "off"}]))
    assert not report.ok and report.fatal
    assert any("read-only by default" in r for r in report.reasons)


async def test_superuser_is_refused() -> None:
    report = await _verify(_rows(role_info=[_role(is_superuser=True)]))
    assert not report.ok and report.fatal


async def test_embedding_model_mismatch_is_refused_as_fatal() -> None:
    report = await _verify(_rows(stored_models=[{"embedding_model": "BAAI/bge-m3"}]))
    assert not report.ok and report.fatal
    assert any("embedding model" in r for r in report.reasons)


async def test_unmigrated_schema_is_refused() -> None:
    report = await _verify(
        _rows(schema_info=[{"has_chunks": False, "has_documents": False,
                            "has_entities": False, "has_relationships": False}])
    )
    assert not report.ok


async def test_missing_knowledge_domains_is_a_warning_not_fatal() -> None:
    report = await _verify(
        _rows(schema_info=[{"has_chunks": True, "has_documents": True,
                            "has_entities": False, "has_relationships": False}])
    )
    assert report.ok  # still serves (search works); entity tools degrade
    assert any("knowledge domains" in w for w in report.warnings)


async def test_empty_store_is_a_warning_not_fatal() -> None:
    report = await _verify(_rows(stored_models=[]))
    assert report.ok
    assert any("empty" in w for w in report.warnings)
