"""T-056 / T-057: numbered SQL migrations 0001-0006, the runner and `mcp-ingest db upgrade`.

Runs against a real, throw-away local PostgreSQL + pgvector (packages/conftest.py); skipped with
a reason when the server binaries or the extension are not installed.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import psycopg
import pytest
from mcp_common.contract_testing import load_contract
from mcp_ingest.cli import app
from mcp_ingest.db import (
    Migration,
    MigrationError,
    discover_migrations,
    migrations_dir,
    upgrade,
)
from typer.testing import CliRunner

ALL_VERSIONS = [
    "0001_extensions",
    "0002_schema_kb",
    "0003_indexes",
    "0004_ingest_state",
    "0005_roles",
    "0006_review_followup",
]


def _swap_user(dsn: str, user: str) -> str:
    return dsn.replace("postgres@", f"{user}@", 1)


def _columns(conn: psycopg.Connection, table: str) -> dict[str, tuple[str, str]]:
    rows = conn.execute(
        "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
        "WHERE table_schema = 'kb' AND table_name = %s",
        (table,),
    ).fetchall()
    return {name: (dtype, nullable) for name, dtype, nullable in rows}


# -- discovery ---------------------------------------------------------------------------------


def test_discovers_the_six_numbered_migrations_in_order() -> None:
    found = discover_migrations()
    assert [m.version for m in found] == ALL_VERSIONS
    assert all(len(m.checksum) == 64 and m.sql.strip() for m in found)


def test_migrations_dir_exists_and_holds_only_numbered_sql() -> None:
    names = sorted(p.name for p in migrations_dir().iterdir())
    assert names == [f"{v}.sql" for v in ALL_VERSIONS]


def test_discovery_rejects_a_badly_named_file(tmp_path: Path) -> None:
    (tmp_path / "0001_ok.sql").write_text("SELECT 1;")
    (tmp_path / "oops.sql").write_text("SELECT 1;")
    with pytest.raises(MigrationError, match="oops.sql"):
        discover_migrations(tmp_path)


def test_discovery_rejects_duplicate_numbers(tmp_path: Path) -> None:
    (tmp_path / "0001_a.sql").write_text("SELECT 1;")
    (tmp_path / "0001_b.sql").write_text("SELECT 1;")
    with pytest.raises(MigrationError, match="duplicate"):
        discover_migrations(tmp_path)


# -- runner ------------------------------------------------------------------------------------


def test_FR_012_AC_001_upgrade_applies_empty_database_up_to_0006(fresh_db: str) -> None:
    with psycopg.connect(fresh_db, autocommit=True) as conn:
        result = upgrade(conn)
        assert result.applied == ALL_VERSIONS
        assert result.already_applied == []
        assert result.current_version == "0006_review_followup"
        assert result.dry_run is False
        recorded = [
            r[0] for r in conn.execute("SELECT version FROM kb.schema_migrations ORDER BY version")
        ]
        assert recorded == ALL_VERSIONS


def test_FR_012_AC_001_upgrade_is_idempotent(fresh_db: str) -> None:
    with psycopg.connect(fresh_db, autocommit=True) as conn:
        upgrade(conn)
        before = _columns(conn, "documents"), _columns(conn, "chunks")
        again = upgrade(conn)
        assert again.applied == []
        assert again.already_applied == ALL_VERSIONS
        assert again.current_version == "0006_review_followup"
        assert (_columns(conn, "documents"), _columns(conn, "chunks")) == before


def test_upgrade_resumes_from_a_partially_migrated_database(fresh_db: str) -> None:
    first_five = discover_migrations()[:5]
    with psycopg.connect(fresh_db, autocommit=True) as conn:
        partial = upgrade(conn, migrations=first_five)
        assert partial.current_version == "0005_roles"
        rest = upgrade(conn)
        assert rest.applied == ["0006_review_followup"]
        assert rest.already_applied == ALL_VERSIONS[:5]


def test_dry_run_on_an_empty_database_reports_pending_and_changes_nothing(fresh_db: str) -> None:
    with psycopg.connect(fresh_db, autocommit=True) as conn:
        result = upgrade(conn, dry_run=True)
        assert result.dry_run is True and result.applied == ALL_VERSIONS
        assert result.current_version is None
        assert conn.execute("SELECT to_regnamespace('kb')").fetchone()[0] is None


def test_dry_run_after_upgrade_has_nothing_to_apply(fresh_db: str) -> None:
    with psycopg.connect(fresh_db, autocommit=True) as conn:
        upgrade(conn)
        result = upgrade(conn, dry_run=True)
        assert result.applied == [] and result.current_version == "0006_review_followup"


def test_a_failing_migration_rolls_back_and_is_not_recorded(fresh_db: str) -> None:
    good = discover_migrations()[:2]
    bad = Migration("0003_broken", "CREATE TABLE kb.half (x int); SELECT 1/0;", "f" * 64)
    with psycopg.connect(fresh_db, autocommit=True) as conn:
        with pytest.raises(MigrationError, match="0003_broken"):
            upgrade(conn, migrations=[*good, bad])
        assert conn.execute("SELECT to_regclass('kb.half')").fetchone()[0] is None
        versions = [r[0] for r in conn.execute("SELECT version FROM kb.schema_migrations")]
        assert versions == ["0001_extensions", "0002_schema_kb"]


def test_an_edited_applied_migration_is_refused(fresh_db: str) -> None:
    with psycopg.connect(fresh_db, autocommit=True) as conn:
        upgrade(conn)
        tampered = [
            Migration(m.version, m.sql + "\n-- edited", "0" * 64)
            if m.version == "0002_schema_kb"
            else m
            for m in discover_migrations()
        ]
        with pytest.raises(MigrationError, match="checksum"):
            upgrade(conn, migrations=tampered)


def test_a_database_ahead_of_the_code_is_refused(fresh_db: str) -> None:
    with psycopg.connect(fresh_db, autocommit=True) as conn:
        upgrade(conn)
        with pytest.raises(MigrationError, match="not known to this version"):
            upgrade(conn, migrations=discover_migrations()[:3])


# -- schema shape (architecture.md ER diagram) -------------------------------------------------


def test_FR_012_AC_001_documents_and_chunks_match_the_er_diagram(migrated_db: str) -> None:
    with psycopg.connect(migrated_db) as conn:
        docs = _columns(conn, "documents")
        chunks = _columns(conn, "chunks")
        assert set(docs) == {
            "id", "source_type", "source_id", "source_uri", "title", "container", "author",
            "content_hash", "source_updated_at", "ingested_at", "deleted_at",
            "last_seen_run_id", "last_seen_at", "chunk_config_hash", "visibility", "metadata",
        }  # fmt: skip
        assert set(chunks) == {
            "id", "document_id", "chunk_index", "content", "token_count", "heading_path",
            "embedding", "embedding_model", "embedded_at",
        }  # fmt: skip
        assert docs["visibility"][1] == "NO" and docs["source_uri"][1] == "NO"
        assert (
            conn.execute(
                "SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
                "WHERE attrelid = 'kb.chunks'::regclass AND attname = 'embedding'"
            ).fetchone()[0]
            == "vector(1024)"
        )


def test_FR_012_AC_003_unique_constraints_make_reingest_an_update(migrated_db: str) -> None:
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        insert = (
            "INSERT INTO kb.documents (source_type, source_id, source_uri, content_hash) "
            "VALUES ('confluence', '1', 'https://w/1', 'h')"
        )
        conn.execute(insert)
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(insert)
        doc_id = conn.execute("SELECT id FROM kb.documents").fetchone()[0]
        vec = "[" + ",".join(["0.1"] * 1024) + "]"
        chunk = (
            "INSERT INTO kb.chunks (document_id, chunk_index, content, embedding, embedding_model)"
            " VALUES (%s, 0, 'c', %s, 'm')"
        )
        conn.execute(chunk, (doc_id, vec))
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(chunk, (doc_id, vec))


def test_chunks_are_deleted_with_their_document(migrated_db: str) -> None:
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        doc_id = conn.execute(
            "INSERT INTO kb.documents (source_type, source_id, source_uri, content_hash) "
            "VALUES ('gitlab', 'p:1', 'https://g/1', 'h') RETURNING id"
        ).fetchone()[0]
        vec = "[" + ",".join(["0.2"] * 1024) + "]"
        conn.execute(
            "INSERT INTO kb.chunks (document_id, chunk_index, content, embedding, embedding_model)"
            " VALUES (%s, 0, 'c', %s, 'm')",
            (doc_id, vec),
        )
        conn.execute("DELETE FROM kb.documents WHERE id = %s", (doc_id,))
        assert conn.execute("SELECT count(*) FROM kb.chunks").fetchone()[0] == 0


def test_hnsw_cosine_index_has_the_adr_0011_parameters(migrated_db: str) -> None:
    with psycopg.connect(migrated_db) as conn:
        defs = [
            r[0]
            for r in conn.execute(
                "SELECT indexdef FROM pg_indexes WHERE schemaname = 'kb' AND tablename = 'chunks'"
            )
        ]
    hnsw = next(d for d in defs if "hnsw" in d)
    assert "vector_cosine_ops" in hnsw
    assert re.search(r"m='?16'?", hnsw) and re.search(r"ef_construction='?64'?", hnsw)
    assert any("(document_id)" in d for d in defs)


def test_FR_012_AC_003_visibility_defaults_to_team_and_rejects_unknown_labels(
    migrated_db: str,
) -> None:
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO kb.documents (source_type, source_id, source_uri, content_hash) "
            "VALUES ('confluence', 'a', 'https://w/a', 'h')"
        )
        assert conn.execute("SELECT visibility FROM kb.documents").fetchone()[0] == "team"
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute("UPDATE kb.documents SET visibility = 'secret'")
        conn.execute("UPDATE kb.documents SET visibility = 'restricted'")


def test_FR_012_AC_002_the_scoped_tombstone_statement_of_adr_0011_a1_runs(
    migrated_db: str,
) -> None:
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        run_old, run_new = (
            conn.execute(
                "INSERT INTO kb.ingest_runs (source_type) VALUES ('confluence') RETURNING id"
            ).fetchone()[0]
            for _ in range(2)
        )
        for source_id, run in (("seen", run_new), ("stale", run_old), ("never", None)):
            conn.execute(
                "INSERT INTO kb.documents (source_type, source_id, source_uri, content_hash, "
                "last_seen_run_id) VALUES ('confluence', %s, 'https://w/x', 'h', %s)",
                (source_id, run),
            )
        conn.execute(  # a different source must never be touched
            "INSERT INTO kb.documents (source_type, source_id, source_uri, content_hash) "
            "VALUES ('gitlab', 'other', 'https://g/x', 'h')"
        )
        updated = conn.execute(
            "UPDATE kb.documents SET deleted_at = now() WHERE source_type = %s "
            "AND last_seen_run_id IS DISTINCT FROM %s AND deleted_at IS NULL",
            ("confluence", run_new),
        ).rowcount
        assert updated == 2
        tombstoned = {
            r[0]
            for r in conn.execute("SELECT source_id FROM kb.documents WHERE deleted_at IS NOT NULL")
        }
        assert tombstoned == {"stale", "never"}


def test_ingest_failures_table_matches_the_er_diagram(migrated_db: str) -> None:
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        cols = _columns(conn, "ingest_failures")
        assert set(cols) == {
            "source_type", "source_id", "first_seen_at", "last_attempt_at", "attempts", "stage",
            "code", "last_error",
        }  # fmt: skip
        conn.execute(
            "INSERT INTO kb.ingest_failures (source_type, source_id, stage, code) "
            "VALUES ('gitlab', 'p:.env', 'redact', 'blocked_by_policy')"
        )
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(
                "INSERT INTO kb.ingest_failures (source_type, source_id, stage, code) "
                "VALUES ('gitlab', 'p:.env', 'redact', 'blocked_by_policy')"
            )
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO kb.ingest_failures (source_type, source_id, stage, code) "
                "VALUES ('gitlab', 'x', 'not-a-stage', 'c')"
            )


def test_run_and_state_tables_exist_with_the_er_columns(migrated_db: str) -> None:
    with psycopg.connect(migrated_db) as conn:
        assert set(_columns(conn, "ingest_runs")) == {
            "id", "source_type", "started_at", "finished_at", "status", "documents_seen",
            "documents_upserted", "documents_skipped", "documents_failed", "chunks_written",
            "error_summary",
        }  # fmt: skip
        assert set(_columns(conn, "ingest_source_state")) == {
            "source_type", "cursor", "last_success_at", "last_run_id",
        }  # fmt: skip


# -- roles (ADR-0003 A1) -----------------------------------------------------------------------


def test_FR_011_AC_003_mcp_query_ro_cannot_write_even_when_read_only_is_switched_off(
    migrated_db: str,
) -> None:
    dsn = _swap_user(migrated_db, "mcp_query_ro")
    with psycopg.connect(dsn, autocommit=True) as conn:
        assert conn.execute("SHOW transaction_read_only").fetchone()[0] == "on"
        assert conn.execute("SELECT count(*) FROM kb.documents").fetchone()[0] == 0
        conn.execute("SET default_transaction_read_only = off")  # privilege must still stop writes
        for statement in (
            "INSERT INTO kb.documents (source_type, source_id, source_uri, content_hash) "
            "VALUES ('x', 'y', 'https://z', 'h')",
            "UPDATE kb.documents SET title = 't'",
            "DELETE FROM kb.documents",
            "DELETE FROM kb.chunks",
            "INSERT INTO kb.ingest_failures (source_type, source_id, stage, code) "
            "VALUES ('a', 'b', 'redact', 'c')",
            "TRUNCATE kb.chunks",
            "DROP TABLE kb.chunks",
            "CREATE TABLE kb.evil (x int)",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(statement)


def test_FR_011_AC_003_mcp_query_ro_privileges_are_select_only(migrated_db: str) -> None:
    with psycopg.connect(_swap_user(migrated_db, "mcp_query_ro")) as conn:
        for table in ("documents", "chunks", "ingest_runs", "ingest_source_state",
                      "ingest_failures", "schema_migrations"):  # fmt: skip
            row = conn.execute(
                "SELECT has_table_privilege(%s, 'SELECT'), has_table_privilege(%s, 'INSERT'), "
                "has_table_privilege(%s, 'UPDATE'), has_table_privilege(%s, 'DELETE')",
                (f"kb.{table}",) * 4,
            ).fetchone()
            assert row == (True, False, False, False), table


def test_mcp_ingest_rw_can_write_data_but_not_alter_the_schema(migrated_db: str) -> None:
    with psycopg.connect(_swap_user(migrated_db, "mcp_ingest_rw"), autocommit=True) as conn:
        assert conn.execute("SHOW transaction_read_only").fetchone()[0] == "off"
        conn.execute(
            "INSERT INTO kb.documents (source_type, source_id, source_uri, content_hash) "
            "VALUES ('confluence', 'rw', 'https://w/rw', 'h')"
        )
        conn.execute("INSERT INTO kb.ingest_runs (source_type) VALUES ('confluence')")
        conn.execute(
            "INSERT INTO kb.ingest_failures (source_type, source_id, stage, code) "
            "VALUES ('gitlab', 'x', 'redact', 'blocked_by_policy')"
        )
        for statement in (
            "DROP TABLE kb.chunks",
            "INSERT INTO kb.schema_migrations (version, checksum) VALUES ('9999_x', 'x')",
            "CREATE EXTENSION IF NOT EXISTS hstore",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(statement)


# -- CLI ---------------------------------------------------------------------------------------


def _strip_secret_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("MCP_INGEST_ADMIN_DSN", "MCP_INGEST_PGVECTOR_DSN"):
        monkeypatch.delenv(var, raising=False)


def test_FR_012_AC_001_cli_db_upgrade_json_matches_the_contract(
    fresh_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jsonschema import Draft202012Validator

    _strip_secret_env(monkeypatch)
    monkeypatch.setenv("MCP_INGEST_ADMIN_DSN", fresh_db)
    contract = load_contract()
    schema = {"components": contract["components"], "$ref": "#/components/schemas/MigrationResult"}
    runner = CliRunner()
    for expected_applied in (ALL_VERSIONS, []):
        result = runner.invoke(app, ["db", "upgrade", "--json"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.stdout)
        assert list(Draft202012Validator(schema).iter_errors(payload)) == []
        assert payload["applied"] == expected_applied
        assert payload["current_version"] == "0006_review_followup"


def test_cli_db_upgrade_dry_run_flag(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    _strip_secret_env(monkeypatch)
    monkeypatch.setenv("MCP_INGEST_ADMIN_DSN", fresh_db)
    result = CliRunner().invoke(app, ["db", "upgrade", "--dry-run", "--json"])
    payload = json.loads(result.stdout)
    assert result.exit_code == 0 and payload["dry_run"] is True
    assert payload["applied"] == ALL_VERSIONS
    with psycopg.connect(fresh_db) as conn:
        assert conn.execute("SELECT to_regnamespace('kb')").fetchone()[0] is None


def test_cli_db_upgrade_prints_human_text_without_json_flag(
    fresh_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _strip_secret_env(monkeypatch)
    monkeypatch.setenv("MCP_INGEST_ADMIN_DSN", fresh_db)
    result = CliRunner().invoke(app, ["db", "upgrade"])
    assert result.exit_code == 0
    assert "0006_review_followup" in result.stdout


def test_cli_db_upgrade_without_a_dsn_exits_2_and_names_the_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _strip_secret_env(monkeypatch)
    result = CliRunner().invoke(app, ["db", "upgrade"])
    assert result.exit_code == 2
    assert "MCP_INGEST_ADMIN_DSN" in result.output


def test_cli_db_upgrade_reports_an_unreachable_database_without_the_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _strip_secret_env(monkeypatch)
    monkeypatch.setenv("MCP_INGEST_ADMIN_DSN", "postgresql://u:s3cr3t@127.0.0.1:1/none")
    result = CliRunner().invoke(app, ["db", "upgrade"])
    assert result.exit_code == 2
    assert "s3cr3t" not in result.output


def test_cli_db_upgrade_falls_back_to_the_ingest_dsn(
    fresh_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _strip_secret_env(monkeypatch)
    monkeypatch.setenv("MCP_INGEST_PGVECTOR_DSN", fresh_db)
    assert CliRunner().invoke(app, ["db", "upgrade", "--json"]).exit_code == 0


def test_cli_db_upgrade_exits_2_on_a_migration_error(
    fresh_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import mcp_ingest.db as db_module

    _strip_secret_env(monkeypatch)
    monkeypatch.setenv("MCP_INGEST_ADMIN_DSN", fresh_db)
    monkeypatch.setattr(
        db_module, "discover_migrations",
        lambda *_a, **_k: [Migration("0001_bad", "SELECT 1/0;", "a" * 64)],
    )  # fmt: skip
    result = CliRunner().invoke(app, ["db", "upgrade"])
    assert result.exit_code == 2 and "0001_bad" in result.output
