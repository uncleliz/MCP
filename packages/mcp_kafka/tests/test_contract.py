"""T-047/T-048: `tools.snapshot.json` + `api-contract.yaml` (ADR-0013), 4 result branches.

Every tool is called through the real MCP protocol (in-memory client session) and its
`structuredContent` is validated against the contract's response schema.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from confluent_kafka import KafkaError
from kafka_helpers import OK_CALLS, TOPIC, FakeAdmin, FakeCluster, FakeConsumer, kafka_error
from mcp.shared.memory import create_connected_server_and_client_session
from mcp_common.config import CommonSettings
from mcp_common.contract_testing import (
    assert_snapshot_matches_contract,
    build_snapshot,
    load_contract,
    validate_structured_content,
)
from mcp_kafka.client import ConfluentKafkaReader, KafkaClient
from mcp_kafka.read_api import KafkaReadApi
from mcp_kafka.server import build_server
from mcp_kafka.settings import Settings

import mcp_kafka

SNAPSHOT_PATH = Path(mcp_kafka.__file__).parent / "tools.snapshot.json"
CONTRACT = load_contract()
EXPECTED_TOOLS = [
    "kafka_describe_consumer_group",
    "kafka_describe_topic",
    "kafka_list_consumer_groups",
    "kafka_list_topics",
    "kafka_peek_messages",
]


def _snapshot() -> dict[str, Any]:
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def server(read_api: KafkaReadApi):
    return build_server(read_api)


async def _call(server, name: str, args: dict[str, Any]):
    async with create_connected_server_and_client_session(server) as session:
        return await session.call_tool(name, arguments=args)


@pytest.mark.asyncio
async def test_snapshot_file_matches_live_tool_surface(read_api: KafkaReadApi) -> None:
    live = await build_snapshot(build_server(read_api))
    assert _snapshot() == json.loads(json.dumps(live)), (
        "tools.snapshot.json is stale; regenerate with `uv run mcp-kafka tools-dump`"
    )


def test_ADR_0013_snapshot_matches_contract_operations() -> None:
    assert_snapshot_matches_contract(_snapshot(), CONTRACT, tag="kafka")


def test_snapshot_has_exactly_five_tools_within_r5_budget() -> None:
    assert sorted(_snapshot()) == EXPECTED_TOOLS and len(_snapshot()) <= 12


def test_peek_schema_uses_the_contract_field_name_from() -> None:
    properties = _snapshot()["kafka_peek_messages"]["inputSchema"]["properties"]
    assert "from" in properties and "from_" not in properties
    variants = properties["offset"]["anyOf"]
    assert any(v.get("pattern") == "^[0-9]{1,19}$" for v in variants)


def test_tool_descriptions_are_at_most_three_sentences() -> None:
    for name, tool in _snapshot().items():
        sentences = [s for s in tool["description"].replace("...", "").split(". ") if s.strip()]
        assert len(sentences) <= 3, name


@pytest.mark.parametrize(("tool", "args"), OK_CALLS, ids=[c[0] for c in OK_CALLS])
@pytest.mark.asyncio
async def test_FR_007_AC_001_ok_branch_validates_against_contract(
    server, tool: str, args: dict[str, Any]
) -> None:
    result = await _call(server, tool, args)
    assert not result.isError, result.content
    validate_structured_content(CONTRACT, tool, result.structuredContent)
    assert result.structuredContent["status"] == "ok"
    assert result.structuredContent["meta"]["source"] == "kafka"
    assert "Nguồn:" in result.content[0].text


@pytest.mark.asyncio
async def test_peek_alias_from_reaches_the_tool_function(server) -> None:
    args = {"topic": TOPIC, "partition": 0, "from": "earliest", "limit": 1}
    earliest = await _call(server, "kafka_peek_messages", args)
    assert earliest.structuredContent["items"][0]["offset"] == "1000"
    assert earliest.structuredContent["meta"]["query_echo"]["from"] == "earliest"
    args = {"topic": TOPIC, "partition": 0, "from": "offset", "offset": "1003"}
    offset = await _call(server, "kafka_peek_messages", args)
    assert [i["offset"] for i in offset.structuredContent["items"]] == ["1003"]


EMPTY: list[tuple[str, dict[str, Any]]] = [
    ("kafka_list_topics", {"pattern": "zzz.*"}),
    ("kafka_peek_messages", {"topic": "empty.topic"}),
    ("kafka_list_consumer_groups", {"pattern": "zzz*"}),
]


@pytest.mark.parametrize(("tool", "args"), EMPTY, ids=[c[0] for c in EMPTY])
@pytest.mark.asyncio
async def test_FR_007_AC_002_empty_branch_validates_against_contract(
    server, tool: str, args: dict[str, Any]
) -> None:
    result = await _call(server, tool, args)
    assert not result.isError
    validate_structured_content(CONTRACT, tool, result.structuredContent)
    payload = result.structuredContent
    assert payload["status"] == "empty" and payload["items"] == [] and payload["citations"] == []
    assert payload["meta"]["query_echo"]
    assert result.content[0].text.startswith("Không tìm thấy")


NOT_FOUND = [
    ("kafka_describe_topic", {"topic": "does.not.exist"}),
    ("kafka_peek_messages", {"topic": "does.not.exist"}),
    ("kafka_describe_consumer_group", {"group_id": "no-such-group"}),
]


@pytest.mark.parametrize(("tool", "args"), NOT_FOUND, ids=[c[0] for c in NOT_FOUND])
@pytest.mark.asyncio
async def test_FR_007_AC_002_not_found_branch_validates_and_creates_nothing(
    server, cluster: FakeCluster, tool: str, args: dict[str, Any]
) -> None:
    before = sorted(cluster.topics)
    result = await _call(server, tool, args)
    assert not result.isError
    validate_structured_content(CONTRACT, tool, result.structuredContent)
    assert result.structuredContent["status"] == "not_found"
    assert "không tồn tại" in result.content[0].text
    assert sorted(cluster.topics) == before and cluster.created == []  # R17 through the protocol


@pytest.mark.asyncio
async def test_error_branches_validate_against_contract(
    settings: Settings, common: CommonSettings, cluster: FakeCluster
) -> None:
    reader = ConfluentKafkaReader(
        settings,
        admin_factory=lambda conf: FakeAdmin(conf, cluster),
        consumer_factory=lambda conf: FakeConsumer(conf, cluster),
    )
    server = build_server(KafkaReadApi(KafkaClient(settings, common=common, reader=reader), common))
    cluster.fail = kafka_error(KafkaError._TRANSPORT)
    unavailable = await _call(server, "kafka_list_topics", {})
    cluster.fail = kafka_error(KafkaError._TIMED_OUT)
    timeout = await _call(server, "kafka_describe_topic", {"topic": TOPIC})
    cluster.fail = kafka_error(KafkaError.TOPIC_AUTHORIZATION_FAILED)
    forbidden = await _call(server, "kafka_list_topics", {})
    cluster.fail = None
    invalid = await _call(server, "kafka_peek_messages", {"topic": TOPIC, "from": "offset"})
    for res, tool, code in [
        (unavailable, "kafka_list_topics", "upstream_unavailable"),
        (timeout, "kafka_describe_topic", "upstream_timeout"),
        (forbidden, "kafka_list_topics", "forbidden"),
        (invalid, "kafka_peek_messages", "invalid_input"),
    ]:
        assert res.isError
        validate_structured_content(CONTRACT, tool, res.structuredContent, is_error=True)
        assert res.structuredContent["error"]["code"] == code
        assert res.structuredContent["error"]["source"] == "kafka"
    assert "VPN" in unavailable.structuredContent["error"]["details"]["hint"]
    assert invalid.structuredContent["error"]["details"]["field"] == "offset"


@pytest.mark.asyncio
async def test_unconfigured_server_reports_source_misconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MCP_KAFKA_BOOTSTRAP_SERVERS", raising=False)
    result = await _call(build_server(common=CommonSettings()), "kafka_list_topics", {})
    assert result.isError
    error = result.structuredContent["error"]
    assert error["code"] == "source_misconfigured"
    assert "MCP_KAFKA_BOOTSTRAP_SERVERS" in error["details"]["missing_env"]
