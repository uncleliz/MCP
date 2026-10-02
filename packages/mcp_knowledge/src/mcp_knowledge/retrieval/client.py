"""Read-only PostgreSQL transport for Hybrid-RAG retrieval (T-095, ADR-0003, ADR-0008, ADR-0011).

Mirrors the read-only guarantees of :mod:`mcp_pgvector.client` for the retrieval SQL of
:mod:`mcp_knowledge.retrieval.sql`:

* no method takes SQL text — :meth:`ReadTx.fetch` looks a statement up **by name**;
* every transaction is opened with ``BEGIN READ ONLY`` (``connection.set_read_only(True)``);
* the configured DSN must be the ``mcp_query_ro`` role (verified by the server's startup check,
  built in E4/T-099 — this module is only the transport).

Timeouts: ``connect_timeout=3``, ``statement_timeout=15s``. psycopg 3 is natively async; a single
connection is shared behind a lock (one stdio user) and dropped on cancellation/broken connection.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import psycopg
from mcp_common.errors import ErrorCode, ToolError
from mcp_common.readonly import enforce
from pgvector.psycopg import register_vector_async
from psycopg.rows import dict_row

from mcp_knowledge.retrieval.sql import ALLOWED_GUCS, STATEMENTS

__all__ = [
    "ALLOWED_STATEMENTS",
    "CONNECT_TIMEOUT_S",
    "STATEMENT_TIMEOUT_MS",
    "KnowledgeRetrievalClient",
    "ReadTx",
    "SOURCE",
]

SOURCE = "knowledge"
CONNECT_TIMEOUT_S = 3
STATEMENT_TIMEOUT_MS = 15_000
ALLOWED_STATEMENTS: tuple[str, ...] = tuple(STATEMENTS)


class ReadTx:
    """A read-only transaction: named, parameterised statements only."""

    def __init__(self, conn: psycopg.AsyncConnection[Any]) -> None:
        self._conn = conn

    async def fetch(self, name: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        enforce(ALLOWED_STATEMENTS, name, source=SOURCE)
        async with self._conn.cursor(row_factory=dict_row) as cursor:
            await cursor.execute(STATEMENTS[name], params or {})
            return list(await cursor.fetchall())

    async def set_local(self, name: str, value: str) -> None:
        """`SET LOCAL <guc> = <value>` for the HNSW tuning knobs only."""
        enforce(sorted(ALLOWED_GUCS), name, source=SOURCE)
        await self.fetch("set_config", {"name": name, "value": value})


class KnowledgeRetrievalClient:
    """A read-only pgvector client that yields ``BEGIN READ ONLY`` transactions."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._conn: psycopg.AsyncConnection[Any] | None = None
        self._lock = asyncio.Lock()

    async def _open(self) -> psycopg.AsyncConnection[Any]:
        conn = await psycopg.AsyncConnection.connect(
            self._dsn,
            connect_timeout=CONNECT_TIMEOUT_S,
            autocommit=False,
            options=f"-c statement_timeout={STATEMENT_TIMEOUT_MS}",
            application_name="mcp-knowledge",
        )
        try:
            await conn.set_read_only(True)
            await register_vector_async(conn)
        except psycopg.ProgrammingError as exc:
            await conn.close()
            raise ToolError(
                ErrorCode.SOURCE_MISCONFIGURED,
                "Extension pgvector chưa cài: chạy `mcp-ingest db upgrade`.",
                SOURCE, False, details={"hint": "mcp-ingest db upgrade"},
            ) from exc  # fmt: skip
        except BaseException:
            await conn.close()
            raise
        return conn

    async def _connection(self) -> psycopg.AsyncConnection[Any]:
        if self._conn is None or self._conn.closed or self._conn.broken:
            self._conn = await self._open()
        return self._conn

    async def _discard(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            with contextlib.suppress(Exception):
                await asyncio.shield(conn.close())

    async def aclose(self) -> None:
        async with self._lock:
            await self._discard()

    @asynccontextmanager
    async def read_tx(self) -> AsyncIterator[ReadTx]:
        async with self._lock:
            try:
                conn = await self._connection()
                async with conn.transaction():
                    yield ReadTx(conn)
            except asyncio.CancelledError:
                await self._discard()
                raise
            except Exception:
                if self._conn is not None and (self._conn.closed or self._conn.broken):
                    await self._discard()
                raise
