"""T-052: the Phase 2 / Journey 2 eval question set in `eval/questions.yaml`.

The questions are run by hand through the `incident_investigation` prompt (there is no runner:
the final answer is Claude's, reviewed by a human — NFR-003). What *is* automatable, and what
this test guards, is that the set is well-formed, covers the cases the ACs need (one-source-empty,
Kafka lag, Redis cache), and only names tools that exist in `api-contract.yaml`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from mcp_common.contract_testing import load_contract, operations_by_id

ROOT = Path(__file__).resolve().parents[3]
SOURCES = {"cloudwatch", "opensearch", "kibana", "kafka", "redis"}
REQUIRED = (
    "id", "question", "covers", "service", "time_from", "time_to", "expected_sources",
    "optional_sources", "expect_empty", "expected_tools", "expected_behavior",
)  # fmt: skip


@pytest.fixture(scope="module")
def questions() -> list[dict]:
    data = yaml.safe_load((ROOT / "eval" / "questions.yaml").read_text(encoding="utf-8"))
    return list(data["phase2_questions"])


def test_FR_009_at_least_ten_questions_with_every_field_and_unique_ids(questions) -> None:
    assert len(questions) >= 10
    ids = [q["id"] for q in questions]
    assert len(set(ids)) == len(ids) and all(i.startswith("Q-J2-") for i in ids)
    for q in questions:
        assert not [f for f in REQUIRED if f not in q], q["id"]
        assert set(q["expected_sources"]) | set(q["optional_sources"]) | set(q["expect_empty"]) <= (
            SOURCES
        ), q["id"]


def test_FR_009_AC_002_there_is_a_case_per_single_empty_source_and_an_all_empty_case(
    questions,
) -> None:
    empty_sets = [sorted(q["expect_empty"]) for q in questions]
    for source in ("cloudwatch", "opensearch", "kibana"):
        assert [source] in empty_sets, source
    assert ["cloudwatch", "kibana", "opensearch"] in empty_sets
    first = next(q for q in questions if q["expect_empty"] == ["cloudwatch"])
    assert "MỘT dòng" in first["expected_behavior"] or "một dòng" in first["expected_behavior"]


def test_FR_009_includes_kafka_lag_and_redis_cache_cases(questions) -> None:
    kafka = [q for q in questions if "kafka_describe_consumer_group" in q["expected_tools"]]
    redis = [q for q in questions if "redis_server_info" in q["expected_tools"]]
    assert kafka and redis
    assert all("kafka" in q["optional_sources"] for q in kafka)
    assert all("redis" in q["optional_sources"] for q in redis)


def test_FR_015_cases_cover_partial_invalid_window_and_redaction(questions) -> None:
    text = " ".join(q["expected_behavior"] for q in questions)
    assert "status=partial" in text and "invalid_input" in text and "redacted" in text
    reversed_window = [q for q in questions if q["time_from"] > q["time_to"]]
    assert reversed_window


def test_expected_tools_all_exist_in_the_contract_and_are_readonly(questions) -> None:
    operations = operations_by_id(load_contract())
    for q in questions:
        for tool in q["expected_tools"]:
            assert tool in operations, (q["id"], tool)
            assert operations[tool]["x-readonly"] is True
