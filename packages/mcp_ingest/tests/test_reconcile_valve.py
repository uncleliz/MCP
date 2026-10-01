"""T-078: reconcile / tombstone with the safety valve.

AC: FR-012/AC-002, FR-011/AC-001. Without the valve a crawl that dies at 30% would tombstone the
other 70% of the corpus and `kb_semantic_search` would answer "nothing indexed" (R18, ADR-0012 A3).
"""

from __future__ import annotations

from ingest_helpers import FakeConnector, PoisonProvider, doc, make_ctx, q, run, scalar
from mcp_ingest.pipeline.reconcile import VALVE_RATIO, reconcile


def seed_ten(dsn: str) -> list:
    docs = [
        doc(str(i), minutes=i, content=f"<p>page number {i} about topic{i}</p>") for i in range(10)
    ]
    run(dsn, {"confluence": FakeConnector("confluence", docs)}, mode="full")
    assert scalar(dsn, "SELECT count(*) FROM kb.documents WHERE deleted_at IS NULL") == 10
    return docs


def live(dsn: str) -> int:
    return scalar(dsn, "SELECT count(*) FROM kb.documents WHERE deleted_at IS NULL")


def test_valve_ratio_is_the_adr_value() -> None:
    assert VALVE_RATIO == 0.8  # THRESHOLD TBD, ADR-0012 A3


def test_FR_012_AC_002_a_crawl_that_dies_at_30_percent_tombstones_nothing(rw_dsn: str) -> None:
    docs = seed_ten(rw_dsn)
    dying = FakeConnector("confluence", docs, die_after=3)
    report = run(rw_dsn, {"confluence": dying}, mode="full")
    result = report.sources[0]
    assert live(rw_dsn) == 10 and result.documents_tombstoned == 0
    assert scalar(rw_dsn, "SELECT count(*) FROM kb.chunks") >= 10
    stages = [e.stage for e in result.errors]
    assert "crawl" in stages and "reconcile" in stages


def test_a_crawl_that_completes_but_returns_too_little_is_stopped_by_the_valve(rw_dsn: str) -> None:
    docs = seed_ten(rw_dsn)
    chunks_before = scalar(rw_dsn, "SELECT count(*) FROM kb.chunks")
    report = run(rw_dsn, {"confluence": FakeConnector("confluence", docs[:3])}, mode="full")
    result = report.sources[0]
    assert live(rw_dsn) == 10 and scalar(rw_dsn, "SELECT count(*) FROM kb.chunks") == chunks_before
    (error,) = result.errors
    assert (
        error.stage == "reconcile"
        and "safety valve" in error.message
        and result.status == "partial"
    )
    assert result.documents_tombstoned == 0


def test_FR_011_AC_001_a_full_crawl_tombstones_what_vanished_and_deletes_its_chunks(
    rw_dsn: str,
) -> None:
    docs = seed_ten(rw_dsn)
    gone_chunks = scalar(
        rw_dsn,
        "SELECT count(*) FROM kb.chunks c JOIN kb.documents d ON d.id = c.document_id WHERE d.source_id IN ('8','9')",  # noqa: E501
    )
    assert gone_chunks >= 2
    report = run(rw_dsn, {"confluence": FakeConnector("confluence", docs[:8])}, mode="full")
    result = report.sources[0]
    assert result.status == "success" and result.documents_tombstoned == 2  # 8/10 == the valve edge
    assert q(
        rw_dsn, "SELECT source_id FROM kb.documents WHERE deleted_at IS NOT NULL ORDER BY 1"
    ) == [("8",), ("9",)]
    assert (
        scalar(
            rw_dsn,
            "SELECT count(*) FROM kb.chunks c JOIN kb.documents d ON d.id = c.document_id WHERE d.deleted_at IS NOT NULL",  # noqa: E501
        )
        == 0
    )  # physically gone
    assert live(rw_dsn) == 8


def test_documents_skipped_by_hash_still_count_as_seen_and_are_not_tombstoned(rw_dsn: str) -> None:
    docs = seed_ten(rw_dsn)
    report = run(rw_dsn, {"confluence": FakeConnector("confluence", docs)}, mode="full")
    assert report.sources[0].documents_skipped == 10 and report.sources[0].documents_tombstoned == 0
    assert live(rw_dsn) == 10


def test_incremental_runs_never_tombstone(rw_dsn: str) -> None:
    docs = seed_ten(rw_dsn)
    report = run(rw_dsn, {"confluence": FakeConnector("confluence", docs[:1])})
    assert report.sources[0].documents_tombstoned is None and live(rw_dsn) == 10


def test_limit_and_since_runs_never_reconcile(rw_dsn: str) -> None:
    docs = seed_ten(rw_dsn)
    limited = run(rw_dsn, {"confluence": FakeConnector("confluence", docs)}, mode="full", limit=2)
    assert limited.sources[0].documents_tombstoned is None and live(rw_dsn) == 10


def test_a_run_with_a_failed_document_does_not_reconcile(rw_dsn: str) -> None:

    docs = seed_ten(rw_dsn)
    docs[9] = doc("9", minutes=9, content="<p>POISON now</p>")
    report = run(rw_dsn, {"confluence": FakeConnector("confluence", docs[:9] + [docs[9]])}, mode="full",  # noqa: E501
                 ctx=make_ctx(rw_dsn, prov=PoisonProvider(), max_retries=0))  # fmt: skip
    # the provider is the fake with another model => compat check passes (same id) and doc 9 fails
    assert report.sources[0].documents_failed == 1
    assert report.sources[0].documents_tombstoned == 0 and live(rw_dsn) == 10


def test_reconcile_is_scoped_to_its_own_source(rw_dsn: str) -> None:
    seed_ten(rw_dsn)
    other = [doc(f"42:mr:{i}", source_type="gitlab", container="p/a", minutes=i) for i in range(3)]
    run(rw_dsn, {"gitlab": FakeConnector("gitlab", other)}, mode="full")
    run(rw_dsn, {"gitlab": FakeConnector("gitlab", other[:2])}, mode="full")  # 2/3 < 0.8: valve
    assert (
        scalar(
            rw_dsn,
            "SELECT count(*) FROM kb.documents WHERE source_type='confluence' AND deleted_at IS NULL",  # noqa: E501
        )
        == 10
    )
    assert (
        scalar(
            rw_dsn,
            "SELECT count(*) FROM kb.documents WHERE source_type='gitlab' AND deleted_at IS NULL",
        )
        == 3
    )


def test_the_first_full_run_on_an_empty_store_passes_the_valve(rw_dsn: str) -> None:
    report = run(rw_dsn, {"confluence": FakeConnector("confluence", [doc("1")])}, mode="full")
    assert report.sources[0].documents_tombstoned == 0 and report.status == "success"


def test_reconcile_function_reports_why_it_did_nothing() -> None:
    class Boom:
        def transaction(self):  # pragma: no cover - must not be reached
            raise AssertionError

    assert reconcile(
        Boom(),
        source_type="x",
        run_id="r",
        run_succeeded=False,  # type: ignore[arg-type]
        documents_seen=10,
        documents_before=10,
    ).blocked_reason
    assert (
        "safety valve"
        in reconcile(
            Boom(),
            source_type="x",
            run_id="r",
            run_succeeded=True,  # type: ignore[arg-type]
            documents_seen=7,
            documents_before=10,
        ).blocked_reason
    )
