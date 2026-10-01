"""T-045/T-053 / NFR-002 + R16: dead AWS fails fast; exhausted executor reports itself.

Budget (ADR-0008 A4): boto3 `connect 3 / read 7 / 2 attempts` on a bounded executor
(`max_workers=4`), tool deadline 25s. `# THRESHOLD TBD (Open question 1)`: NFR-002 is pending
PO confirmation; until then these numbers follow ADR-0006 A2 / ADR-0008 A4.
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

import pytest
from cw_helpers import OK_CALLS
from mcp.shared.memory import create_connected_server_and_client_session
from mcp_cloudwatch.client import (
    CONNECT_TIMEOUT_S,
    MAX_ATTEMPTS,
    READ_TIMEOUT_S,
    CloudWatchClient,
)
from mcp_cloudwatch.read_api import CloudWatchReadApi
from mcp_cloudwatch.server import build_server
from mcp_common.config import CommonSettings
from mcp_common.contract_testing import load_contract, validate_structured_content
from mcp_common.runtime import BoundedExecutor

CONTRACT = load_contract()


def test_NFR_002_boto_budget_is_21s_class_and_below_the_25s_deadline() -> None:
    common = CommonSettings()
    assert (CONNECT_TIMEOUT_S, READ_TIMEOUT_S, MAX_ATTEMPTS) == (3, 7, 2)
    worst_case = MAX_ATTEMPTS * (CONNECT_TIMEOUT_S + READ_TIMEOUT_S) + common.http_backoff_base
    assert worst_case == 21.0 < common.tool_deadline


class _Events:
    def register_first(self, *_a, **_k) -> None:
        return None


class _Meta:
    events = _Events()


class _HungLogs:
    """Synchronous SDK stand-in whose every call blocks until released (R16)."""

    meta = _Meta()

    def __init__(self) -> None:
        self.release = threading.Event()
        self.started = 0

    def describe_log_groups(self, **_kwargs):
        self.started += 1
        self.release.wait(15)
        return {"logGroups": []}


async def _call(server, name: str, args: dict[str, Any]):
    async with create_connected_server_and_client_session(server) as session:
        return await session.call_tool(name, arguments=args)


@pytest.mark.asyncio
async def test_R16_n_hung_calls_then_call_n_plus_one_is_upstream_unavailable(
    settings, common: CommonSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Calls that hang past the per-tool deadline leave their threads running; the 5th call
    must come back as `upstream_unavailable` immediately instead of silently queueing."""
    monkeypatch.setenv("MCP_TOOL_DEADLINE_CLOUDWATCH_LIST_LOG_GROUPS", "0.3")
    hung = _HungLogs()
    client = CloudWatchClient(
        settings, common=common, client_factory=lambda s: hung, executor=BoundedExecutor(4)
    )
    server = build_server(CloudWatchReadApi(client, common, settings), common=common)
    try:
        first_four = [await _call(server, "cloudwatch_list_log_groups", {}) for _ in range(4)]
        for result in first_four:  # each one hit the 0.3s deadline; its thread is still hung
            assert result.structuredContent["error"]["code"] == "upstream_timeout"
        assert hung.started == 4
        started = time.monotonic()
        fifth = await _call(server, "cloudwatch_list_log_groups", {})
        assert time.monotonic() - started < 0.25  # immediate, not another 0.3s deadline wait
        error = fifth.structuredContent["error"]
        assert error["code"] == "upstream_unavailable" and error["retryable"] is True
        assert "đang có call" in error["details"]["hint"]
        assert hung.started == 4  # the 5th call never reached the SDK
        validate_structured_content(
            CONTRACT, "cloudwatch_list_log_groups", fifth.structuredContent, is_error=True
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

        def describe_log_groups(self, **_kwargs):
            raise EndpointConnectionError(endpoint_url="https://logs.ap-southeast-1.amazonaws.com")

    client = CloudWatchClient(settings, common=common, client_factory=lambda s: Dead())
    server = build_server(CloudWatchReadApi(client, common, settings), common=common)
    result = await _call(server, "cloudwatch_list_log_groups", {})
    error = result.structuredContent["error"]
    assert error["code"] == "upstream_unavailable"
    assert "VPN" in error["details"]["hint"] and "amazonaws.com" in error["details"]["hint"]


class _QuickLogs:
    meta = _Meta()

    def start_query(self, **_kwargs):
        return {"queryId": "q1"}

    def get_query_results(self, **_kwargs):
        return {"status": "Complete", "results": []}


@pytest.mark.asyncio
async def test_NFR_002_slow_insights_is_validated_against_its_own_deadline_override(
    settings, common: CommonSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = CloudWatchClient(settings, common=common, client_factory=lambda s: _QuickLogs())
    server = build_server(CloudWatchReadApi(client, common, settings), common=common)
    args = {**OK_CALLS[2][1], "timeout_s": 22}
    monkeypatch.setenv("MCP_TOOL_DEADLINE_CLOUDWATCH_RUN_LOGS_INSIGHTS", "15")
    refused = await _call(server, "cloudwatch_run_logs_insights", args)
    error = refused.structuredContent["error"]
    assert error["code"] == "invalid_input" and error["details"]["field"] == "timeout_s"
    monkeypatch.setenv("MCP_TOOL_DEADLINE_CLOUDWATCH_RUN_LOGS_INSIGHTS", "60")
    accepted = await _call(server, "cloudwatch_run_logs_insights", args)
    assert not accepted.isError and accepted.structuredContent["status"] == "empty"
