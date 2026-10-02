"""PostgreSQL transport layer for mcp-pgvector (ADR-0008, ADR-0003, ADR-0011).

Read-only guarantees in this file (defense in depth, ADR-0003):

* layer 2 — there is no method that takes SQL text. :meth:`ReadTx.fetch` looks the statement up
  by **name** in :data:`mcp_pgvector.sql.STATEMENTS`; any other name raises `not_permitted`
  before anything is sent;
* layer 2b — every query runs in a transaction opened with `BEGIN READ ONLY`
  (`connection.set_read_only(True)`), so even a role that *could* write cannot in this path;
* layer 3 — the startup check (:meth:`PgVectorClient.verify_credentials`) connects with the
  configured DSN and **refuses to serve** unless the role is read-only by default
  (`SHOW transaction_read_only = on`), is not a superuser and cannot INSERT/UPDATE/DELETE/
  TRUNCATE `kb.chunks`/`kb.documents` — pasting the `mcp_ingest_rw` DSN here is exactly the hole
  ADR-0003 A1 closes. It also refuses when the stored `embedding_model` differs from the
  configured one (ADR-0010).

Timeouts: `connect_timeout=3`, `statement_timeout=15s` (ADR-0008 A4, set on the session).
psycopg 3 is natively async, so no thread executor is needed here; a single connection is shared
behind a lock (one stdio user), and dropped on any cancellation or broken-connection error.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import psycopg
from mcp_common.config import CommonSettings
from mcp_common.errors import ErrorCode, ToolError, map_exception_to_tool_error
from mcp_common.readonly import enforce
from mcp_common.redact import register_dsn_secret
from mcp_ingest.embedding import EmbeddingModelMismatchError, validate_stored_embeddings
from mcp_ingest.ports import EmbeddingProvider
from pgvector.psycopg import register_vector_async
from psycopg import errors as pgerrors
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

from mcp_pgvector.settings import Settings
from mcp_pgvector.sql import ALLOWED_GUCS, STATEMENTS

__all__ = [
    "ALLOWED_STATEMENTS",
    "CONNECT_TIMEOUT_S",
    "STATEMENT_TIMEOUT_MS",
    "Capabilities",
    "CredentialReport",
    "PgVectorClient",
    "ReadTx",
    "SOURCE",
    "parse_version",
    "to_tool_error",
]

SOURCE = "pgvector"
CONNECT_TIMEOUT_S = 3
STATEMENT_TIMEOUT_MS = 15_000
ALLOWED_STATEMENTS: tuple[str, ...] = tuple(STATEMENTS)
ITERATIVE_SCAN_MIN_VERSION = (0, 8, 0)

_WRITE_PRIVILEGES = (
    ("chunks_insert", "INSERT on kb.chunks"),
    ("chunks_update", "UPDATE on kb.chunks"),
    ("chunks_delete", "DELETE on kb.chunks"),
    ("chunks_truncate", "TRUNCATE on kb.chunks"),
    ("documents_insert", "INSERT on kb.documents"),
    ("documents_update", "UPDATE on kb.documents"),
    ("documents_delete", "DELETE on kb.documents"),
    ("documents_truncate", "TRUNCATE on kb.documents"),
)


def parse_version(text: str | None) -> tuple[int, ...] | None:
    if not text:
        return None
    parts = re.findall(r"\d+", text)[:3]
    return tuple(int(p) for p in parts) if parts else None


@dataclass(frozen=True)
class Capabilities:
    """What the connected pgvector can do (decides the HNSW post-filter strategy, ADR-0011 A3)."""

    version: tuple[int, ...] | None

    @property
    def supports_iterative_scan(self) -> bool:
        return self.version is not None and self.version >= ITERATIVE_SCAN_MIN_VERSION


@dataclass
class CredentialReport:
    ok: bool
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # `fatal`: positive evidence that serving would be unsafe or wrong (a write-capable role, a
    # different embedding space). Not bypassable with MCP_ALLOW_UNVERIFIED_CREDENTIALS, which only
    # covers "could not verify" (e.g. the database is down).
    fatal: bool = False
    role: str | None = None
    pgvector_version: str | None = None


def _dsn_host(dsn: str) -> str | None:
    try:
        host = conninfo_to_dict(dsn).get("host")
        return str(host) if host else None
    except psycopg.ProgrammingError:
        return None


def to_tool_error(exc: BaseException, host: str | None) -> ToolError:
    """Classify a psycopg exception into the contract's error taxonomy (never echoes the DSN)."""
    hint = f"kiểm tra VPN/kết nối nội bộ tới Postgres ({host or 'host'})"
    if isinstance(exc, pgerrors.QueryCanceled):
        return ToolError(
            ErrorCode.UPSTREAM_TIMEOUT,
            f"Truy vấn vượt statement_timeout {STATEMENT_TIMEOUT_MS // 1000}s.",
            SOURCE, True, details={"host": host, "hint": "thu hẹp bộ lọc hoặc giảm top_k"},
        )  # fmt: skip
    if isinstance(exc, pgerrors.InsufficientPrivilege):
        return ToolError(
            ErrorCode.FORBIDDEN, "Role Postgres không đủ quyền SELECT trên schema kb.",
            SOURCE, False, details={"sqlstate": exc.sqlstate},
        )  # fmt: skip
    if isinstance(exc, pgerrors.UndefinedTable | pgerrors.InvalidSchemaName):
        return ToolError(
            ErrorCode.SOURCE_MISCONFIGURED,
            "Schema kb chưa tồn tại: chạy `mcp-ingest db upgrade` trước.",
            SOURCE, False, details={"missing_env": [], "hint": "mcp-ingest db upgrade"},
        )  # fmt: skip
    if isinstance(exc, pgerrors.InvalidPassword | pgerrors.InvalidAuthorizationSpecification):
        return ToolError(
            ErrorCode.UNAUTHORIZED,
            "Postgres từ chối thông tin đăng nhập (kiểm tra MCP_PGVECTOR_DSN).",
            SOURCE, False, details={"missing_env": ["MCP_PGVECTOR_DSN"]},
        )  # fmt: skip
    if isinstance(exc, psycopg.OperationalError):
        timed_out = "timeout" in str(exc).lower() or "timed out" in str(exc).lower()
        return ToolError(
            ErrorCode.UPSTREAM_TIMEOUT if timed_out else ErrorCode.UPSTREAM_UNAVAILABLE,
            "Timeout khi kết nối Postgres." if timed_out else "Không kết nối được tới Postgres.",
            SOURCE, True, details={"host": host, "hint": hint},
        )  # fmt: skip
    if isinstance(exc, Exception):
        return map_exception_to_tool_error(exc, source=SOURCE, host=host)
    raise exc  # pragma: no cover - BaseException (cancellation) is never mapped


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
        """`SET LOCAL <guc> = <value>` for the two HNSW tuning knobs only."""
        enforce(sorted(ALLOWED_GUCS), name, source=SOURCE)
        await self.fetch("set_config", {"name": name, "value": value})


class PgVectorClient:
    def __init__(self, settings: Settings, *, common: CommonSettings | None = None) -> None:
        self._settings = settings
        self._common = common or CommonSettings()
        # E-mcp-data-platform-009 (FR-025/NFR-014): register the configured DSN credential for
        # value-based scrubbing at the single client-construction seam, so a psycopg error
        # carrying the DSN/password is redacted from any outbound error/result/log. Additive.
        register_dsn_secret(settings.dsn.get_secret_value())
        self._host = _dsn_host(settings.dsn.get_secret_value())
        self._conn: psycopg.AsyncConnection[Any] | None = None
        self._lock = asyncio.Lock()
        self._capabilities: Capabilities | None = None

    # -- connections ---------------------------------------------------------------------------

    async def _open(self, *, read_only: bool) -> psycopg.AsyncConnection[Any]:
        conn = await psycopg.AsyncConnection.connect(
            self._settings.dsn.get_secret_value(),
            connect_timeout=CONNECT_TIMEOUT_S,
            autocommit=not read_only,
            options=f"-c statement_timeout={STATEMENT_TIMEOUT_MS}",
            application_name="mcp-pgvector",
        )
        try:
            if read_only:
                await conn.set_read_only(True)  # every transaction starts with BEGIN READ ONLY
            await register_vector_async(conn)
        except psycopg.ProgrammingError as exc:  # "vector type not found in the database"
            await conn.close()
            raise ToolError(
                ErrorCode.SOURCE_MISCONFIGURED,
                "Extension pgvector chưa được cài trong database: chạy `mcp-ingest db upgrade`.",
                SOURCE,
                False,
                details={"missing_env": [], "hint": "mcp-ingest db upgrade"},
            ) from exc
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

    @asynccontextmanager
    async def read_tx(self) -> AsyncIterator[ReadTx]:
        """One `BEGIN READ ONLY` transaction on the shared connection."""
        async with self._lock:
            try:
                conn = await self._connection()
                async with conn.transaction():
                    yield ReadTx(conn)
            except ToolError:
                raise
            except asyncio.CancelledError:
                await self._discard()  # state of an interrupted query is unknown
                raise
            except Exception as exc:
                if self._conn is not None and (self._conn.closed or self._conn.broken):
                    await self._discard()
                raise to_tool_error(exc, self._host) from exc

    async def capabilities(self) -> Capabilities:
        if self._capabilities is None:
            async with self.read_tx() as tx:
                rows = await tx.fetch("extension_version")
            version = parse_version(rows[0]["extversion"]) if rows else None
            self._capabilities = Capabilities(version)
        return self._capabilities

    # -- startup credential + consistency check ---------------------------------------------------

    async def verify_credentials(self, provider: EmbeddingProvider) -> CredentialReport:
        """Refuse a write-capable role and an embedding-space mismatch (ADR-0003 A1, ADR-0010)."""
        try:
            conn = await self._open(read_only=False)
        except Exception as exc:  # noqa: BLE001 - reported, never raised: the DSN stays secret
            mapped = to_tool_error(exc, self._host)
            return CredentialReport(False, [f"{mapped.code.value}: {mapped.message}"])
        try:
            return await self._verify(conn, provider)
        except Exception as exc:  # noqa: BLE001
            mapped = to_tool_error(exc, self._host)
            return CredentialReport(False, [f"{mapped.code.value}: {mapped.message}"])
        finally:
            await conn.close()

    async def _verify(
        self, conn: psycopg.AsyncConnection[Any], provider: EmbeddingProvider
    ) -> CredentialReport:
        tx = ReadTx(conn)
        reasons: list[str] = []
        warnings: list[str] = []
        fatal = False
        schema = (await tx.fetch("schema_info"))[0]
        if not (schema["has_chunks"] and schema["has_documents"]):
            return CredentialReport(
                False, ["schema kb is not migrated: run `mcp-ingest db upgrade` first"]
            )
        # The session default, as the role configures it (NOT inside our own READ ONLY txn).
        read_only_default = (await tx.fetch("show_read_only"))[0]["transaction_read_only"]
        role = (await tx.fetch("role_info"))[0]
        if read_only_default != "on":
            reasons.append(
                f"role '{role['role']}' is not read-only by default (default_transaction_read_only"
                "=off): MCP_PGVECTOR_DSN must use the mcp_query_ro role"
            )
            fatal = True
        if role["is_superuser"]:
            reasons.append(f"role '{role['role']}' is a superuser; use the mcp_query_ro role")
            fatal = True
        granted = [label for key, label in _WRITE_PRIVILEGES if role[key]]
        if granted:
            reasons.append(
                f"role '{role['role']}' has write privileges ({', '.join(granted)}): this looks "
                "like the mcp_ingest_rw DSN; MCP_PGVECTOR_DSN must be the read-only mcp_query_ro"
            )
            fatal = True
        version_rows = await tx.fetch("extension_version")
        version_text = version_rows[0]["extversion"] if version_rows else None
        if parse_version(version_text) is None:
            warnings.append("pgvector extension version could not be read")
        elif not Capabilities(parse_version(version_text)).supports_iterative_scan:
            warnings.append(
                f"pgvector {version_text} < 0.8: no hnsw.iterative_scan; filtered searches use "
                "over-fetch (top_k x 4) and can miss matches behind narrow filters"
            )
        stored = {r["embedding_model"] for r in await tx.fetch("stored_models")}
        dims = (await tx.fetch("embedding_dimensions"))[0]["dimensions"]
        try:
            validate_stored_embeddings(provider, stored_models=stored, stored_dimensions=dims)
        except EmbeddingModelMismatchError as exc:
            reasons.append(str(exc))
            fatal = True
        if not stored:
            warnings.append("kb.chunks is empty: nothing has been indexed yet (run mcp-ingest)")
        return CredentialReport(not reasons, reasons, warnings, fatal, role["role"], version_text)

    async def credential_check(self, provider: EmbeddingProvider) -> bool:
        """`mcp_common.runtime.serve(credential_check=...)` hook."""
        return (await self.verify_credentials(provider)).ok
