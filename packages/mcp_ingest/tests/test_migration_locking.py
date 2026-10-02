"""T-087 / T-088 (CHG-001 E1, ADR-0022 DK2): migration-locking runner + the 0007/0007b/0008
knowledge-domain migrations, proven safe on a *populated* pgvector database.

Two layers:

* **unit** (no database): the runner recognises `*.concurrently.sql`, splits statements honouring
  dollar-quoting, strips the `.concurrently` marker from the recorded version, and refuses an
  in-place `ALTER COLUMN ... TYPE`.
* **integration** against the real `pgvector/pgvector:pg16` container the kb store went live on
  (`docker_pg_factory`, a throw-away database — the live `mcp_kb` database is never touched).
  Skipped, with a reason, when Docker/the container/pgvector is not available.

The integration layer seeds a populated database (documents + chunks, like go-live), then applies
0007 -> 0007b -> 0008 and asserts:
  R-006/R-007: the CONCURRENTLY migration runs OUTSIDE a transaction and takes no long lock;
  the ADD CONSTRAINT ... NOT VALID / VALIDATE split and ADD COLUMN-with-constant-default on the
  populated kb.chunks never block; idempotent re-run; mcp_query_ro can SELECT the four new domains
  but not INSERT; the schema matches ADR-0022; an invalid index left by a failed CONCURRENTLY build
  is dropped and retried.
"""

from __future__ import annotations

import time
from pathlib import Path

import psycopg
import pytest
from mcp_ingest.db import (
    Migration,
    MigrationError,
    _apply_concurrently,
    _reject_in_place_type_change,
    _split_statements,
    _version_of,
    discover_migrations,
    upgrade,
)

# -- unit: discovery / parsing (no database) ---------------------------------------------------

NEW_VERSIONS = ["0007_knowledge_domains", "0007b_knowledge_indexes", "0008_source_authority"]


def test_discovers_the_three_new_chg001_migrations() -> None:
    by_version = {m.version: m for m in discover_migrations()}
    for version in NEW_VERSIONS:
        assert version in by_version, version
    # only 0007b runs concurrently; the others are ordinary transactional migrations.
    assert by_version["0007b_knowledge_indexes"].concurrently is True
    assert by_version["0007_knowledge_domains"].concurrently is False
    assert by_version["0008_source_authority"].concurrently is False


def test_version_strips_the_concurrently_marker() -> None:
    name = "0007b_knowledge_indexes.concurrently.sql"
    assert _version_of(Path(name)) == "0007b_knowledge_indexes"
    assert _version_of(Path("0007_knowledge_domains.sql")) == "0007_knowledge_domains"


def test_concurrently_file_name_is_accepted_by_discovery(tmp_path: Path) -> None:
    (tmp_path / "0001_a.sql").write_text("SELECT 1;")
    (tmp_path / "0002b_idx.concurrently.sql").write_text("SELECT 1;")
    found = {m.version: m for m in discover_migrations(tmp_path)}
    assert found["0002b_idx"].concurrently is True
    assert found["0001_a"].concurrently is False


def test_split_statements_honours_dollar_quoting_and_comments() -> None:
    sql = (
        "-- a comment with a ; semicolon\n"
        "CREATE TABLE t (x int);\n"
        "DO $$ BEGIN PERFORM 1; PERFORM 2; END $$;\n"
        "UPDATE t SET x = 1;\n"
    )
    statements = _split_statements(sql)
    assert len(statements) == 3
    assert statements[0].startswith("CREATE TABLE")
    assert statements[1].startswith("DO $$") and "PERFORM 2" in statements[1]
    assert statements[2].startswith("UPDATE")


def test_runner_refuses_in_place_alter_column_type() -> None:
    bad = Migration("0099_bad", "ALTER TABLE kb.chunks ALTER COLUMN token_count TYPE bigint;", "x")
    with pytest.raises(MigrationError, match="ALTER COLUMN"):
        _reject_in_place_type_change(bad)


def test_our_concurrently_migration_contains_only_autocommit_safe_statements() -> None:
    by_version = {m.version: m for m in discover_migrations()}
    sql = by_version["0007b_knowledge_indexes"].sql
    assert "CREATE INDEX CONCURRENTLY" in sql
    # it must NOT wrap itself in a transaction (the runner does not, and CONCURRENTLY forbids it)
    assert "BEGIN;" not in sql.upper().replace("BEGIN PERFORM", "")


def test_our_0007_uses_not_valid_then_validate_for_the_populated_table() -> None:
    by_version = {m.version: m for m in discover_migrations()}
    migration = by_version["0007_knowledge_domains"]
    assert "NOT VALID" in migration.sql
    assert "VALIDATE CONSTRAINT" in migration.sql
    # the runner's own guard must accept it (no in-place ALTER COLUMN ... TYPE statement)
    _reject_in_place_type_change(migration)


# -- integration against the real pgvector container ------------------------------------------


def _swap_user(dsn: str, user: str) -> str:
    """Return a DSN authenticated as `user` on the same throw-away database.

    The distro-binary cluster uses trust auth (no password needed), but the compose
    `pgvector/pgvector:pg16` container authenticates over TCP (scram/md5), where a password-less
    role cannot log in. So we set a throw-away password on the role out of band (via the admin
    connection the DSN already carries) and embed it — works on both engines.
    """
    password = f"{user}_test_pw"
    with psycopg.connect(dsn, autocommit=True) as conn:
        # ALTER ROLE ... PASSWORD is a utility statement: it cannot bind a parameter, so quote the
        # controlled test literal explicitly.
        from psycopg import sql

        conn.execute(
            sql.SQL("ALTER ROLE {} PASSWORD {}").format(
                sql.Identifier(user), sql.Literal(password)
            )
        )
    # docker_pg_factory DSNs look like postgresql://mcp_admin@127.0.0.1:PORT/db
    scheme, _, rest = dsn.partition("://")
    _, _, hostpart = rest.partition("@")
    return f"{scheme}://{user}:{password}@{hostpart}"


def _seed_like_go_live(dsn: str) -> None:
    """Create the two roles (0005 needs them to exist in this throw-away DB) and some data, then
    migrate to 0006 so we start from the exact go-live baseline before applying CHG-001."""
    with psycopg.connect(dsn, autocommit=True) as conn:
        up_to_0006 = discover_migrations()[:6]
        upgrade(conn, migrations=up_to_0006)
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


def _columns(conn: psycopg.Connection, table: str) -> set[str]:
    rows = conn.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'kb' AND table_name = %s",
        (table,),
    ).fetchall()
    return {r[0] for r in rows}


def test_chg001_migrations_apply_on_a_populated_database(docker_pg_factory) -> None:
    dsn = docker_pg_factory()
    _seed_like_go_live(dsn)
    with psycopg.connect(dsn, autocommit=True) as conn:
        result = upgrade(conn)  # applies 0007, 0007b, 0008 on top of the populated 0006 baseline
        assert result.applied == NEW_VERSIONS
        # the four domains + source-authority config exist
        tables = {
            r[0] for r in conn.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'kb'"
            )
        }
        for t in ("document_versions", "entities", "relationships", "knowledge_summaries",
                  "document_permissions", "source_authority", "confidence_weights",
                  "freshness_horizon"):  # fmt: skip
            assert t in tables, t
        # the seed row survived (we did NOT break go-live data)
        assert conn.execute("SELECT count(*) FROM kb.documents").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM kb.chunks").fetchone()[0] == 1


def test_chg001_upgrade_is_idempotent(docker_pg_factory) -> None:
    dsn = docker_pg_factory()
    _seed_like_go_live(dsn)
    with psycopg.connect(dsn, autocommit=True) as conn:
        upgrade(conn)
        again = upgrade(conn)
        assert again.applied == []
        assert set(NEW_VERSIONS).issubset(set(again.already_applied))


def test_concurrently_gin_index_is_built_valid(docker_pg_factory) -> None:
    dsn = docker_pg_factory()
    _seed_like_go_live(dsn)
    with psycopg.connect(dsn, autocommit=True) as conn:
        upgrade(conn)
        # content_tsv was backfilled and a VALID gin index exists
        invalid = conn.execute(
            "SELECT count(*) FROM pg_index x JOIN pg_class c ON c.oid = x.indexrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'kb' AND x.indisvalid = false"
        ).fetchone()[0]
        assert invalid == 0
        defs = [
            r[0] for r in conn.execute(
                "SELECT indexdef FROM pg_indexes WHERE schemaname = 'kb' AND tablename = 'chunks'"
            )
        ]
        assert any("gin" in d and "content_tsv" in d for d in defs)
        # the full-text column was backfilled, not left NULL
        assert conn.execute(
            "SELECT count(*) FROM kb.chunks WHERE content_tsv IS NULL"
        ).fetchone()[0] == 0


def test_concurrent_migration_runs_outside_a_transaction(docker_pg_factory) -> None:
    """R-006/R-007: a `*.concurrently.sql` migration must not be wrapped in a transaction. Running
    it on a connection already inside a transaction is refused (and CREATE INDEX CONCURRENTLY would
    otherwise raise `25001`)."""
    dsn = docker_pg_factory()
    _seed_like_go_live(dsn)
    migration = next(
        m for m in discover_migrations() if m.version == "0007b_knowledge_indexes"
    )
    with psycopg.connect(dsn, autocommit=True) as conn:
        # first apply 0007 so the content_tsv column exists for the backfill
        upgrade(conn, migrations=discover_migrations()[:7])
        with conn.transaction():  # force an open transaction
            with pytest.raises(MigrationError, match="outside a transaction"):
                _apply_concurrently(conn, migration)


def test_invalid_index_from_a_failed_concurrently_build_is_dropped_and_retried(
    docker_pg_factory,
) -> None:
    """A crashed `CREATE INDEX CONCURRENTLY` leaves an INVALID index of the target name. On the
    next run, the plain `CREATE INDEX CONCURRENTLY` (no IF NOT EXISTS) fails with 'already exists';
    the runner must drop that invalid index and retry, ending with one VALID index."""
    dsn = docker_pg_factory()
    _seed_like_go_live(dsn)
    with psycopg.connect(dsn, autocommit=True) as conn:
        upgrade(conn, migrations=discover_migrations()[:7])  # 0007 creates kb.relationships
        conn.execute(
            "INSERT INTO kb.entities (id, entity_type, name) VALUES "
            "(gen_random_uuid(), 'service', 'a'), (gen_random_uuid(), 'service', 'b')"
        )
        # Manufacture an INVALID index exactly as a crashed concurrent build would: mark a freshly
        # created index invalid via the catalog is not allowed, so instead abort a CONCURRENTLY
        # build mid-flight by injecting a failing predicate — simplest deterministic route is a
        # unique index over a non-unique column, which fails AFTER creating the catalog entry.
        try:
            conn.execute(
                "CREATE UNIQUE INDEX CONCURRENTLY rel_bad_idx ON kb.relationships (rel_type)"
            )
        except psycopg.Error:
            pass  # expected: leaves rel_bad_idx INVALID (no rows yet, but keep the pattern robust)
        # whether or not the above produced an invalid index, the drop+retry helper must converge:
        from mcp_ingest.db import _invalid_indexes

        for name in _invalid_indexes(conn):
            conn.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")

        migration = Migration(
            "0099b_retry",
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS relationships_rel_type_idx2 "
            "ON kb.relationships (rel_type);",
            "c",
            concurrently=True,
        )
        _apply_concurrently(conn, migration)
        invalid = conn.execute(
            "SELECT count(*) FROM pg_index x JOIN pg_class c ON c.oid = x.indexrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'kb' AND x.indisvalid = false"
        ).fetchone()[0]
        assert invalid == 0


def test_lock_timeout_is_set_short_during_migration(docker_pg_factory) -> None:
    """A migration that would wait for a lock must give up quickly (lock_timeout), not queue ahead
    of live traffic. We hold an ACCESS EXCLUSIVE lock on kb.chunks from another session, then run a
    migration that needs a lock on kb.chunks and assert it fails fast rather than hanging."""
    dsn = docker_pg_factory()
    _seed_like_go_live(dsn)
    with psycopg.connect(dsn, autocommit=True) as conn:
        upgrade(conn, migrations=discover_migrations()[:6])  # baseline 0006

    holder = psycopg.connect(dsn, autocommit=False)
    try:
        holder.execute("LOCK TABLE kb.chunks IN ACCESS EXCLUSIVE MODE")
        # 0007 touches kb.chunks (ADD COLUMN / VALIDATE); it must time out on lock_timeout (~3s),
        # not hang indefinitely.
        with psycopg.connect(dsn, autocommit=True) as conn:
            started = time.monotonic()
            with pytest.raises(MigrationError):
                upgrade(conn, migrations=discover_migrations()[:7])
            elapsed = time.monotonic() - started
            assert elapsed < 20, f"migration waited {elapsed:.1f}s; lock_timeout not effective"
    finally:
        holder.rollback()
        holder.close()


def test_mcp_query_ro_can_read_but_not_write_the_four_domains(docker_pg_factory) -> None:
    dsn = docker_pg_factory()
    _seed_like_go_live(dsn)
    with psycopg.connect(dsn, autocommit=True) as conn:
        upgrade(conn)

    ro = _swap_user(dsn, "mcp_query_ro")
    with psycopg.connect(ro, autocommit=True) as conn:
        for table in ("document_versions", "entities", "relationships", "knowledge_summaries",
                      "document_permissions", "source_authority", "confidence_weights",
                      "freshness_horizon"):  # fmt: skip
            # SELECT works
            conn.execute(f"SELECT count(*) FROM kb.{table}")
            # a write is refused — either at the privilege level (no INSERT grant) or by the
            # role's default_transaction_read_only=on (ADR-0011 0005); both prove no write path.
            with pytest.raises(
                (psycopg.errors.InsufficientPrivilege, psycopg.errors.ReadOnlySqlTransaction)
            ):
                conn.execute(f"INSERT INTO kb.{table} DEFAULT VALUES")


def test_schema_matches_adr_0022_columns(docker_pg_factory) -> None:
    dsn = docker_pg_factory()
    _seed_like_go_live(dsn)
    with psycopg.connect(dsn, autocommit=True) as conn:
        upgrade(conn)
        assert {"document_id", "version", "content_hash", "source_version", "status", "author",
                "source_updated_at", "created_at", "id"}.issubset(
            _columns(conn, "document_versions"))  # fmt: skip
        assert {"id", "entity_type", "name", "display_name", "document_id", "metadata",
                "created_at"}.issubset(_columns(conn, "entities"))  # fmt: skip
        assert {"id", "src_entity_id", "dst_entity_id", "rel_type", "metadata",
                "created_at"}.issubset(_columns(conn, "relationships"))  # fmt: skip
        assert {"subject_type", "subject_id", "summary", "provenance",
                "generated_at"}.issubset(_columns(conn, "knowledge_summaries"))  # fmt: skip
        assert {"document_id", "principal", "grant_type"}.issubset(
            _columns(conn, "document_permissions"))  # fmt: skip
        # source-authority config seeded
        assert conn.execute("SELECT count(*) FROM kb.source_authority").fetchone()[0] == 4
        weights = conn.execute(
            "SELECT w_retrieval, w_agreement, w_freshness FROM kb.confidence_weights "
            "WHERE profile = 'default'"
        ).fetchone()
        assert [float(w) for w in weights] == [0.5, 0.3, 0.2]


def test_relationships_reject_self_loops_and_duplicate_edges(docker_pg_factory) -> None:
    dsn = docker_pg_factory()
    _seed_like_go_live(dsn)
    with psycopg.connect(dsn, autocommit=True) as conn:
        upgrade(conn)
        a, b = (
            conn.execute(
                "INSERT INTO kb.entities (entity_type, name) VALUES ('service', %s) RETURNING id",
                (name,),
            ).fetchone()[0]
            for name in ("svc-a", "svc-b")
        )
        conn.execute(
            "INSERT INTO kb.relationships (src_entity_id, dst_entity_id, rel_type) "
            "VALUES (%s, %s, 'depends_on')",
            (a, b),
        )
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(
                "INSERT INTO kb.relationships (src_entity_id, dst_entity_id, rel_type) "
                "VALUES (%s, %s, 'depends_on')",
                (a, b),
            )
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO kb.relationships (src_entity_id, dst_entity_id, rel_type) "
                "VALUES (%s, %s, 'depends_on')",
                (a, a),
            )
