"""T-076: checkpoint, per-source failure isolation, advisory locks, `ingest_runs`.

AC: FR-012/AC-002. Guards R18 (silent data loss: the cursor must never jump over a failed doc).
"""

from __future__ import annotations

from datetime import timedelta

import psycopg
import pytest
from ingest_helpers import (
    T0,
    FakeConnector,
    PoisonProvider,
    doc,
    make_ctx,
    provider,
    q,
    run,
    scalar,
)
from mcp_common.errors import ErrorCode, ToolError
from mcp_ingest.connectors.base import ConnectorStatus, Cursor
from mcp_ingest.pipeline.checkpoint import EPSILON, next_cursor, try_lock
from mcp_ingest.pipeline.run import ConfigurationError
from mcp_ingest.reports import EXIT_FAILED, EXIT_LOCKED, EXIT_OK, EXIT_PARTIAL


def cursor_of(dsn: str, source: str) -> Cursor | None:
    rows = q(dsn, "SELECT cursor FROM kb.ingest_source_state WHERE source_type = %s", (source,))
    return Cursor.from_json(rows[0][0]) if rows else None


# -- next_cursor (pure) --------------------------------------------------------------------------


def test_next_cursor_is_the_max_committed_watermark_when_nothing_failed() -> None:
    marks = [T0, T0 + timedelta(minutes=5), T0 + timedelta(minutes=2)]
    assert next_cursor(None, marks, []) == Cursor(T0 + timedelta(minutes=5))


def test_R18_next_cursor_steps_back_before_the_earliest_failed_document() -> None:
    committed = [T0 + timedelta(minutes=m) for m in (0, 20)]
    failed = [T0 + timedelta(minutes=10), T0 + timedelta(minutes=30)]
    assert next_cursor(None, committed, failed) == Cursor(T0 + timedelta(minutes=10) - EPSILON)


def test_next_cursor_never_moves_backwards_and_keeps_old_without_information() -> None:
    old = Cursor(T0 + timedelta(hours=1))
    assert next_cursor(old, [], []) == old
    assert next_cursor(old, [], [T0]) == old  # failed doc older than the settled cursor
    assert next_cursor(old, [], [None]) == old  # failed doc without a watermark: do not guess
    assert next_cursor(None, [], [None]) is None


def test_cursor_json_roundtrip() -> None:
    cursor = Cursor(T0)
    assert Cursor.from_json(cursor.to_json()) == cursor
    assert Cursor.from_json(None) is None and Cursor.from_json({"watermark": None}) is None


# -- integration ---------------------------------------------------------------------------------


def test_FR_012_AC_002_one_dead_source_does_not_stop_the_others_nor_move_its_cursor(
    rw_dsn: str,
) -> None:
    good = FakeConnector("confluence", [doc("1"), doc("2", minutes=5)])
    dead = FakeConnector(
        "gitlab",
        [doc("42:blob:a.md", source_type="gitlab", container="pay/api", minutes=1)],
        die_after=0,
    )
    report = run(rw_dsn, {"gitlab": dead, "confluence": good})

    by_source = {s.source_type: s for s in report.sources}
    assert report.status == "partial" and report.exit_code == EXIT_PARTIAL
    assert by_source["confluence"].status == "success" and by_source["confluence"].cursor_advanced
    assert by_source["gitlab"].status == "failed" and not by_source["gitlab"].cursor_advanced
    assert (
        by_source["gitlab"].errors[0].stage == "crawl" and by_source["gitlab"].errors[0].retryable
    )
    assert scalar(rw_dsn, "SELECT count(*) FROM kb.documents WHERE source_type = 'confluence'") == 2
    assert cursor_of(rw_dsn, "confluence") == Cursor(T0 + timedelta(minutes=5))
    assert cursor_of(rw_dsn, "gitlab") is None  # did not jump
    (summary,) = q(
        rw_dsn, "SELECT status, error_summary FROM kb.ingest_runs WHERE source_type = 'gitlab'"
    )
    assert summary[0] == "failed" and summary[1][0]["stage"] == "crawl"
    assert good.closed and dead.closed


def test_a_source_that_dies_midway_keeps_its_committed_documents_but_not_its_cursor(
    rw_dsn: str,
) -> None:
    docs = [doc(str(i), minutes=i) for i in range(1, 5)]
    run(rw_dsn, {"confluence": FakeConnector("confluence", docs[:1])})  # cursor = T0+1
    before = cursor_of(rw_dsn, "confluence")
    dying = FakeConnector("confluence", docs, die_after=3)
    report = run(rw_dsn, {"confluence": dying})
    result = report.sources[0]
    assert result.status == "partial" and result.documents_seen == 3  # docs 1 (skipped), 2 and 3
    assert cursor_of(rw_dsn, "confluence") == before


def test_R18_a_failed_document_in_the_middle_pulls_the_cursor_back_before_it(rw_dsn: str) -> None:
    docs = [
        doc("1", minutes=0),
        doc("2", minutes=10, content="<p>POISON</p>"),
        doc("3", minutes=20),
    ]
    flaky = PoisonProvider()
    first = run(
        rw_dsn, {"confluence": FakeConnector("confluence", docs)},
        ctx=make_ctx(rw_dsn, prov=flaky),
    )  # fmt: skip
    result = first.sources[0]
    assert result.status == "partial", "any failed document makes the source partial"
    assert (result.documents_upserted, result.documents_failed) == (2, 1)
    assert result.errors[0].source_id == "2" and result.errors[0].stage == "embed"
    assert cursor_of(rw_dsn, "confluence") == Cursor(T0 + timedelta(minutes=10) - EPSILON)
    assert flaky.calls == 2 + 3  # doc 1, doc 3, and doc 2 tried 1 + 2 retries

    fixed = FakeConnector("confluence", [docs[0], doc("2", minutes=10), docs[2]])
    second = run(rw_dsn, {"confluence": fixed})
    assert second.status == "success"
    assert fixed.cursors_seen[0] == Cursor(T0 + timedelta(minutes=10) - EPSILON)
    assert scalar(rw_dsn, "SELECT count(*) FROM kb.documents") == 3
    assert scalar(rw_dsn, "SELECT count(*) FROM kb.ingest_failures") == 0


def test_the_cursor_boundary_is_inclusive_a_document_on_the_watermark_is_seen_again(
    rw_dsn: str,
) -> None:
    run(rw_dsn, {"confluence": FakeConnector("confluence", [doc("1", minutes=5)])})
    again = FakeConnector("confluence", [doc("1", minutes=5), doc("0", minutes=4)])
    report = run(rw_dsn, {"confluence": again})
    # `>=`: the doc exactly at the watermark comes back (and the hash absorbs it); older ones don't
    assert report.sources[0].documents_seen == 1 and report.sources[0].documents_skipped == 1


def test_FR_012_AC_002_one_ingest_runs_row_per_source_per_run(rw_dsn: str) -> None:
    connectors = {
        "confluence": FakeConnector("confluence", [doc("1")]),
        "gitlab": FakeConnector("gitlab", [doc("42:mr:1", source_type="gitlab", container="p/a")]),
    }
    run(rw_dsn, connectors)
    run(rw_dsn, connectors)
    rows = q(
        rw_dsn,
        "SELECT source_type, status, documents_seen FROM kb.ingest_runs ORDER BY started_at, source_type",  # noqa: E501
    )
    assert len(rows) == 4 and {r[0] for r in rows} == {"confluence", "gitlab"}
    assert all(r[1] == "success" and r[2] == 1 for r in rows)
    state = q(
        rw_dsn,
        "SELECT source_type, last_run_id IS NOT NULL, last_success_at IS NOT NULL FROM kb.ingest_source_state ORDER BY 1",  # noqa: E501
    )
    assert state == [("confluence", True, True), ("gitlab", True, True)]


def test_FR_012_AC_002_a_second_process_does_not_run_the_same_source(
    rw_dsn: str,
) -> None:
    holder = psycopg.connect(rw_dsn, autocommit=True)
    try:
        assert try_lock(holder, "confluence")
        blocked = FakeConnector("confluence", [doc("1")])
        free = FakeConnector("gitlab", [doc("42:mr:1", source_type="gitlab", container="p/a")])
        report = run(rw_dsn, {"confluence": blocked, "gitlab": free})
        by = {s.source_type: s for s in report.sources}
        assert by["confluence"].status == "skipped" and not blocked.cursors_seen
        assert by["gitlab"].status == "success"
        assert report.exit_code == EXIT_OK  # a locked source is neutral when another one ran

        everything_locked = run(rw_dsn, {"confluence": FakeConnector("confluence", [doc("1")])})
        assert everything_locked.exit_code == EXIT_LOCKED
        assert (
            scalar(rw_dsn, "SELECT count(*) FROM kb.documents WHERE source_type = 'confluence'")
            == 0
        )
    finally:
        holder.close()
    # lock released with the session: the next run goes through
    assert run(rw_dsn, {"confluence": FakeConnector("confluence", [doc("1")])}).exit_code == EXIT_OK


def test_the_lock_is_released_after_a_run(rw_dsn: str) -> None:
    run(rw_dsn, {"confluence": FakeConnector("confluence", [doc("1")])})
    with psycopg.connect(rw_dsn, autocommit=True) as other:
        assert try_lock(other, "confluence")


def test_tool_errors_keep_their_contract_code_and_a_crash_is_internal(rw_dsn: str) -> None:
    timeout = ToolError(ErrorCode.UPSTREAM_TIMEOUT, "gitlab timed out", "gitlab", True)
    report = run(
        rw_dsn,
        {
            "gitlab": FakeConnector(
                "gitlab", [doc("42:mr:1", source_type="gitlab")], die_after=0, die_with=timeout
            )
        },
    )
    error = report.sources[0].errors[0]
    assert (error.code, error.retryable, report.exit_code) == (
        "upstream_timeout",
        True,
        EXIT_FAILED,
    )
    crash = run(
        rw_dsn,
        {"gitlab": FakeConnector("gitlab", [doc("1")], die_after=0, die_with=ValueError("boom"))},
    )
    assert crash.sources[0].errors[0].code == "internal"


def test_a_connector_that_cannot_be_built_is_a_failed_source_not_a_crashed_run(rw_dsn: str) -> None:
    from ingest_helpers import FakeConnector as FC
    from mcp_common.config import SourceMisconfiguredError
    from mcp_ingest.pipeline.run import RunOptions, run_ingest

    def build(name: str):
        raise SourceMisconfiguredError(["MCP_GITLAB_PRIVATE_TOKEN"], source="gitlab")

    report = run_ingest(
        make_ctx(rw_dsn), RunOptions(sources=["gitlab", "confluence"]),
        describe=lambda n: ConnectorStatus(True, True),
        build=lambda n: build(n) if n == "gitlab" else FC("confluence", [doc("1")]),
        explicit_source=False,
    )  # fmt: skip
    by = {s.source_type: s for s in report.sources}
    assert by["gitlab"].status == "failed" and by["gitlab"].errors[0].stage == "config"
    assert "MCP_GITLAB_PRIVATE_TOKEN" in by["gitlab"].errors[0].message
    assert by["confluence"].status == "success" and report.exit_code == EXIT_PARTIAL


def test_disabled_and_misconfigured_sources(rw_dsn: str) -> None:
    from mcp_ingest.pipeline.run import RunOptions, run_ingest

    statuses = {
        "opensearch": ConnectorStatus(False, False, ["MCP_INGEST_OPENSEARCH_INDICES"]),
        "gitlab": ConnectorStatus(True, False, ["MCP_GITLAB_BASE_URL"]),
    }

    def go(names: list[str], explicit: bool):
        return run_ingest(
            make_ctx(rw_dsn), RunOptions(sources=names), describe=lambda n: statuses[n],
            build=lambda n: pytest.fail("must not build"), explicit_source=explicit,
        )  # fmt: skip

    disabled = go(["opensearch"], True)
    assert disabled.sources[0].status == "skipped" and not disabled.sources[0].errors
    assert disabled.exit_code == EXIT_OK  # "does not crawl anything" is not an error

    skipped = go(["gitlab"], False)
    assert skipped.sources[0].status == "skipped" and skipped.sources[0].errors[0].stage == "config"
    assert skipped.exit_code == EXIT_FAILED  # nothing could run because of configuration

    explicit = go(["gitlab"], True)
    assert (
        explicit.sources[0].status == "failed"
        and "MCP_GITLAB_BASE_URL" in explicit.sources[0].errors[0].message
    )


def test_dry_run_writes_nothing_and_reports_what_would_happen(rw_dsn: str) -> None:
    run(rw_dsn, {"confluence": FakeConnector("confluence", [doc("1")])})
    snapshot = (
        scalar(rw_dsn, "SELECT count(*) FROM kb.ingest_runs"),
        scalar(rw_dsn, "SELECT count(*) FROM kb.chunks"),
        q(rw_dsn, "SELECT cursor FROM kb.ingest_source_state"),
    )
    report = run(
        rw_dsn,
        {
            "confluence": FakeConnector(
                "confluence", [doc("1"), doc("2", minutes=3, content="<p>new</p>")]
            )
        },
        dry_run=True,
    )
    result = report.sources[0]
    assert report.dry_run and result.run_id is None
    assert (result.documents_skipped, result.documents_upserted) == (1, 1)
    assert snapshot == (
        scalar(rw_dsn, "SELECT count(*) FROM kb.ingest_runs"),
        scalar(rw_dsn, "SELECT count(*) FROM kb.chunks"),
        q(rw_dsn, "SELECT cursor FROM kb.ingest_source_state"),
    )
    assert scalar(rw_dsn, "SELECT count(*) FROM kb.documents") == 1


def test_since_overrides_the_checkpoint_and_does_not_move_it(rw_dsn: str) -> None:
    run(rw_dsn, {"confluence": FakeConnector("confluence", [doc("1", minutes=10)])})
    saved = cursor_of(rw_dsn, "confluence")
    connector = FakeConnector("confluence", [doc("1", minutes=10)])
    run(rw_dsn, {"confluence": connector}, since=T0 - timedelta(days=1))
    assert connector.cursors_seen == [Cursor(T0 - timedelta(days=1))]
    assert cursor_of(rw_dsn, "confluence") == saved


def test_limit_caps_documents_per_source(rw_dsn: str) -> None:
    connector = FakeConnector("confluence", [doc(str(i), minutes=i) for i in range(5)])
    report = run(rw_dsn, {"confluence": connector}, limit=2)
    assert report.sources[0].documents_seen == 2


def test_the_run_refuses_when_the_configured_model_differs_from_the_stored_one(rw_dsn: str) -> None:
    run(rw_dsn, {"confluence": FakeConnector("confluence", [doc("1")])})
    with pytest.raises(ConfigurationError, match="reembed"):
        run(rw_dsn, {"confluence": FakeConnector("confluence", [doc("2", minutes=1)])},
            ctx=make_ctx(rw_dsn, model="other/model"))  # fmt: skip
    with pytest.raises(ConfigurationError, match="dimension"):
        run(rw_dsn, {"confluence": FakeConnector("confluence", [doc("2", minutes=1)])},
            ctx=make_ctx(rw_dsn, prov=provider(dimensions=8)))  # fmt: skip


def test_an_unconfigured_connector_is_neutral_when_another_source_ran(rw_dsn: str) -> None:
    """A team that only uses Confluence must not get exit 1 every hour (`--source all`)."""
    from mcp_ingest.pipeline.run import RunOptions, run_ingest

    statuses = {
        "confluence": ConnectorStatus(True, True),
        "gitlab": ConnectorStatus(True, False, ["MCP_GITLAB_BASE_URL"]),
        "opensearch": ConnectorStatus(False, False, ["MCP_INGEST_OPENSEARCH_INDICES"]),
    }
    report = run_ingest(
        make_ctx(rw_dsn),
        RunOptions(sources=list(statuses)),
        describe=lambda name: statuses[name],
        build=lambda name: FakeConnector("confluence", [doc("1")]),
        explicit_source=False,
    )
    by = {s.source_type: s for s in report.sources}
    assert report.exit_code == EXIT_OK and report.status == "success"
    assert by["gitlab"].status == "skipped" and by["gitlab"].errors[0].stage == "config"
    assert by["opensearch"].status == "skipped" and not by["opensearch"].errors
