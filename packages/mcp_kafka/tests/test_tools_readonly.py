"""T-048 / NFR-001: the read-only surface of mcp-kafka (FR-007/AC-003, FR-014/AC-001/002)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from kafka_helpers import OK_CALLS, FakeCluster
from mcp.shared.memory import create_connected_server_and_client_session
from mcp_common.contract_testing import load_contract, operations_by_id
from mcp_common.testing import (
    assert_readonly_tool_surface,
    assert_unknown_tool_rejected_at_protocol_layer,
)
from mcp_common.tooling import registered_tool_functions
from mcp_kafka.read_api import KafkaReadApi
from mcp_kafka.server import build_server

import mcp_kafka

PACKAGE_DIR = Path(mcp_kafka.__file__).parent
CONTRACT = load_contract()


@pytest.fixture
def server(read_api: KafkaReadApi):
    return build_server(read_api)


def test_FR_014_AC_001_tool_surface_is_readonly_and_in_contract(server) -> None:
    snapshot = json.loads((PACKAGE_DIR / "tools.snapshot.json").read_text(encoding="utf-8"))
    assert_readonly_tool_surface(
        registered_tools=registered_tool_functions(server),
        contract_operations=operations_by_id(CONTRACT),
        snapshot=snapshot,
    )


@pytest.mark.parametrize(
    "write_tool",
    [
        "kafka_produce_message",
        "kafka_create_topic",
        "kafka_delete_topic",
        "kafka_commit_offsets",
        "kafka_reset_offsets",
        "kafka_delete_consumer_group",
        "kafka_alter_config",
    ],
)
@pytest.mark.asyncio
async def test_FR_014_AC_002_unknown_write_tool_rejected_at_protocol_layer(
    server, write_tool: str
) -> None:
    await assert_unknown_tool_rejected_at_protocol_layer(server, write_tool, {"topic": "x"})


@pytest.mark.asyncio
async def test_FR_007_AC_003_a_full_tool_sweep_never_commits_subscribes_or_creates(
    server, cluster: FakeCluster
) -> None:
    topics_before = sorted(cluster.topics)
    offsets_before = {g: dict(i["offsets"]) for g, i in cluster.groups.items()}
    async with create_connected_server_and_client_session(server) as session:
        for name, args in OK_CALLS:
            result = await session.call_tool(name, arguments=args)
            assert not result.isError, (name, result.content)
        for name, args in [
            ("kafka_describe_topic", {"topic": "ghost.topic"}),
            ("kafka_peek_messages", {"topic": "ghost.topic"}),
            ("kafka_describe_consumer_group", {"group_id": "ghost-group"}),
        ]:
            await session.call_tool(name, arguments=args)
    assert cluster.forbidden_calls == []  # commit / subscribe / store_offsets tripwires
    assert sorted(cluster.topics) == topics_before and cluster.created == []
    assert {g: i["offsets"] for g, i in cluster.groups.items()} == offsets_before
    assert all(kw["topic"] is None for n, kw in cluster.calls if n == "list_topics")
    assert all(c["enable.auto.commit"] is False for c in cluster.consumer_confs)
    assert all(c["allow.auto.create.topics"] is False for c in cluster.consumer_confs)
    assert all(c["allow.auto.create.topics"] is False for c in cluster.admin_confs)
    group_ids = [c["group.id"] for c in cluster.consumer_confs]
    assert group_ids and len(set(group_ids)) == len(group_ids)  # throw-away each time
    assert not any(g in group_ids for g in cluster.groups)  # never impersonates a real group
