"""T-044: prompt `incident_investigation` + server instructions (FR-009, ADR-0014)."""

from __future__ import annotations

import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from mcp_cloudwatch.prompts import INCIDENT_INVESTIGATION_TEMPLATE
from mcp_cloudwatch.server import SERVER_INSTRUCTIONS, build_server
from mcp_common.contract_testing import load_contract, operations_by_id

ARGS = {
    "service": "payment",
    "time_from": "2026-09-30T10:00:00Z",
    "time_to": "2026-09-30T12:00:00Z",
}


@pytest.fixture
def server(read_api):
    return build_server(read_api)


@pytest.mark.asyncio
async def test_FR_009_AC_001_prompts_list_has_incident_investigation_with_three_arguments(
    server,
) -> None:
    async with create_connected_server_and_client_session(server) as session:
        prompts = (await session.list_prompts()).prompts
    assert [p.name for p in prompts] == ["incident_investigation"]
    assert [(a.name, a.required) for a in prompts[0].arguments or []] == [
        ("service", True), ("time_from", True), ("time_to", True),
    ]  # fmt: skip


@pytest.mark.asyncio
async def test_FR_009_AC_001_prompt_orchestrates_all_sources_with_the_given_window(server) -> None:
    async with create_connected_server_and_client_session(server) as session:
        result = await session.get_prompt("incident_investigation", ARGS)
    text = result.messages[0].content.text
    assert "payment" in text and ARGS["time_from"] in text and ARGS["time_to"] in text
    for tool in (
        "cloudwatch_describe_alarms", "cloudwatch_get_metric_data", "opensearch_search_logs",
        "opensearch_count", "kibana_find_saved_objects", "kibana_build_dashboard_link",
        "kafka_describe_consumer_group", "redis_server_info",
    ):  # fmt: skip
        assert tool in text, tool
    assert "Nguồn:" in text and "citation" in text and "một citation" in text


@pytest.mark.asyncio
async def test_FR_009_AC_002_prompt_demands_one_line_per_empty_source(server) -> None:
    async with create_connected_server_and_client_session(server) as session:
        result = await session.get_prompt("incident_investigation", ARGS)
    text = result.messages[0].content.text
    assert "status=empty" in text and "MỘT dòng" in text
    assert "Không có alarm CloudWatch trong khung giờ này" in text
    assert "Không có log OpenSearch" in text and "Không tìm thấy dashboard Kibana" in text


def test_prompt_only_names_tools_that_exist_in_the_contract() -> None:
    import re

    operations = operations_by_id(load_contract())
    named = set(
        re.findall(
            r"\b(?:cloudwatch|opensearch|kibana|kafka|redis)_[a-z_]+\b",
            INCIDENT_INVESTIGATION_TEMPLATE,
        )
    )
    assert named and named <= set(operations), named - set(operations)


def test_server_instructions_remind_citation_discipline(server) -> None:
    assert "Nguồn" in SERVER_INSTRUCTIONS and "empty" in SERVER_INSTRUCTIONS
    assert server.instructions == SERVER_INSTRUCTIONS
