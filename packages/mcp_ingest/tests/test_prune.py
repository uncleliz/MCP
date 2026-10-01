"""T-081: `prune [--tombstoned] --older-than Nd`.

AC: FR-012/AC-003, NFR-004. Without `prune` the store only grows. There is no implicit retention:
`--older-than` is mandatory (# THRESHOLD TBD: default retention is an open PO question).
"""

from __future__ import annotations

import json
from datetime import timedelta

import psycopg
import pytest
from ingest_helpers import FakeConnector, assert_valid, doc, q, run, scalar
from mcp_ingest.commands.prune import prune


@pytest.fixture
def aged(rw_dsn: str) -> str:
    """4 documents: live-fresh, tombstoned 45d ago, tombstoned 5d ago, live-but-unseen 120d."""
    docs = [doc(str(i), minutes=i, content=f"<p>page {i} text</p>") for i in range(4)]
    run(rw_dsn, {"confluence": FakeConnector("confluence", docs)})
    q(
        rw_dsn,
        "DELETE FROM kb.chunks WHERE document_id IN (SELECT id FROM kb.documents WHERE source_id IN ('1','2'))",  # noqa: E501
    )
    q(
        rw_dsn,
        "UPDATE kb.documents SET deleted_at = now() - interval '45 days' WHERE source_id = '1'",
    )
    q(
        rw_dsn,
        "UPDATE kb.documents SET deleted_at = now() - interval '5 days' WHERE source_id = '2'",
    )
    q(
        rw_dsn,
        "UPDATE kb.documents SET last_seen_at = now() - interval '120 days' WHERE source_id = '3'",
    )
    return rw_dsn


def ids(dsn: str) -> list[str]:
    return [r[0] for r in q(dsn, "SELECT source_id FROM kb.documents ORDER BY 1")]


def test_FR_012_AC_003_prune_tombstoned_removes_exactly_the_old_tombstones(aged: str) -> None:
    with psycopg.connect(aged, autocommit=True) as conn:
        report = prune(conn, older_than_days=30, tombstoned=True, dry_run=False)
    assert ids(aged) == ["0", "2", "3"]  # '1' (45d) gone; '2' (5d) too young; live docs untouched
    assert report.documents_deleted == 1 and not report.dry_run
    assert report.per_source[0].source_type == "confluence"
    assert (
        scalar(
            aged,
            "SELECT count(*) FROM kb.chunks c JOIN kb.documents d ON d.id=c.document_id WHERE d.source_id IN ('0','3')",  # noqa: E501
        )
        >= 2
    )


def test_dry_run_reports_without_deleting(aged: str) -> None:
    with psycopg.connect(aged, autocommit=True) as conn:
        report = prune(conn, older_than_days=30, tombstoned=True)
    assert report.dry_run and report.documents_deleted == 1
    assert ids(aged) == ["0", "1", "2", "3"]


def test_chunks_of_pruned_live_documents_cascade(aged: str) -> None:
    with psycopg.connect(aged, autocommit=True) as conn:
        report = prune(conn, older_than_days=90, tombstoned=False, dry_run=False)
    assert ids(aged) == ["0", "1", "2"]  # only the live document unseen for 120d
    assert report.documents_deleted == 1 and report.chunks_deleted >= 1
    assert (
        scalar(
            aged,
            "SELECT count(*) FROM kb.chunks c JOIN kb.documents d ON d.id=c.document_id WHERE d.source_id = '3'",  # noqa: E501
        )
        == 0
    )


def test_retention_never_deletes_a_document_merely_unchanged_at_the_source(rw_dsn: str) -> None:
    run(rw_dsn, {"confluence": FakeConnector("confluence", [doc("1")])})
    q(rw_dsn, "UPDATE kb.documents SET source_updated_at = now() - interval '3 years'")
    with psycopg.connect(rw_dsn, autocommit=True) as conn:
        assert (
            prune(conn, older_than_days=30, tombstoned=False, dry_run=False).documents_deleted == 0
        )


def test_prune_clears_failure_rows_of_what_it_deleted_and_honours_source_scope(aged: str) -> None:
    q(
        aged,
        "INSERT INTO kb.ingest_failures (source_type, source_id, stage, code) VALUES ('confluence','1','embed','x')",  # noqa: E501
    )
    with psycopg.connect(aged, autocommit=True) as conn:
        none = prune(conn, older_than_days=30, tombstoned=True, sources=["gitlab"], dry_run=False)
        assert none.documents_deleted == 0 and ids(aged) == ["0", "1", "2", "3"]
        prune(conn, older_than_days=30, tombstoned=True, sources=["confluence"], dry_run=False)
    assert scalar(aged, "SELECT count(*) FROM kb.ingest_failures") == 0


def test_reindex_is_recommended_after_a_bulk_delete(aged: str, monkeypatch) -> None:
    monkeypatch.setattr("mcp_ingest.commands.prune.REINDEX_RECOMMENDED_CHUNKS", 1)
    with psycopg.connect(aged, autocommit=True) as conn:
        assert prune(conn, older_than_days=90, tombstoned=False).reindex_recommended is True


def test_prune_json_validates_against_the_contract(aged: str) -> None:
    with psycopg.connect(aged, autocommit=True) as conn:
        report = prune(conn, older_than_days=30, tombstoned=True)
    assert_valid(json.loads(report.model_dump_json()), operation_id="ingest_prune")


def test_an_invalid_age_is_rejected(aged: str) -> None:
    with psycopg.connect(aged, autocommit=True) as conn, pytest.raises(ValueError):
        prune(conn, older_than_days=0, tombstoned=True)


def test_prune_uses_an_injectable_clock(aged: str) -> None:
    with psycopg.connect(aged, autocommit=True) as conn:
        later = scalar(aged, "SELECT now() + interval '60 days'")
        report = prune(conn, older_than_days=30, tombstoned=True, now=later + timedelta(0))
    assert report.documents_deleted == 2  # '1' and '2' are both older than 30d by then
