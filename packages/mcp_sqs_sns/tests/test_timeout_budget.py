"""T-059/T-061 / NFR-002 + R16: dead AWS fails fast; exhausted executor reports itself."""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from mcp_common.config import CommonSettings
from mcp_common.contract_testing import load_contract, validate_structured_content
from mcp_common.runtime import BoundedExecutor
from mcp_sqs_sns.client import CONNECT_TIMEOUT_S, MAX_ATTEMPTS, READ_TIMEOUT_S, SqsSnsClient
from mcp_sqs_sns.read_api import SqsSnsReadApi
from mcp_sqs_sns.server import build_server

CONTRACT = load_contract()


def test_NFR_002_boto_budget_is_20s_class_and_below_the_25s_deadline() -> None:
    common = CommonSettings()
    assert (CONNECT_TIMEOUT_S, READ_TIMEOUT_S, MAX_ATTEMPTS) == (3, 7, 2)
    assert MAX_ATTEMPTS * (CONNECT_TIMEOUT_S + READ_TIMEOUT_S) == 20 < common.tool_deadline


class _Events:
    def register_first(self, *_a, **_k) -> None:
        return None


class _Meta:
    events = _Events()


class _HungSqs:
    meta = _Meta()

    def __init__(self) -> None:
        self.release = threading.Event()
        self.started = 0

    def list_queues(self, **_kwargs):
        self.started += 1
        self.release.wait(15)
        return {}


async def _call(server, name: str, args: dict[str, Any]):
    async with create_connected_server_and_client_session(server) as session:
        return await session.call_tool(name, arguments=args)


@pytest.mark.asyncio
async def test_R16_n_hung_calls_then_call_n_plus_one_is_upstream_unavailable(
    settings, common: CommonSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MCP_TOOL_DEADLINE_SQS_LIST_QUEUES", "0.3")
    hung = _HungSqs()
    client = SqsSnsClient(
        settings, common=common, client_factory=lambda s: hung, executor=BoundedExecutor(4)
    )
    server = build_server(SqsSnsReadApi(client, common, settings), common=common)
    try:
        for _ in range(4):
            result = await _call(server, "sqs_list_queues", {})
            assert result.structuredContent["error"]["code"] == "upstream_timeout"
        assert hung.started == 4
        started = time.monotonic()
        fifth = await _call(server, "sqs_list_queues", {})
        assert time.monotonic() - started < 0.25
        error = fifth.structuredContent["error"]
        assert error["code"] == "upstream_unavailable" and error["retryable"] is True
        assert hung.started == 4
        validate_structured_content(
            CONTRACT, "sqs_list_queues", fifth.structuredContent, is_error=True
        )
    finally:
        hung.release.set()
        await asyncio.sleep(0.05)
        client._executor.shutdown(wait=True)


@pytest.mark.asyncio
async def test_NFR_002_aws_unreachable_is_upstream_unavailable_with_hint(
    settings, common: CommonSettings
) -> None:
    from botocore.exceptions import EndpointConnectionError

    class Dead:
        meta = _Meta()

        def list_topics(self, **_kwargs):
            raise EndpointConnectionError(endpoint_url="https://sns.ap-southeast-1.amazonaws.com")

    client = SqsSnsClient(settings, common=common, client_factory=lambda s: Dead())
    server = build_server(SqsSnsReadApi(client, common, settings), common=common)
    result = await _call(server, "sns_list_topics", {})
    error = result.structuredContent["error"]
    assert error["code"] == "upstream_unavailable" and error["source"] == "sns"
    assert "VPN" in error["details"]["hint"]
