"""T-053 / NFR-002: an unreachable or hung Redis fails fast and explains itself.

Budget (ADR-0008 A4 / ADR-0006 A2): connect 2s / read 5s, tool deadline 25s. NOTE: the
NFR-002 threshold is still pending PO confirmation (Open question); until then these
numbers follow ADR-0006/0008 A4 — `# THRESHOLD TBD (Open question 1)`.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest
import redis.exceptions as rex
from mcp.shared.memory import create_connected_server_and_client_session
from mcp_common.config import CommonSettings
from mcp_common.contract_testing import load_contract, validate_structured_content
from mcp_redis.client import CONNECT_TIMEOUT_S, READ_TIMEOUT_S, RedisClient
from mcp_redis.read_api import RedisReadApi
from mcp_redis.server import build_server
from redis_helpers import FakeRedis

CONTRACT = load_contract()


def _server(settings, fake: FakeRedis):
    common = CommonSettings()  # production numbers
    client = RedisClient(settings, common=common, redis_factory=lambda db: fake)
    return build_server(RedisReadApi(client, common), common=common)


async def _call(server, name: str, args: dict[str, Any]):
    async with create_connected_server_and_client_session(server) as session:
        return await session.call_tool(name, arguments=args)


def test_NFR_002_redis_timeouts_leave_room_under_the_tool_deadline() -> None:
    common = CommonSettings()
    # Redis is a single round trip family: connect + read is far below the 25s deadline,
    # and even a pipeline of reads stays under it for the documented 2s/5s numbers.
    assert CONNECT_TIMEOUT_S == 2.0 and READ_TIMEOUT_S == 5.0
    assert CONNECT_TIMEOUT_S + READ_TIMEOUT_S < common.tool_deadline
    assert 2 * (CONNECT_TIMEOUT_S + READ_TIMEOUT_S) + common.http_backoff_base < 25.0


@pytest.mark.asyncio
async def test_NFR_002_dead_redis_is_upstream_unavailable_with_vpn_hint(
    settings, fake_data
) -> None:
    fake = FakeRedis(fake_data)
    fake.fail = rex.ConnectionError("Error 111 connecting to redis.example.test:6379")
    result = await _call(_server(settings, fake), "redis_scan_keys", {})
    assert result.isError
    validate_structured_content(
        CONTRACT, "redis_scan_keys", result.structuredContent, is_error=True
    )
    error = result.structuredContent["error"]
    assert error["code"] == "upstream_unavailable"
    assert "VPN" in error["details"]["hint"] and "redis.example.test" in error["details"]["hint"]


@pytest.mark.asyncio
async def test_NFR_002_read_timeout_maps_to_upstream_timeout(settings, fake_data) -> None:
    fake = FakeRedis(fake_data)
    fake.fail = rex.TimeoutError("Timeout reading from socket")
    error = (await _call(_server(settings, fake), "redis_get_key", {"key": "k"})).structuredContent[
        "error"
    ]
    assert error["code"] == "upstream_timeout" and error["retryable"] is True


@pytest.mark.asyncio
async def test_NFR_002_hung_redis_hits_the_tool_deadline_with_hint(
    settings, fake_data, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MCP_TOOL_DEADLINE_REDIS_KEY_INFO", "0.3")
    fake = FakeRedis(fake_data)

    async def hang() -> None:
        await asyncio.sleep(30)

    fake.delay = hang
    started = time.monotonic()
    result = await _call(_server(settings, fake), "redis_key_info", {"key": "greeting"})
    assert time.monotonic() - started < 5
    error = result.structuredContent["error"]
    assert error["code"] == "upstream_timeout" and "0.3" in error["message"]
    assert error["details"]["tool"] == "redis_key_info" and "VPN" in error["details"]["hint"]
