"""T-042/T-043/T-045: `tools.snapshot.json` + `api-contract.yaml` (ADR-0013), 4 result branches.

Every tool is called through the real MCP protocol (in-memory client session) and its
`structuredContent` is validated against the contract's response schema.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from cw_helpers import GROUP, OK_CALLS, WINDOW_ARGS, Stubs, stub_ok
from mcp.shared.memory import create_connected_server_and_client_session
from mcp_cloudwatch.read_api import CloudWatchReadApi
from mcp_cloudwatch.server import build_server
from mcp_common.config import CommonSettings
from mcp_common.contract_testing import (
    assert_snapshot_matches_contract,
    build_snapshot,
    load_contract,
    validate_structured_content,
)

import mcp_cloudwatch

SNAPSHOT_PATH = Path(mcp_cloudwatch.__file__).parent / "tools.snapshot.json"
CONTRACT = load_contract()
EXPECTED_TOOLS = [
    "cloudwatch_describe_alarm_history",
    "cloudwatch_describe_alarms",
    "cloudwatch_filter_log_events",
    "cloudwatch_get_metric_data",
    "cloudwatch_list_log_groups",
    "cloudwatch_list_metrics",
    "cloudwatch_run_logs_insights",
]


def _snapshot() -> dict[str, Any]:
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def server(read_api: CloudWatchReadApi):
    return build_server(read_api)


async def _call(server, name: str, args: dict[str, Any]):
    async with create_connected_server_and_client_session(server) as session:
        return await session.call_tool(name, arguments=args)


@pytest.mark.asyncio
async def test_snapshot_file_matches_live_tool_surface(read_api: CloudWatchReadApi) -> None:
    live = await build_snapshot(build_server(read_api))
    assert _snapshot() == json.loads(json.dumps(live)), (
        "tools.snapshot.json is stale; regenerate with `uv run mcp-cloudwatch tools-dump`"
    )


def test_ADR_0013_snapshot_matches_contract_operations() -> None:
    assert_snapshot_matches_contract(_snapshot(), CONTRACT, tag="cloudwatch")


def test_snapshot_has_exactly_seven_tools_within_r5_budget() -> None:
    assert sorted(_snapshot()) == EXPECTED_TOOLS and len(_snapshot()) <= 12


def test_tool_descriptions_are_at_most_three_sentences() -> None:
    for name, tool in _snapshot().items():
        sentences = [s for s in tool["description"].replace("...", "").split(". ") if s.strip()]
        assert len(sentences) <= 3, name


@pytest.mark.parametrize(("tool", "args"), OK_CALLS, ids=[c[0] for c in OK_CALLS])
@pytest.mark.asyncio
async def test_FR_006_AC_001_ok_branch_validates_against_contract(
    server, stubs: Stubs, tool: str, args: dict[str, Any]
) -> None:
    stub_ok(stubs, tool)
    result = await _call(server, tool, args)
    assert not result.isError, result.content
    validate_structured_content(CONTRACT, tool, result.structuredContent)
    assert result.structuredContent["status"] == "ok"
    assert result.structuredContent["meta"]["source"] == "cloudwatch"
    assert "Nguồn:" in result.content[0].text
    stubs.assert_all_consumed()


EMPTY: list[tuple[str, dict[str, Any], list[tuple[str, str, dict]]]] = [
    ("cloudwatch_list_log_groups", {}, [("logs", "describe_log_groups", {"logGroups": []})]),
    (
        "cloudwatch_filter_log_events",
        {"log_group_name": GROUP, **WINDOW_ARGS},
        [("logs", "filter_log_events", {"events": []})],
    ),
    (
        "cloudwatch_run_logs_insights",
        OK_CALLS[2][1],
        [("logs", "start_query", {"queryId": "q"}),
         ("logs", "get_query_results", {"status": "Complete", "results": []})],
    ),
    ("cloudwatch_list_metrics", {}, [("cloudwatch", "list_metrics", {"Metrics": []})]),
    ("cloudwatch_describe_alarms", {}, [("cloudwatch", "describe_alarms", {"MetricAlarms": []})]),
]  # fmt: skip


@pytest.mark.parametrize(("tool", "args", "queue"), EMPTY, ids=[c[0] for c in EMPTY])
@pytest.mark.asyncio
async def test_FR_006_AC_002_empty_branch_validates_against_contract(
    server, stubs: Stubs, tool: str, args: dict[str, Any], queue: list
) -> None:
    for service, operation, response in queue:
        stubs.add(service, operation, response)
    result = await _call(server, tool, args)
    assert not result.isError
    validate_structured_content(CONTRACT, tool, result.structuredContent)
    payload = result.structuredContent
    assert payload["status"] == "empty" and payload["items"] == [] and payload["citations"] == []
    assert payload["meta"]["query_echo"] is not None
    assert result.content[0].text.startswith("Không tìm thấy")


NOT_FOUND: list[tuple[str, dict[str, Any], list[tuple[str, str, dict | str]]]] = [
    (
        "cloudwatch_filter_log_events",
        {"log_group_name": "/nope", **WINDOW_ARGS},
        [("logs", "filter_log_events", "ResourceNotFoundException")],
    ),
    (
        "cloudwatch_run_logs_insights",
        {**OK_CALLS[2][1], "log_group_names": ["/nope"]},
        [("logs", "start_query", "ResourceNotFoundException")],
    ),
    (
        "cloudwatch_get_metric_data",
        {"namespace": "Nope", "metric_name": "X", **WINDOW_ARGS},
        [("cloudwatch", "list_metrics", {"Metrics": []})],
    ),
    (
        "cloudwatch_describe_alarm_history",
        {"alarm_name": "nope", **WINDOW_ARGS},
        [("cloudwatch", "describe_alarms", {"MetricAlarms": [], "CompositeAlarms": []})],
    ),
]  # fmt: skip


@pytest.mark.parametrize(("tool", "args", "queue"), NOT_FOUND, ids=[c[0] for c in NOT_FOUND])
@pytest.mark.asyncio
async def test_FR_006_AC_002_not_found_branch_validates_against_contract(
    server, stubs: Stubs, tool: str, args: dict[str, Any], queue: list
) -> None:
    for service, operation, response in queue:
        if isinstance(response, str):
            stubs.error(service, operation, response)
        else:
            stubs.add(service, operation, response)
    result = await _call(server, tool, args)
    assert not result.isError
    validate_structured_content(CONTRACT, tool, result.structuredContent)
    assert result.structuredContent["status"] == "not_found"
    assert "không tồn tại" in result.content[0].text


@pytest.mark.asyncio
async def test_error_branches_validate_against_contract(server, stubs: Stubs) -> None:
    stubs.error("logs", "describe_log_groups", "AccessDeniedException", status=403)
    forbidden = await _call(server, "cloudwatch_list_log_groups", {})
    stubs.error("cloudwatch", "describe_alarms", "ThrottlingException")
    throttled = await _call(server, "cloudwatch_describe_alarms", {})
    invalid = await _call(
        server, "cloudwatch_filter_log_events",
        {"log_group_name": GROUP, "time_from": "2026-09-30T12:00:00Z",
         "time_to": "2026-09-30T10:00:00Z"},
    )  # fmt: skip
    not_permitted = await _call(
        server, "cloudwatch_run_logs_insights", {**OK_CALLS[2][1], "query": "fields a | delete b"}
    )
    for res, tool, code in [
        (forbidden, "cloudwatch_list_log_groups", "forbidden"),
        (throttled, "cloudwatch_describe_alarms", "rate_limited"),
        (invalid, "cloudwatch_filter_log_events", "invalid_input"),
        (not_permitted, "cloudwatch_run_logs_insights", "not_permitted"),
    ]:
        assert res.isError
        validate_structured_content(CONTRACT, tool, res.structuredContent, is_error=True)
        assert res.structuredContent["error"]["code"] == code
        assert res.structuredContent["error"]["source"] == "cloudwatch"
    assert invalid.structuredContent["error"]["details"]["field"] == "time_from"
    assert not_permitted.structuredContent["error"]["details"]["operation"] == (
        "insights command 'delete'"
    )


@pytest.mark.asyncio
async def test_unconfigured_server_reports_source_misconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MCP_CLOUDWATCH_REGION", raising=False)
    result = await _call(build_server(common=CommonSettings()), "cloudwatch_list_log_groups", {})
    assert result.isError
    error = result.structuredContent["error"]
    assert error["code"] == "source_misconfigured"
    assert "MCP_CLOUDWATCH_REGION" in error["details"]["missing_env"]
