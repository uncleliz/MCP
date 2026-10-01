"""Numbered SQL migration runner for schema `kb` (T-056/T-057, ADR-0011).

* Migrations are `migrations/NNNN_name.sql`, applied in order, each in its own transaction
  together with the row in `kb.schema_migrations` (so a failure leaves nothing half-applied).
* Idempotent: re-running applies nothing. An already-applied file whose checksum changed, or a
  database that is ahead of the code, is refused instead of silently drifting.
* Concurrency: a session-level advisory lock serialises two `db upgrade` runs.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

import psycopg

from mcp_ingest.reports import MigrationResult

__all__ = [
    "Migration",
    "MigrationError",
    "connect_for_migration",
    "discover_migrations",
    "migrations_dir",
    "upgrade",
]

_NAME = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")
_LOCK_KEY = "mcp-ingest:migrate"


class MigrationError(RuntimeError):
    """A migration could not be applied or the history is inconsistent."""


@dataclass(frozen=True)
class Migration:
    version: str  # file stem, e.g. "0002_schema_kb"
    sql: str
    checksum: str  # sha256 of the file content


def migrations_dir() -> Path:
    """`migrations/` shipped inside the wheel, or the source-tree sibling in a checkout."""
    here = Path(__file__).resolve().parent
    for candidate in (here / "migrations", here.parents[1] / "migrations"):
        if candidate.is_dir():
            return candidate
    raise MigrationError("migrations directory not found")


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
        found.append(Migration(path.stem, text, hashlib.sha256(text.encode("utf-8")).hexdigest()))
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
            if not dry_run:
                try:
                    with conn.transaction():
                        conn.execute(migration.sql.encode("utf-8"))
                        conn.execute(
                            "INSERT INTO kb.schema_migrations (version, checksum) VALUES (%s, %s)",
                            (migration.version, migration.checksum),
                        )
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
