"""T-060/T-061: `tools.snapshot.json` + `api-contract.yaml` (ADR-0013), 4 result branches.

Every tool is called through the real MCP protocol (in-memory client session) and its
`structuredContent` is validated against the contract's response schema.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from mcp_common.contract_testing import (
    assert_snapshot_matches_contract,
    build_snapshot,
    load_contract,
    validate_structured_content,
)
from mcp_sqs_sns.read_api import SqsSnsReadApi
from mcp_sqs_sns.server import build_server
from sqs_helpers import DLQ, OK_CALLS, QUEUE, TOPIC_ARN, Stubs, stub_ok

import mcp_sqs_sns

SNAPSHOT_PATH = Path(mcp_sqs_sns.__file__).parent / "tools.snapshot.json"
CONTRACT = load_contract()
EXPECTED_TOOLS = [
    "sns_get_topic_attributes",
    "sns_list_subscriptions_by_topic",
    "sns_list_topics",
    "sqs_get_queue_attributes",
    "sqs_list_dead_letter_source_queues",
    "sqs_list_queues",
]
SOURCE_OF = {name: name.split("_", 1)[0] for name in EXPECTED_TOOLS}


def _snapshot() -> dict[str, Any]:
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def server(read_api: SqsSnsReadApi):
    return build_server(read_api)


async def _call(server, name: str, args: dict[str, Any]):
    async with create_connected_server_and_client_session(server) as session:
        return await session.call_tool(name, arguments=args)


@pytest.mark.asyncio
async def test_snapshot_file_matches_live_tool_surface(read_api: SqsSnsReadApi) -> None:
    live = await build_snapshot(build_server(read_api))
    assert _snapshot() == json.loads(json.dumps(live)), (
        "tools.snapshot.json is stale; regenerate with `uv run mcp-sqs-sns tools-dump`"
    )


def test_ADR_0013_snapshot_matches_contract_operations() -> None:
    assert_snapshot_matches_contract(_snapshot(), CONTRACT, tag="sqs-sns")


def test_snapshot_has_exactly_six_tools_within_r5_budget() -> None:
    assert sorted(_snapshot()) == EXPECTED_TOOLS and len(_snapshot()) <= 12


def test_tool_descriptions_are_at_most_three_sentences() -> None:
    for name, tool in _snapshot().items():
        sentences = [s for s in tool["description"].replace("...", "").split(". ") if s.strip()]
        assert len(sentences) <= 3, name


def test_no_tool_offers_to_read_message_content() -> None:
    for name, tool in _snapshot().items():
        assert "receive" not in name and "message" not in name.replace("messages", "")
        assert "body" not in tool["inputSchema"].get("properties", {})


@pytest.mark.parametrize(("tool", "args"), OK_CALLS, ids=[c[0] for c in OK_CALLS])
@pytest.mark.asyncio
async def test_FR_010_AC_001_ok_branch_validates_against_contract(
    server, stubs: Stubs, tool: str, args: dict[str, Any]
) -> None:
    stub_ok(stubs, tool)
    result = await _call(server, tool, args)
    assert not result.isError, result.content
    validate_structured_content(CONTRACT, tool, result.structuredContent)
    assert result.structuredContent["status"] == "ok"
    assert result.structuredContent["meta"]["source"] == SOURCE_OF[tool]
    assert "Nguồn:" in result.content[0].text
    stubs.assert_all_consumed()


@pytest.mark.asyncio
async def test_FR_010_AC_001_queue_text_shows_the_approximate_count_and_arn(
    server, stubs: Stubs
) -> None:
    stub_ok(stubs, "sqs_get_queue_attributes")
    result = await _call(server, "sqs_get_queue_attributes", {"queue_name": QUEUE})
    text = result.content[0].text
    assert "1842" in text and f"arn:aws:sqs:ap-southeast-1:123456789012:{QUEUE}" in text


EMPTY: list[tuple[str, dict[str, Any], list[tuple[str, str, dict]]]] = [
    ("sqs_list_queues", {}, [("sqs", "list_queues", {})]),
    (
        "sqs_list_dead_letter_source_queues", {"queue_url": "https://sqs.r.amazonaws.com/1/dlq"},
        [("sqs", "list_dead_letter_source_queues", {"queueUrls": []})],
    ),
    ("sns_list_topics", {}, [("sns", "list_topics", {"Topics": []})]),
    (
        "sns_list_subscriptions_by_topic", {"topic_arn": TOPIC_ARN},
        [("sns", "list_subscriptions_by_topic", {"Subscriptions": []})],
    ),
]  # fmt: skip


@pytest.mark.parametrize(("tool", "args", "queue"), EMPTY, ids=[c[0] for c in EMPTY])
@pytest.mark.asyncio
async def test_FR_010_AC_002_empty_branch_validates_against_contract(
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


NOT_FOUND: list[tuple[str, dict[str, Any], list[tuple[str, str, str]]]] = [
    (
        "sqs_get_queue_attributes", {"queue_name": "nope"},
        [("sqs", "get_queue_url", "AWS.SimpleQueueService.NonExistentQueue")],
    ),
    (
        "sqs_list_dead_letter_source_queues", {"queue_name": "nope"},
        [("sqs", "get_queue_url", "QueueDoesNotExist")],
    ),
    (
        "sns_get_topic_attributes", {"topic_arn": TOPIC_ARN},
        [("sns", "get_topic_attributes", "NotFound")],
    ),
    (
        "sns_list_subscriptions_by_topic", {"topic_arn": TOPIC_ARN},
        [("sns", "list_subscriptions_by_topic", "NotFound")],
    ),
]  # fmt: skip


@pytest.mark.parametrize(("tool", "args", "queue"), NOT_FOUND, ids=[c[0] for c in NOT_FOUND])
@pytest.mark.asyncio
async def test_FR_010_AC_002_not_found_branch_validates_against_contract(
    server, stubs: Stubs, tool: str, args: dict[str, Any], queue: list
) -> None:
    for service, operation, code in queue:
        stubs.error(service, operation, code, status=404)
    result = await _call(server, tool, args)
    assert not result.isError
    validate_structured_content(CONTRACT, tool, result.structuredContent)
    assert result.structuredContent["status"] == "not_found"
    assert "không tồn tại" in result.content[0].text


@pytest.mark.asyncio
async def test_error_branches_validate_against_contract(server, stubs: Stubs) -> None:
    stubs.error("sqs", "list_queues", "AccessDenied", status=403)
    forbidden = await _call(server, "sqs_list_queues", {})
    stubs.error("sns", "list_topics", "Throttling")
    throttled = await _call(server, "sns_list_topics", {})
    invalid = await _call(server, "sqs_get_queue_attributes", {})
    bad_arn = await _call(server, "sns_get_topic_attributes", {"topic_arn": "nope"})
    for res, tool, code, source in [
        (forbidden, "sqs_list_queues", "forbidden", "sqs"),
        (throttled, "sns_list_topics", "rate_limited", "sns"),
        (invalid, "sqs_get_queue_attributes", "invalid_input", "sqs"),
    ]:
        assert res.isError
        validate_structured_content(CONTRACT, tool, res.structuredContent, is_error=True)
        assert res.structuredContent["error"]["code"] == code
        assert res.structuredContent["error"]["source"] == source
    assert invalid.structuredContent["error"]["details"]["field"] == "queue_name"
    assert bad_arn.isError  # rejected by the input schema (pattern) before reaching AWS


@pytest.mark.asyncio
async def test_queue_url_wins_over_queue_name_through_the_protocol(server, stubs: Stubs) -> None:
    stub_ok(stubs, "sqs_list_dead_letter_source_queues")
    stubs.stubbers["sqs"]._queue.clear()  # noqa: SLF001 - drop the GetQueueUrl stub: not used
    stubs.add("sqs", "list_dead_letter_source_queues", {"queueUrls": []})
    result = await _call(
        server, "sqs_list_dead_letter_source_queues",
        {"queue_name": "ignored", "queue_url": f"https://sqs.r.amazonaws.com/1/{DLQ}"},
    )  # fmt: skip
    assert not result.isError and result.structuredContent["status"] == "empty"


@pytest.mark.asyncio
async def test_unconfigured_server_reports_source_misconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mcp_common.config import CommonSettings

    monkeypatch.delenv("MCP_SQS_SNS_REGION", raising=False)
    result = await _call(build_server(common=CommonSettings()), "sqs_list_queues", {})
    assert result.isError
    error = result.structuredContent["error"]
    assert error["code"] == "source_misconfigured"
    assert "MCP_SQS_SNS_REGION" in error["details"]["missing_env"]
