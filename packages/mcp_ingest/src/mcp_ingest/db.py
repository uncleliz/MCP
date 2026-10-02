"""Numbered SQL migration runner for schema `kb` (T-056/T-057, ADR-0011; T-087 DK2 locking).

* Migrations are `migrations/NNNN_name.sql`, applied in order, each in its own transaction
  together with the row in `kb.schema_migrations` (so a failure leaves nothing half-applied).
* Idempotent: re-running applies nothing. An already-applied file whose checksum changed, or a
  database that is ahead of the code, is refused instead of silently drifting.
* Concurrency: a session-level advisory lock serialises two `db upgrade` runs.

Safe on a *populated* database (ADR-0022 DK2, R-006/R-007 — CHG-001 schema changes run on the
pgvector store that is already go-live with data):

* A migration file named `NNNN_name.concurrently.sql` runs **outside** any transaction, in
  autocommit, so it may contain `CREATE INDEX CONCURRENTLY` (which Postgres forbids inside a
  transaction block). Its statements are executed one at a time; the `kb.schema_migrations` row is
  recorded afterwards in its own statement. If a `CREATE INDEX CONCURRENTLY` leaves an *invalid*
  index behind (it failed partway, no automatic rollback), the runner drops that invalid index and
  retries once before giving up.
* Ordinary (transactional) migrations should add constraints with `ADD CONSTRAINT ... NOT VALID`
  and `VALIDATE CONSTRAINT` in a *separate* statement, and `ADD COLUMN` with a constant default, so
  they never take a long full-table `ACCESS EXCLUSIVE` lock on live data.
* `lock_timeout` + `statement_timeout` are set at the start of every migration so a blocked
  migration fails fast instead of queuing behind — and ahead of — live read/write traffic.
* `ALTER COLUMN ... TYPE` (an in-place table rewrite under `ACCESS EXCLUSIVE`) is refused.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

import psycopg

from mcp_ingest.reports import MigrationResult, MigrationStatusReport

__all__ = [
    "Migration",
    "MigrationError",
    "connect_for_migration",
    "discover_migrations",
    "migration_status",
    "migrations_dir",
    "upgrade",
]

_NAME = re.compile(r"^(\d{4}[a-z]?)_[a-z0-9_]+(?P<concurrently>\.concurrently)?\.sql$")
_LOCK_KEY = "mcp-ingest:migrate"

# DK2 (R-006/R-007): a blocked migration must fail fast rather than queue behind — and ahead of —
# live read/write traffic. Short, overridable only by editing this file (never silently).
_LOCK_TIMEOUT = "3s"
_STATEMENT_TIMEOUT = "60s"

# ALTER COLUMN ... TYPE rewrites the whole table under ACCESS EXCLUSIVE; forbidden on live data.
_ALTER_COLUMN_TYPE = re.compile(r"\balter\s+column\s+\w+\s+(?:set\s+data\s+)?type\b", re.IGNORECASE)


class MigrationError(RuntimeError):
    """A migration could not be applied or the history is inconsistent."""


@dataclass(frozen=True)
class Migration:
    version: str  # logical version, e.g. "0002_schema_kb" or "0007b_knowledge_indexes"
    sql: str
    checksum: str  # sha256 of the file content
    concurrently: bool = False  # run outside a transaction (CREATE INDEX CONCURRENTLY), DK2


def migrations_dir() -> Path:
    """`migrations/` shipped inside the wheel, or the source-tree sibling in a checkout."""
    here = Path(__file__).resolve().parent
    for candidate in (here / "migrations", here.parents[1] / "migrations"):
        if candidate.is_dir():
            return candidate
    raise MigrationError("migrations directory not found")


def _version_of(path: Path) -> str:
    """Logical version string, stripping the optional `.concurrently` marker from the stem.

    `0007b_knowledge_indexes.concurrently.sql` -> `0007b_knowledge_indexes` so the recorded
    version is stable whether or not a migration runs concurrently.
    """
    stem = path.name[: -len(".sql")]
    if stem.endswith(".concurrently"):
        stem = stem[: -len(".concurrently")]
    return stem


def discover_migrations(directory: Path | None = None) -> list[Migration]:
    root = directory or migrations_dir()
    found: list[Migration] = []
    seen: dict[str, str] = {}
    for path in sorted(root.iterdir()):
        if path.name.startswith(".") or not path.is_file():
            continue
        match = _NAME.match(path.name)
        if match is None:
            raise MigrationError(f"unexpected file in migrations dir: {path.name}")
        number = match.group(1)
        if number in seen:
            raise MigrationError(
                f"duplicate migration number {number}: {seen[number]}, {path.name}"
            )
        seen[number] = path.name
        text = path.read_text(encoding="utf-8")
        found.append(
            Migration(
                version=_version_of(path),
                sql=text,
                checksum=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                concurrently=match.group("concurrently") is not None,
            )
        )
    return found


def connect_for_migration(dsn: str) -> psycopg.Connection:
    """Autocommit connection (the runner opens its own transactions)."""
    return psycopg.connect(dsn, autocommit=True, connect_timeout=10)


def _applied(conn: psycopg.Connection) -> dict[str, str]:
    exists = conn.execute("SELECT to_regclass('kb.schema_migrations')").fetchone()
    if exists is None or exists[0] is None:
        return {}
    return {
        version: checksum
        for version, checksum in conn.execute("SELECT version, checksum FROM kb.schema_migrations")
    }


def _split_statements(sql: str) -> list[str]:
    """Split a SQL script into statements on top-level `;`, honouring `$tag$...$tag$` quoting.

    Enough for our migrations (which use `$$ ... $$` DO blocks and dollar-quoted bodies); it is
    not a general SQL parser. Line (`--`) comments are stripped so a `;` inside a comment never
    ends a statement. Used only for `*.concurrently.sql` files, which must run one statement at a
    time outside a transaction.
    """
    out: list[str] = []
    buf: list[str] = []
    i = 0
    n = len(sql)
    dollar_tag: str | None = None
    while i < n:
        ch = sql[i]
        if dollar_tag is None and ch == "-" and sql[i : i + 2] == "--":
            # line comment: skip to end of line (keep the newline for readability)
            end = sql.find("\n", i)
            if end == -1:
                break
            i = end
            continue
        if ch == "$":
            match = re.match(r"\$[A-Za-z0-9_]*\$", sql[i:])
            if match:
                tag = match.group(0)
                if dollar_tag is None:
                    dollar_tag = tag
                elif dollar_tag == tag:
                    dollar_tag = None
                buf.append(tag)
                i += len(tag)
                continue
        if ch == ";" and dollar_tag is None:
            statement = "".join(buf).strip()
            if statement:
                out.append(statement)
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return out


def _reject_in_place_type_change(migration: Migration) -> None:
    if _ALTER_COLUMN_TYPE.search(migration.sql):
        raise MigrationError(
            f"migration {migration.version} uses ALTER COLUMN ... TYPE, an in-place table "
            "rewrite under ACCESS EXCLUSIVE; forbidden on a populated database (DK2). Add a new "
            "column with a constant default, backfill, then swap instead."
        )


def _invalid_indexes(conn: psycopg.Connection) -> set[str]:
    """Fully-qualified names of indexes in schema `kb` left `INVALID` by a failed CONCURRENTLY."""
    rows = conn.execute(
        "SELECT n.nspname || '.' || c.relname "
        "FROM pg_index x "
        "JOIN pg_class c ON c.oid = x.indexrelid "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'kb' AND x.indisvalid = false"
    ).fetchall()
    return {row[0] for row in rows}


def _set_timeouts(conn: psycopg.Connection) -> None:
    conn.execute(f"SET lock_timeout = '{_LOCK_TIMEOUT}'")
    conn.execute(f"SET statement_timeout = '{_STATEMENT_TIMEOUT}'")


def _apply_transactional(conn: psycopg.Connection, migration: Migration) -> None:
    """Ordinary migration: whole file + its history row in one transaction (atomic)."""
    with conn.transaction():
        _set_timeouts(conn)
        conn.execute(migration.sql.encode("utf-8"))
        conn.execute(
            "INSERT INTO kb.schema_migrations (version, checksum) VALUES (%s, %s)",
            (migration.version, migration.checksum),
        )


def _apply_concurrently(conn: psycopg.Connection, migration: Migration) -> None:
    """CONCURRENTLY migration: statements run one at a time in autocommit, **outside** any
    transaction (ADR-0022 DK2). An invalid index left behind by a failed `CREATE INDEX
    CONCURRENTLY` is dropped and the statement retried once before the migration is recorded."""
    # The connection is already autocommit (connect_for_migration / the test harness); make the
    # requirement explicit so a caller cannot wrap this in a transaction by mistake.
    if conn.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
        raise MigrationError(
            f"concurrent migration {migration.version} must run outside a transaction"
        )
    _set_timeouts(conn)
    before_invalid = _invalid_indexes(conn)
    for statement in _split_statements(migration.sql):
        try:
            conn.execute(statement.encode("utf-8"))
        except psycopg.Error:
            # A failed CREATE INDEX CONCURRENTLY is not rolled back: it may leave an INVALID
            # index. Drop any invalid index this migration newly created, then retry once.
            new_invalid = _invalid_indexes(conn) - before_invalid
            for name in new_invalid:
                conn.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
            conn.execute(statement.encode("utf-8"))  # retry once; a second failure propagates
    conn.execute(
        "INSERT INTO kb.schema_migrations (version, checksum) VALUES (%s, %s)",
        (migration.version, migration.checksum),
    )


def upgrade(
    conn: psycopg.Connection,
    *,
    dry_run: bool = False,
    migrations: list[Migration] | None = None,
) -> MigrationResult:
    """Apply pending migrations. `dry_run` reports what *would* be applied and writes nothing;
    its `current_version` is the version the database is at *before* the run."""
    available = migrations if migrations is not None else discover_migrations()
    by_version = {m.version: m for m in available}
    conn.execute("SELECT pg_advisory_lock(hashtext(%s))", (_LOCK_KEY,))
    try:
        if not dry_run:
            conn.execute("CREATE SCHEMA IF NOT EXISTS kb")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS kb.schema_migrations ("
                "version text PRIMARY KEY, checksum text NOT NULL, "
                "applied_at timestamptz NOT NULL DEFAULT now())"
            )
        applied = _applied(conn)
        unknown = sorted(set(applied) - set(by_version))
        if unknown:
            raise MigrationError(
                f"database has migrations not known to this version of mcp-ingest: "
                f"{', '.join(unknown)} (upgrade the code, do not downgrade the database)"
            )
        for version, checksum in applied.items():
            if by_version[version].checksum != checksum:
                raise MigrationError(
                    f"checksum mismatch for already-applied migration {version}: "
                    "the file was edited after it was applied; add a new migration instead"
                )
        pending = [m for m in available if m.version not in applied]
        current = max(applied) if applied else None
        done: list[str] = []
        for migration in pending:
            _reject_in_place_type_change(migration)
            if not dry_run:
                try:
                    if migration.concurrently:
                        _apply_concurrently(conn, migration)
                    else:
                        _apply_transactional(conn, migration)
                except psycopg.Error as exc:
                    raise MigrationError(
                        f"migration {migration.version} failed: {type(exc).__name__}: "
                        f"{str(exc).splitlines()[0] if str(exc) else ''}"
                    ) from exc
                current = migration.version
            done.append(migration.version)
        return MigrationResult(
            applied=done,
            already_applied=sorted(applied),
            current_version=current,
            dry_run=dry_run,
        )
    finally:
        conn.execute("SELECT pg_advisory_unlock(hashtext(%s))", (_LOCK_KEY,))


def migration_status(
    conn: psycopg.Connection,
    *,
    migrations: list[Migration] | None = None,
) -> MigrationStatusReport:
    """Read-only view of migration state: which numbered migrations are applied vs pending.

    Writes nothing (no advisory lock, no schema creation) so it is safe to run with a non-DDL
    connection. Used by `mcp-ingest db status` (T-090) to confirm the CHG-001 migrations
    (0007/0007b/0008) have landed on a populated database. An applied migration unknown to this
    build, or an applied migration whose file checksum changed, surfaces as a warning rather than
    an exception — `status` must report drift, not refuse to run.
    """
    available = migrations if migrations is not None else discover_migrations()
    by_version = {m.version: m for m in available}
    applied = _applied(conn)
    warnings: list[str] = []
    for version in sorted(set(applied) - set(by_version)):
        warnings.append(
            f"database has migration {version} not known to this build of mcp-ingest"
        )
    for version, checksum in applied.items():
        known = by_version.get(version)
        if known is not None and known.checksum != checksum:
            warnings.append(f"checksum mismatch for applied migration {version} (file edited?)")
    pending = [m.version for m in available if m.version not in applied]
    return MigrationStatusReport(
        current_version=max(applied) if applied else None,
        applied=sorted(applied),
        pending=pending,
        warnings=warnings,
    )
