"""T-077: `kb.ingest_failures` + `run --retry-failed`.

AC: FR-012/AC-002. A document that fails for good must stay visible and retryable even though the
checkpoint has moved on (ADR-0012 A2/A5).
"""

from __future__ import annotations

from ingest_helpers import FakeConnector, PoisonProvider, doc, make_ctx, q, run, scalar


def test_FR_012_AC_002_a_document_failing_three_times_leaves_a_failure_row(rw_dsn: str) -> None:
    docs = [doc("1"), doc("2", minutes=5, content="<p>POISON</p>")]
    run(rw_dsn, {"confluence": FakeConnector("confluence", docs)},
        ctx=make_ctx(rw_dsn, prov=PoisonProvider()))  # fmt: skip
    rows = q(rw_dsn, "SELECT source_id, attempts, stage, code, last_error FROM kb.ingest_failures")
    assert len(rows) == 1
    source_id, attempts, stage, code, last_error = rows[0]
    assert (source_id, attempts, stage, code) == (
        "2",
        3,
        "embed",
        "upstream_error",
    )  # 1 + 2 retries
    assert "TimeoutError" in last_error


def test_max_doc_retries_is_honoured(rw_dsn: str) -> None:
    poison = PoisonProvider()
    run(rw_dsn, {"confluence": FakeConnector("confluence", [doc("2", content="<p>POISON</p>")])},
        ctx=make_ctx(rw_dsn, prov=poison, max_retries=0))  # fmt: skip
    assert poison.calls == 1
    assert scalar(rw_dsn, "SELECT attempts FROM kb.ingest_failures") == 1


def test_a_second_failing_run_accumulates_attempts(rw_dsn: str) -> None:
    for _ in range(2):
        run(rw_dsn, {"confluence": FakeConnector("confluence", [doc("2", content="<p>POISON</p>")])},  # noqa: E501
            ctx=make_ctx(rw_dsn, prov=PoisonProvider()))  # fmt: skip
    assert scalar(rw_dsn, "SELECT attempts FROM kb.ingest_failures") == 6


def test_FR_012_AC_002_retry_failed_reingests_exactly_the_failed_document_and_clears_the_row(
    rw_dsn: str,
) -> None:
    docs = [doc("1"), doc("2", minutes=5, content="<p>POISON</p>"), doc("3", minutes=9)]
    run(rw_dsn, {"confluence": FakeConnector("confluence", docs)},
        ctx=make_ctx(rw_dsn, prov=PoisonProvider()))  # fmt: skip
    assert scalar(rw_dsn, "SELECT count(*) FROM kb.documents") == 2

    # The source content is fixed; the cursor has already moved past doc 2's neighbours.
    healed = FakeConnector("confluence", [doc("1"), doc("2", minutes=5), doc("3", minutes=9)])
    report = run(rw_dsn, {"confluence": healed}, retry_failed=True)

    assert healed.fetched == ["2"], "only the document in ingest_failures is re-fetched"
    assert report.status == "success"
    assert scalar(rw_dsn, "SELECT count(*) FROM kb.ingest_failures") == 0
    assert q(rw_dsn, "SELECT source_id FROM kb.documents ORDER BY 1") == [("1",), ("2",), ("3",)]


def test_a_document_that_still_fails_stays_in_the_table(rw_dsn: str) -> None:
    docs = [doc("2", minutes=5, content="<p>POISON</p>")]
    run(rw_dsn, {"confluence": FakeConnector("confluence", docs)},
        ctx=make_ctx(rw_dsn, prov=PoisonProvider(), max_retries=0))  # fmt: skip
    again = FakeConnector("confluence", docs)
    report = run(rw_dsn, {"confluence": again}, retry_failed=True,
                 ctx=make_ctx(rw_dsn, prov=PoisonProvider(), max_retries=0))  # fmt: skip
    assert report.status == "partial" and report.sources[0].documents_failed == 1
    assert scalar(rw_dsn, "SELECT count(*) FROM kb.ingest_failures") == 1


def test_a_failure_for_a_document_gone_from_the_source_is_kept_for_a_human(rw_dsn: str) -> None:
    run(rw_dsn, {"confluence": FakeConnector("confluence", [doc("2", content="<p>POISON</p>")])},
        ctx=make_ctx(rw_dsn, prov=PoisonProvider(), max_retries=0))  # fmt: skip
    gone = FakeConnector("confluence", [])
    report = run(rw_dsn, {"confluence": gone}, retry_failed=True)
    assert report.status == "success" and gone.fetched == ["2"]
    assert scalar(rw_dsn, "SELECT count(*) FROM kb.ingest_failures") == 1


def test_retry_failed_without_failures_is_a_normal_run(rw_dsn: str) -> None:
    connector = FakeConnector("confluence", [doc("1")])
    report = run(rw_dsn, {"confluence": connector}, retry_failed=True)
    assert report.status == "success" and connector.fetched == []
    assert report.sources[0].documents_upserted == 1
