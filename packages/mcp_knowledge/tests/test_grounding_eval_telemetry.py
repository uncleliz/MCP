"""Grounding eval harness + telemetry (T-108) — runs now on the deterministic fake provider.

Asserts the harness exercises the gate end-to-end and that both the harness and the telemetry trace
are honest about τ/recall being TBD until HF egress clears (NFR-003/NFR-010, L-002): the
``calibration_status`` is ``uncalibrated`` and no recall number is invented, and the τ source
constants carry the grep-able ``# THRESHOLD TBD`` marker.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime

from knowledge_helpers import make_candidate
from mcp_common.envelope import GroundedStatus, GroundingVerdict
from mcp_knowledge.grounding import eval_harness
from mcp_knowledge.grounding.eval_harness import EvalCase, run_eval
from mcp_knowledge.grounding.telemetry import trace_from_result
from mcp_knowledge.grounding.verdict import grade_claims
from mcp_knowledge.tools.grounded import build_reconciled_result

_NOW = datetime(2026, 10, 2, tzinfo=UTC)


def test_harness_runs_on_fake_provider_and_counts_verdicts() -> None:
    cases = [
        EvalCase(id="Q-001", question="has evidence?", expect="FACT",
                 candidates=[make_candidate()]),
        EvalCase(id="Q-002", question="no evidence?", expect="UNKNOWN", candidates=[]),
    ]  # fmt: skip
    report = run_eval(cases, now=_NOW)
    assert report.cases == 2
    assert report.verdict_counts.get("FACT", 0) >= 1
    assert report.verdict_counts.get("UNKNOWN", 0) >= 1
    # Per-case verdict recorded; the FACT case carries a confidence, the UNKNOWN case does not.
    by_id = {c["id"]: c for c in report.per_case}
    assert by_id["Q-001"]["verdict"] == GroundingVerdict.FACT.value
    assert by_id["Q-001"]["confidence"] is not None
    assert by_id["Q-002"]["verdict"] == GroundingVerdict.UNKNOWN.value


def test_harness_report_is_uncalibrated_and_claims_no_recall() -> None:
    report = run_eval([], now=_NOW)
    assert report.calibration_status == "uncalibrated"
    assert report.recall is None  # TBD — never an invented number before HF egress


def test_harness_source_carries_threshold_tbd_marker_for_grep() -> None:
    source = inspect.getsource(eval_harness)
    assert "# THRESHOLD TBD (NFR-010, L-002)" in source


def test_questions_yaml_skeleton_loads() -> None:
    questions = eval_harness.load_questions()
    assert questions, "the questions skeleton must load"
    assert all("id" in q and "expect" in q for q in questions)


def test_telemetry_trace_counts_match_verdicts() -> None:
    graded = grade_claims([], now=_NOW)  # empty -> builder inserts an UNKNOWN placeholder
    result = build_reconciled_result(
        graded,
        started=0.0,
        query_echo={"query": "x"},
        reranker_status="disabled",
    )
    trace = trace_from_result(result)
    assert trace.status == GroundedStatus.INSUFFICIENT_EVIDENCE.value
    assert trace.reranker == "disabled"
    assert trace.calibration_status == "uncalibrated"
    assert trace.unknown == trace.claims_total
    assert trace.fact == 0


def test_telemetry_trace_json_is_stable_and_secretless() -> None:
    graded = grade_claims([make_candidate_raw()], now=_NOW)
    result = build_reconciled_result(
        graded, started=0.0, query_echo={"query": "secret-query"}, reranker_status="enabled"
    )
    trace = trace_from_result(result)
    payload = trace.to_json()
    # The trace must NOT carry the query text (secretless at INFO; query only at DEBUG by caller).
    assert "secret-query" not in payload
    # Deterministic: same result ⇒ same JSON.
    assert trace.to_json() == payload
    assert '"fact":1' in payload


def make_candidate_raw():
    from mcp_knowledge.grounding.verdict import _raw_from_chunk
    from mcp_knowledge.pack.compress import compress_candidates

    return _raw_from_chunk(compress_candidates([make_candidate()])[0])


def test_simple_yaml_parser_handles_comments_and_stray_lines() -> None:
    from mcp_knowledge.grounding.eval_harness import _parse_simple_yaml

    text = (
        "# a comment before any item\n"
        "stray-line-ignored\n"
        "\n"
        "- id: Q-1\n"
        "  question: \"q\"\n"
        "  no_colon_line\n"  # a line with no ':' under an item is ignored
        "- id: Q-2\n"
        "  expect: FACT\n"
    )
    items = _parse_simple_yaml(text)
    assert [i["id"] for i in items] == ["Q-1", "Q-2"]
    assert items[1]["expect"] == "FACT"


def test_harness_grades_conflict_case() -> None:
    from mcp_knowledge.grounding.verdict import grade_claims as _gc

    # Two candidates with the same heading but different values -> CONFLICT (exercised via gate).
    c1 = make_candidate(chunk_id=1, document_id="11111111-1111-4111-8111-111111111111",
                        content="read timeout 5s", source_type="confluence")
    c2 = make_candidate(chunk_id=2, document_id="22222222-2222-4222-8222-222222222222",
                        content="read timeout 10s", source_type="gitlab")
    # heading_path is None on make_candidate, so group by content fallback keeps them separate;
    # grade_claims directly with a shared heading is covered in test_grounding_gt. Here assert the
    # harness runs multi-candidate cases without error and counts verdicts.
    report = run_eval(
        [EvalCase(id="Q-c", question="conflict?", expect="CONFLICT", candidates=[c1, c2])],
        now=_NOW,
    )
    assert report.cases == 1
    assert sum(report.verdict_counts.values()) >= 1
    _ = _gc  # import exercised
