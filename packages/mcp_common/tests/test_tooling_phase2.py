"""Phase-2 shared helpers in `mcp_common.tooling`: `CallState` (one tool call's clock,
redaction counter, shared byte budget, warnings), `parse_time_range`, `sanitize_json`."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from mcp_common.envelope import Citation, SourceType
from mcp_common.errors import ErrorCode, ToolError
from mcp_common.testing import assert_envelope_invariants
from mcp_common.tooling import CallState, build_result, parse_time_range, sanitize_json


def _dt(text: str) -> datetime:
    return datetime.fromisoformat(text)


def test_parse_time_range_ok_returns_aware_pair() -> None:
    start, end = parse_time_range(
        _dt("2026-09-30T10:00:00+00:00"), _dt("2026-09-30T12:00:00+00:00"),
        max_days=31, source="opensearch",
    )  # fmt: skip
    assert start < end and start.tzinfo is not None


@pytest.mark.parametrize(
    ("start", "end", "field"),
    [
        ("2026-09-30T10:00:00", "2026-09-30T12:00:00+00:00", "time_from"),  # naive
        ("2026-09-30T10:00:00+00:00", "2026-09-30T12:00:00", "time_to"),  # naive
        ("2026-09-30T12:00:00+00:00", "2026-09-30T10:00:00+00:00", "time_from"),  # reversed
        ("2026-09-30T10:00:00+00:00", "2026-09-30T10:00:00+00:00", "time_from"),  # empty window
        ("2026-01-01T00:00:00+00:00", "2026-09-30T00:00:00+00:00", "time_from"),  # too long
    ],
)
def test_parse_time_range_rejects_with_field(start: str, end: str, field: str) -> None:
    with pytest.raises(ToolError) as exc:
        parse_time_range(_dt(start), _dt(end), max_days=31, source="cloudwatch")
    assert exc.value.code == ErrorCode.INVALID_INPUT
    assert exc.value.details["field"] == field
    assert exc.value.source == "cloudwatch"


def test_call_state_warn_dedupes_and_truncation_flags() -> None:
    call = CallState(1024)
    call.warn("a")
    call.warn("a")
    assert call.warnings == ["a"] and not call.truncated
    call.truncated_elsewhere = True
    assert call.truncated


def test_call_state_text_redacts_wraps_and_spends_budget() -> None:
    call = CallState(10)
    wrapped = call.text("password=hunter2hunter2 and a very long tail", "opensearch", "i/1")
    assert wrapped is not None and "hunter2" not in wrapped
    assert call.counter.count == 1 and call.truncated
    assert call.text("", "opensearch", "i/2") is None
    assert call.text(None, "opensearch", "i/3") is None


def test_call_state_text_respects_redact_disabled() -> None:
    call = CallState(1024, redact_disabled=True)
    assert "password=abcdefgh" in (call.text("password=abcdefgh", "x", "1") or "")
    assert call.counter.count == 0


def test_sanitize_json_scrubs_leaves_wraps_free_text_only() -> None:
    call = CallState(4096)
    out = sanitize_json(
        {
            "level": "ERROR",
            "message": "boom token=abcdefgh1234",
            "nested": {"list": ["plain", {"msg": "x"}], "n": 3, "ok": True, "none": None},
            "long": "y" * 300,
        },
        call,
        source="opensearch",
        content_id="idx/1",
    )
    assert out["level"] == "ERROR"
    assert out["message"].startswith("<untrusted-content") and "abcdefgh1234" not in out["message"]
    assert out["nested"]["list"][0] == "plain"
    assert out["nested"]["list"][1]["msg"].startswith("<untrusted-content")
    assert out["nested"]["n"] == 3 and out["nested"]["ok"] is True and out["nested"]["none"] is None
    assert out["long"].startswith("<untrusted-content")
    assert call.counter.count == 1


def test_sanitize_json_stops_when_budget_exhausted() -> None:
    call = CallState(5)
    out = sanitize_json({"message": "abcdefghijklmnop", "other": "zzzzzzzz"}, call,
                        source="opensearch", content_id="i")  # fmt: skip
    assert call.truncated
    assert "abcdefghijklmnop" not in out["message"]


def test_utc_helper_sanity() -> None:
    assert datetime.now(UTC).tzinfo is not None


def _scope() -> Citation:
    return Citation(
        source_type=SourceType.CLOUDWATCH,
        label="Logs Insights /aws/x 10:00-12:00Z",
        locator={"log_groups": ["/aws/x"]},
    )


def test_build_result_partial_without_items_keeps_the_scope_citation() -> None:
    """Contract invariant 7: partial + no rows is valid and cites the query scope."""
    result = build_result(
        SourceType.CLOUDWATCH, [], [], started=0.0, query_echo={}, truncated=True,
        scope_citation=_scope(),
    )  # fmt: skip
    assert result.status.value == "partial" and result.items == []
    assert [c.label for c in result.citations] == [_scope().label]
    assert_envelope_invariants(result)


def test_build_result_scope_citation_is_ignored_when_not_truncated_or_when_items_exist() -> None:
    empty = build_result(
        SourceType.CLOUDWATCH, [], [], started=0.0, query_echo={}, scope_citation=_scope()
    )
    assert empty.status.value == "empty" and empty.citations == []
    ok = build_result(
        SourceType.CLOUDWATCH, [{"citation_ref": 0}], [_scope()], started=0.0, query_echo={},
        truncated=True, scope_citation=Citation(source_type=SourceType.CLOUDWATCH, label="x"),
    )  # fmt: skip
    assert ok.status.value == "partial" and [c.label for c in ok.citations] == [_scope().label]
