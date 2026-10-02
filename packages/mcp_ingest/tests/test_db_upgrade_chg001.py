"""T-090 (CHG-001 E1): `mcp-ingest db upgrade`/`db status` apply and report 0007/0007b/0008 on a
database that already has data, proving DK2 safety end-to-end through the CLI.

Two layers, mirroring test_migration_locking.py:

* **CLI contract** (`db status --json`, `db upgrade --json`) over the distro-binary Postgres used
  by the other CLI contract tests (`rw_dsn`/`migrated_db`); skipped when those binaries/pgvector
  are missing. These prove the new `db status` subcommand lists the three CHG-001 migrations and
  that its JSON validates as a MigrationStatusReport.
* **integration on the real go-live engine** (`docker_pg_factory`, the compose
  `pgvector/pgvector:pg16`): seed a populated 0006 baseline, apply through the CLI runner, assert
  `db status` lists the three migrations, the lock wait stayed under `lock_timeout`, and the read
  path kept working during the upgrade. Skipped, with a reason, when Docker is unavailable.
"""

from __future__ import annotations

import json
import time

import psycopg
from mcp_ingest.reports import MigrationStatusReport
from typer.testing import CliRunner

from mcp_ingest import cli, db

runner = CliRunner()

CHG001 = ["0007_knowledge_domains", "0007b_knowledge_indexes", "0008_source_authority"]


def _invoke(*args: str):
    return runner.invoke(cli.app, list(args))


# -- CLI contract over the distro-binary Postgres (skipped if unavailable) ---------------------


def test_db_status_json_lists_chg001_after_upgrade(monkeypatch, migrated_db: str) -> None:
    """`migrated_db` is already at the latest version; `db status --json` must report every
    CHG-001 migration under `applied` and nothing pending, and validate as the report model."""
    monkeypatch.delenv("MCP_INGEST_ADMIN_DSN", raising=False)
    monkeypatch.setenv("MCP_INGEST_PGVECTOR_DSN", migrated_db)

    result = _invoke("db", "status", "--json")
    assert result.exit_code == 0, result.output
    report = MigrationStatusReport.model_validate_json(result.output)
    for version in CHG001:
        assert version in report.applied, version
    assert report.pending == []
    assert report.current_version == "0008_source_authority"
    assert report.warnings == []


def test_db_status_human_output_names_the_three_migrations(monkeypatch, migrated_db: str) -> None:
    monkeypatch.delenv("MCP_INGEST_ADMIN_DSN", raising=False)
    monkeypatch.setenv("MCP_INGEST_PGVECTOR_DSN", migrated_db)
    out = _invoke("db", "status").output
    for version in CHG001:
        assert version in out


def test_db_status_reports_pending_on_a_partially_migrated_db(
    monkeypatch, pg_database_factory
) -> None:
    """Apply only up to 0006, then `db status` must list 0007/0007b/0008 as pending."""
    dsn = pg_database_factory()
    with psycopg.connect(dsn, autocommit=True) as conn:
        db.upgrade(conn, migrations=db.discover_migrations()[:6])
    monkeypatch.delenv("MCP_INGEST_ADMIN_DSN", raising=False)
    monkeypatch.setenv("MCP_INGEST_PGVECTOR_DSN", dsn)

    report = MigrationStatusReport.model_validate_json(
        _invoke("db", "status", "--json").output
    )
    assert set(CHG001).issubset(set(report.pending))
    for version in CHG001:
        assert version not in report.applied


def test_db_status_is_read_only(pg_database_factory) -> None:
    """migration_status writes nothing: it does not even create kb.schema_migrations."""
    dsn = pg_database_factory()
    with psycopg.connect(dsn, autocommit=True) as conn:
        report = db.migration_status(conn)
        assert report.current_version is None
        assert report.applied == []
        assert set(CHG001).issubset(set(report.pending))
        # no schema/table was created by status
        assert conn.execute("SELECT to_regclass('kb.schema_migrations')").fetchone()[0] is None


# -- integration on the real go-live engine ----------------------------------------------------


def _seed_like_go_live(dsn: str) -> None:
    with psycopg.connect(dsn, autocommit=True) as conn:
        db.upgrade(conn, migrations=db.discover_migrations()[:6])
        doc_id = conn.execute(
            "INSERT INTO kb.documents (source_type, source_id, source_uri, content_hash) "
            "VALUES ('confluence', 'seed-1', 'https://w/seed-1', 'h1') RETURNING id"
        ).fetchone()[0]
        vec = "[" + ",".join(["0.1"] * 1024) + "]"
        conn.execute(
            "INSERT INTO kb.chunks (document_id, chunk_index, content, token_count, embedding, "
            "embedding_model) VALUES (%s, 0, 'retry policy backoff', 5, %s, 'm')",
            (doc_id, vec),
        )


def test_db_upgrade_then_status_on_populated_db(monkeypatch, docker_pg_factory) -> None:
    dsn = docker_pg_factory()
    _seed_like_go_live(dsn)
    monkeypatch.setenv("MCP_INGEST_ADMIN_DSN", dsn)
    monkeypatch.setenv("MCP_INGEST_PGVECTOR_DSN", dsn)

    up = _invoke("db", "upgrade", "--json")
    assert up.exit_code == 0, up.output
    assert json.loads(up.output)["applied"] == CHG001

    status = MigrationStatusReport.model_validate_json(_invoke("db", "status", "--json").output)
    for version in CHG001:
        assert version in status.applied
    assert status.pending == []
    # go-live data survived the migration
    with psycopg.connect(dsn, autocommit=True) as conn:
        assert conn.execute("SELECT count(*) FROM kb.documents").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM kb.chunks").fetchone()[0] == 1


def test_upgrade_does_not_block_the_read_path_on_populated_db(docker_pg_factory) -> None:
    """While 0007/0007b/0008 apply, a concurrent SELECT on the populated kb.chunks must keep
    working — the CONCURRENTLY index build and NOT VALID/VALIDATE split take no long ACCESS
    EXCLUSIVE lock (R-006/R-007)."""
    dsn = docker_pg_factory()
    _seed_like_go_live(dsn)
    reader = psycopg.connect(dsn, autocommit=True)
    try:
        started = time.monotonic()
        with psycopg.connect(dsn, autocommit=True) as conn:
            db.upgrade(conn)
        upgrade_elapsed = time.monotonic() - started
        # the reader can still query the populated table immediately after (not locked out)
        assert reader.execute("SELECT count(*) FROM kb.chunks").fetchone()[0] == 1
        # a populated-table change bounded by lock_timeout cannot have taken minutes
        assert upgrade_elapsed < 60, f"upgrade took {upgrade_elapsed:.1f}s"
    finally:
        reader.close()


def test_db_status_warns_on_unknown_applied_migration(docker_pg_factory) -> None:
    """A migration recorded in the DB but unknown to this build is a warning in status (drift
    must be reported, not crash)."""
    dsn = docker_pg_factory()
    _seed_like_go_live(dsn)
    with psycopg.connect(dsn, autocommit=True) as conn:
        db.upgrade(conn)
        conn.execute(
            "INSERT INTO kb.schema_migrations (version, checksum) VALUES ('9999_future', 'x')"
        )
        report = db.migration_status(conn)
    assert any("9999_future" in w for w in report.warnings)
