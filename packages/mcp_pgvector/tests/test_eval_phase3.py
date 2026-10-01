"""T-084: the Phase 3 / Journey 3 eval question set in `eval/questions.yaml`.

The questions are run by hand through the `semantic_synthesis` prompt (the final answer is
Claude's, reviewed by a human — NFR-003). What is automatable, and what this test guards, is that
the set is well-formed, includes the two cases that must be told apart (nothing indexed vs. filters
excluded everything), a queue-state case that needs an ARN, and only names tools that exist in
`api-contract.yaml`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from mcp_common.contract_testing import load_contract, operations_by_id

ROOT = Path(__file__).resolve().parents[3]
REQUIRED = (
    "id", "question", "covers", "queue_state", "expect_no_index_data", "filter_excludes_all",
    "expected_tools", "expected_citations", "expected_behavior",
)  # fmt: skip


@pytest.fixture(scope="module")
def questions() -> list[dict]:
    data = yaml.safe_load((ROOT / "eval" / "questions.yaml").read_text(encoding="utf-8"))
    return list(data["phase3_questions"])


def test_FR_013_at_least_ten_questions_with_every_field_and_unique_ids(questions) -> None:
    assert len(questions) >= 10
    ids = [q["id"] for q in questions]
    assert len(set(ids)) == len(ids) and all(i.startswith("Q-J3-") for i in ids)
    for q in questions:
        assert not [f for f in REQUIRED if f not in q], q["id"]
        assert set(q["expected_citations"]) <= {"source_uri", "arn"}, q["id"]


def test_FR_013_AC_002_no_index_data_and_filter_excluded_are_separate_cases(questions) -> None:
    nothing = [q for q in questions if q["expect_no_index_data"]]
    filtered = [q for q in questions if q["filter_excludes_all"]]
    assert nothing and filtered
    assert not {q["id"] for q in nothing} & {q["id"] for q in filtered}
    for q in nothing:
        assert (
            q["expected_citations"] == []
            and "không tìm thấy dữ liệu đã index" in q["expected_behavior"]
        ), q["id"]
    for q in filtered:
        assert "BỘ LỌC" in q["expected_behavior"] and "kb_semantic_search" in q["expected_tools"]


def test_FR_013_AC_001_queue_state_cases_cite_an_arn_and_source_cases_cite_the_original_url(
    questions,
) -> None:
    queue = [q for q in questions if q["queue_state"] and not q["expect_no_index_data"]]
    assert queue and all("arn" in q["expected_citations"] for q in queue)
    assert any(
        "sqs_get_queue_attributes" in q["expected_tools"]
        and "source_uri" in q["expected_citations"]
        for q in queue
    )
    sourced = [q for q in questions if "source_uri" in q["expected_citations"]]
    assert len(sourced) >= 5
    assert all("document_id" not in q["expected_behavior"] or "không phải" in q["expected_behavior"]
               for q in sourced)  # fmt: skip


def test_every_expected_tool_exists_in_the_contract_and_is_readonly(questions) -> None:
    operations = operations_by_id(load_contract())
    for q in questions:
        for tool in q["expected_tools"]:
            assert tool in operations, (q["id"], tool)
            assert operations[tool].get("x-readonly") is True, (q["id"], tool)


def test_the_set_covers_freshness_secret_and_restricted_content(questions) -> None:
    covers = {c for q in questions for c in q["covers"]}
    assert {"NFR-004", "R4", "BR-003", "BR-005", "FR-015/AC-002"} <= covers
