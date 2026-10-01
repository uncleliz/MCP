"""T-053 / NFR-002 + R16: dead Kafka fails fast; an exhausted executor reports itself.

Budget (ADR-0009 A3 / ADR-0006 A2): `socket.timeout.ms=8000`, per-call timeout 8s, bounded
executor (`max_workers=4`), tool deadline 25s. `# THRESHOLD TBD (Open question 1)`: NFR-002
is pending PO confirmation; until then these numbers follow ADR-0006 A2 / ADR-0009 A3.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest
from confluent_kafka import KafkaError
from kafka_helpers import FakeAdmin, FakeCluster, FakeConsumer, kafka_error
from mcp.shared.memory import create_connected_server_and_client_session
from mcp_common.config import CommonSettings
from mcp_common.contract_testing import load_contract, validate_structured_content
from mcp_common.runtime import BoundedExecutor
from mcp_kafka.client import (
    TIMEOUT_MS,
    TIMEOUT_S,
    ConfluentKafkaReader,
    KafkaClient,
    build_admin_config,
)
from mcp_kafka.read_api import KafkaReadApi
from mcp_kafka.server import build_server

CONTRACT = load_contract()


def _server(settings, reader, executor: BoundedExecutor | None = None):
    common = CommonSettings()
    client = KafkaClient(settings, common=common, reader=reader, executor=executor)
    return build_server(KafkaReadApi(client, common), common=common)


async def _call(server, name: str, args: dict[str, Any]):
    async with create_connected_server_and_client_session(server) as session:
        return await session.call_tool(name, arguments=args)


def test_NFR_002_kafka_timeouts_are_8s_below_the_25s_deadline(settings) -> None:
    conf = build_admin_config(settings)
    assert conf["socket.timeout.ms"] == TIMEOUT_MS == 8000 and TIMEOUT_S == 8
    assert 2 * TIMEOUT_S + CommonSettings().http_backoff_base < CommonSettings().tool_deadline + 1


@pytest.mark.asyncio
async def test_NFR_002_every_adapter_call_uses_the_bounded_per_call_timeout(
    settings, cluster: FakeCluster
) -> None:
    reader = ConfluentKafkaReader(
        settings,
        admin_factory=lambda conf: FakeAdmin(conf, cluster),
        consumer_factory=lambda conf: FakeConsumer(conf, cluster),
    )
    await _call(_server(settings, reader), "kafka_describe_topic", {"topic": "payment.events"})
    timeouts = [kw["timeout"] for n, kw in cluster.calls if n == "list_topics"]
    assert timeouts == [8]


@pytest.mark.asyncio
async def test_NFR_002_dead_broker_is_upstream_unavailable_with_vpn_hint(
    settings, cluster: FakeCluster
) -> None:
    cluster.fail = kafka_error(KafkaError._TRANSPORT)
    reader = ConfluentKafkaReader(
        settings,
        admin_factory=lambda conf: FakeAdmin(conf, cluster),
        consumer_factory=lambda conf: FakeConsumer(conf, cluster),
    )
    result = await _call(_server(settings, reader), "kafka_list_topics", {})
    assert result.isError
    validate_structured_content(
        CONTRACT, "kafka_list_topics", result.structuredContent, is_error=True
    )
    error = result.structuredContent["error"]
    assert error["code"] == "upstream_unavailable" and error["retryable"] is True
    assert "VPN" in error["details"]["hint"] and "kafka.example.test" in error["details"]["hint"]


class _HungReader:
    """A synchronous adapter whose every call blocks until released (R16)."""

    def __init__(self) -> None:
        self.release = threading.Event()
        self.started = 0

    def list_topics(self):
        self.started += 1
        self.release.wait(15)
        return []

    def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_R16_n_hung_calls_then_call_n_plus_one_is_upstream_unavailable(
    settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MCP_TOOL_DEADLINE_KAFKA_LIST_TOPICS", "0.3")
    hung = _HungReader()
    server = _server(settings, hung, BoundedExecutor(4))
    try:
        for _ in range(4):  # each hits the 0.3s deadline; its thread stays blocked
            result = await _call(server, "kafka_list_topics", {})
            assert result.structuredContent["error"]["code"] == "upstream_timeout"
        assert hung.started == 4
        started = time.monotonic()
        fifth = await _call(server, "kafka_list_topics", {})
        assert time.monotonic() - started < 0.25  # immediate, not another deadline wait
        error = fifth.structuredContent["error"]
        assert error["code"] == "upstream_unavailable" and error["retryable"] is True
        assert "kafka.example.test:9092" in error["details"]["hint"]
        assert hung.started == 4  # the 5th call never reached the SDK
        validate_structured_content(
            CONTRACT, "kafka_list_topics", fifth.structuredContent, is_error=True
        )
    finally:
        hung.release.set()
        time.sleep(0.05)


@pytest.mark.asyncio
async def test_NFR_002_per_tool_deadline_override_is_honoured(
    settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MCP_TOOL_DEADLINE_KAFKA_DESCRIBE_TOPIC", "0.3")
    hung = _HungReader()
    started = time.monotonic()
    result = await _call(
        _server(settings, hung, BoundedExecutor(4)), "kafka_describe_topic", {"topic": "t"}
    )
    hung.release.set()
    assert time.monotonic() - started < 5
    error = result.structuredContent["error"]
    assert error["code"] == "upstream_timeout" and "0.3" in error["message"]
    assert error["details"]["tool"] == "kafka_describe_topic" and "VPN" in error["details"]["hint"]
