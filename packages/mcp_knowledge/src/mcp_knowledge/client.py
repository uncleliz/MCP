"""Read-only PostgreSQL client for the knowledge domain tools + startup credential check (T-099).

This is the top-level transport for :mod:`mcp_knowledge.tools` (entity / related / summary / version
and the hybrid search tools). It is a thin peer of :mod:`mcp_knowledge.retrieval.client` — it opens
the SAME ``mcp_query_ro`` connection with ``BEGIN READ ONLY`` and runs only the named statements of
:mod:`mcp_knowledge.domain_sql` — plus the startup credential/consistency check that MIRRORS
:meth:`mcp_pgvector.client.PgVectorClient.verify_credentials` (ADR-0003 A1, ADR-0010):

* refuses a write-capable role (pasting the ``mcp_ingest_rw`` DSN into ``MCP_KNOWLEDGE_DSN`` is
  exactly the hole ADR-0003 A1 closes);
* refuses a superuser or a role not read-only by default;
* refuses when the stored ``embedding_model`` / dimension differs from the configured provider.

The retrieval engine (:class:`mcp_knowledge.retrieval.hybrid.HybridRetriever`) is driven by the
sibling :class:`mcp_knowledge.retrieval.client.KnowledgeRetrievalClient`; this client exposes
:meth:`retrieval_client` so both share one DSN and one read-only contract.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import psycopg
from mcp_common.readonly import enforce
from mcp_common.redact import register_dsn_secret
from mcp_ingest.embedding import EmbeddingModelMismatchError, validate_stored_embeddings
from mcp_ingest.ports import EmbeddingProvider
from pgvector.psycopg import register_vector_async
from psycopg.rows import dict_row

from mcp_knowledge.domain_sql import DOMAIN_STATEMENTS
from mcp_knowledge.retrieval.client import (
    CONNECT_TIMEOUT_S,
    STATEMENT_TIMEOUT_MS,
    KnowledgeRetrievalClient,
)
from mcp_knowledge.retrieval.sql import ALLOWED_GUCS

__all__ = [
    "ALLOWED_DOMAIN_STATEMENTS",
    "CredentialReport",
    "DomainReadTx",
    "KnowledgeClient",
    "SOURCE",
]

SOURCE = "knowledge"
ALLOWED_DOMAIN_STATEMENTS: tuple[str, ...] = tuple(DOMAIN_STATEMENTS)

_WRITE_PRIVILEGES = (
    ("chunks_insert", "INSERT on kb.chunks"),
    ("chunks_update", "UPDATE on kb.chunks"),
    ("chunks_delete", "DELETE on kb.chunks"),
    ("chunks_truncate", "TRUNCATE on kb.chunks"),
    ("documents_insert", "INSERT on kb.documents"),
    ("documents_update", "UPDATE on kb.documents"),
    ("documents_delete", "DELETE on kb.documents"),
    ("documents_truncate", "TRUNCATE on kb.documents"),
    ("entities_insert", "INSERT on kb.entities"),
)


@dataclass
class CredentialReport:
    """Mirror of :class:`mcp_pgvector.client.CredentialReport` (ADR-0003 A1)."""

    ok: bool
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # `fatal`: positive evidence that serving would be unsafe/wrong (write-capable role, embedding
    # mismatch). Not bypassable with MCP_ALLOW_UNVERIFIED_CREDENTIALS (that only covers "could not
    # verify", e.g. the database being down).
    fatal: bool = False
    role: str | None = None


class DomainReadTx:
    """A read-only transaction over the knowledge domain statements (named, parameterised only)."""

    def __init__(self, conn: psycopg.AsyncConnection[Any]) -> None:
        self._conn = conn

    async def fetch(self, name: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        enforce(ALLOWED_DOMAIN_STATEMENTS, name, source=SOURCE)
        async with self._conn.cursor(row_factory=dict_row) as cursor:
            await cursor.execute(DOMAIN_STATEMENTS[name], params or {})
            return list(await cursor.fetchall())

    async def set_local(self, name: str, value: str) -> None:
        enforce(sorted(ALLOWED_GUCS), name, source=SOURCE)
        await self.fetch("set_config", {"name": name, "value": value})


class KnowledgeClient:
    """A read-only knowledge client: domain statements + startup credential check."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        # E-mcp-data-platform-009 (FR-025/NFR-014): register the configured DSN credential for
        # value-based scrubbing at the single knowledge client-construction seam (server + CLI
        # build this), so a psycopg error carrying the DSN/password is redacted from any
        # outbound error/result/log. Additive.
        register_dsn_secret(dsn)
        self._conn: psycopg.AsyncConnection[Any] | None = None
        self._lock = asyncio.Lock()
        self._retrieval = KnowledgeRetrievalClient(dsn)

    def retrieval_client(self) -> KnowledgeRetrievalClient:
        """The sibling client the :class:`HybridRetriever` uses (same DSN, same read-only tx)."""
        return self._retrieval

    async def _open(self, *, read_only: bool) -> psycopg.AsyncConnection[Any]:
        conn = await psycopg.AsyncConnection.connect(
            self._dsn,
            connect_timeout=CONNECT_TIMEOUT_S,
            autocommit=not read_only,
            options=f"-c statement_timeout={STATEMENT_TIMEOUT_MS}",
            application_name="mcp-knowledge",
        )
        try:
            if read_only:
                await conn.set_read_only(True)
            await register_vector_async(conn)
        except BaseException:
            await conn.close()
            raise
        return conn

    async def _connection(self) -> psycopg.AsyncConnection[Any]:
        if self._conn is None or self._conn.closed or self._conn.broken:
            self._conn = await self._open(read_only=True)
        return self._conn

    async def _discard(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            with contextlib.suppress(Exception):
                await asyncio.shield(conn.close())

    async def aclose(self) -> None:
        async with self._lock:
            await self._discard()
        await self._retrieval.aclose()

    @asynccontextmanager
    async def read_tx(self) -> AsyncIterator[DomainReadTx]:
        async with self._lock:
            try:
                conn = await self._connection()
                async with conn.transaction():
                    yield DomainReadTx(conn)
            except asyncio.CancelledError:
                await self._discard()
                raise
            except Exception:
                if self._conn is not None and (self._conn.closed or self._conn.broken):
                    await self._discard()
                raise

    # -- startup credential + consistency check (mirror mcp_pgvector) ----------------------------

    async def verify_credentials(self, provider: EmbeddingProvider) -> CredentialReport:
        """Refuse a write-capable role and an embedding-space mismatch (ADR-0003 A1, ADR-0010)."""
        try:
            conn = await self._open(read_only=False)
        except Exception as exc:  # noqa: BLE001 - reported, never raised: the DSN stays secret
            return CredentialReport(False, [f"could not verify credentials: {type(exc).__name__}"])
        try:
            return await self._verify(conn, provider)
        except Exception as exc:  # noqa: BLE001
            return CredentialReport(False, [f"could not verify credentials: {type(exc).__name__}"])
        finally:
            await conn.close()

    async def _verify(
        self, conn: psycopg.AsyncConnection[Any], provider: EmbeddingProvider
    ) -> CredentialReport:
        tx = DomainReadTx(conn)
        reasons: list[str] = []
        warnings: list[str] = []
        fatal = False
        schema = (await tx.fetch("schema_info"))[0]
        if not (schema["has_chunks"] and schema["has_documents"]):
            return CredentialReport(
                False, ["schema kb is not migrated: run `mcp-ingest db upgrade` first"]
            )
        if not (schema["has_entities"] and schema["has_relationships"]):
            warnings.append(
                "knowledge domains (kb.entities/kb.relationships) are not migrated: run "
                "`mcp-ingest db upgrade` to enable get_service/find_related_knowledge"
            )
        read_only_default = (await tx.fetch("show_read_only"))[0]["transaction_read_only"]
        role = (await tx.fetch("role_info"))[0]
        if read_only_default != "on":
            reasons.append(
                f"role '{role['role']}' is not read-only by default "
                "(default_transaction_read_only=off): MCP_KNOWLEDGE_DSN must use mcp_query_ro"
            )
            fatal = True
        if role["is_superuser"]:
            reasons.append(f"role '{role['role']}' is a superuser; use the mcp_query_ro role")
            fatal = True
        granted = [label for key, label in _WRITE_PRIVILEGES if role.get(key)]
        if granted:
            reasons.append(
                f"role '{role['role']}' has write privileges ({', '.join(granted)}): this looks "
                "like the mcp_ingest_rw DSN; MCP_KNOWLEDGE_DSN must be the read-only mcp_query_ro"
            )
            fatal = True
        stored = {r["embedding_model"] for r in await tx.fetch("stored_models")}
        dims = (await tx.fetch("embedding_dimensions"))[0]["dimensions"]
        try:
            validate_stored_embeddings(provider, stored_models=stored, stored_dimensions=dims)
        except EmbeddingModelMismatchError as exc:
            reasons.append(str(exc))
            fatal = True
        if not stored:
            warnings.append("kb.chunks is empty: nothing has been indexed yet (run mcp-ingest)")
        return CredentialReport(not reasons, reasons, warnings, fatal, role["role"])

    async def credential_check(self, provider: EmbeddingProvider) -> bool:
        """`mcp_common.runtime.serve(credential_check=...)` hook."""
        return (await self.verify_credentials(provider)).ok
