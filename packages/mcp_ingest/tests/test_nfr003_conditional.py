"""T-118 / TC-130 (NFR-003 CONDITIONAL / TBD, L-002/E-004) — opening HF egress ENABLES the
measurement but does NOT prove semantic quality. The eval harness RUNS and reports a verdict
distribution + ``calibration_status=uncalibrated``; it must NOT assert any invented recall/τ, and
the ``# THRESHOLD TBD`` marker stays grep-able (ADR-0023 §7, ADR-0010 provisional).

This is the CHG-003 honesty contract for NFR-003: a test that fails if anyone later makes the
harness claim a recall number or set τ while the real golden-set (spike S2) has not been measured.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime

import pytest

# The eval harness lives in mcp_knowledge (built in CHG-001 T-108); CHG-003 only asserts the
# honesty contract on top of it. Skip cleanly if the knowledge package is not installed.
eval_harness = pytest.importorskip("mcp_knowledge.grounding.eval_harness")

_NOW = datetime(2026, 10, 2, tzinfo=UTC)


def _candidate() -> object:
    """A minimal retrieval Candidate (built inline — no cross-package test helper)."""
    from mcp_knowledge.retrieval.hybrid import Candidate

    return Candidate(
        chunk_id=1,
        document_id="11111111-1111-4111-8111-111111111111",
        chunk_index=0,
        content="The payment worker retries failed transactions with exponential backoff.",
        heading_path="Payments > Retry policy",
        source_type="confluence",
        source_id="100100",
        source_uri="https://tnexwm.atlassian.net/wiki/spaces/ENG/pages/100100",
        title="Payment retry policy",
        container="ENG",
        author="svc",
        source_updated_at=_NOW,
        ingested_at=_NOW,
        rrf_score=0.9,
    )


def test_TC130_harness_runs_and_reports_verdict_distribution_only() -> None:
    """The harness runs (on the fake/deterministic provider) and reports a verdict distribution."""
    cases = [
        eval_harness.EvalCase(
            id="Q-001", question="has evidence?", expect="FACT", candidates=[_candidate()]
        ),
        eval_harness.EvalCase(id="Q-002", question="no evidence?", expect="UNKNOWN", candidates=[]),
    ]
    report = eval_harness.run_eval(cases, now=_NOW)
    # a verdict distribution is reported (the measurable-now part)
    assert report.cases == 2
    assert sum(report.verdict_counts.values()) >= 2
    assert report.verdict_counts.get("UNKNOWN", 0) >= 1


def test_TC130_report_is_uncalibrated_and_invents_no_recall_or_tau() -> None:
    """calibration_status=uncalibrated and recall is None — never an invented number (L-002)."""
    report = eval_harness.run_eval([], now=_NOW)
    assert report.calibration_status == "uncalibrated"
    assert report.recall is None  # UNVERIFIED until a measured golden-set runs post-egress


def test_TC130_threshold_tbd_marker_is_grep_able() -> None:
    """The τ source constant carries the grep-able `# THRESHOLD TBD` marker (L-002/E-004)."""
    source = inspect.getsource(eval_harness)
    assert "# THRESHOLD TBD (NFR-010, L-002)" in source
