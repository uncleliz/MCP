"""T-053 / NFR-002: an unreachable Kibana fails fast and explains itself.

Thresholds are the ADR-0006 A2 set (connect 3s / read 7s / 2 attempts / 1s backoff = 21s,
deadline 25s). `# THRESHOLD TBD (Open question 1)`: NFR-002 is pending PO confirmation.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
import pytest
import respx
from kibana_helpers import API, find_url
from mcp.shared.memory import create_connected_server_and_client_session
from mcp_common.config import CommonSettings
from mcp_common.contract_testing import load_contract, validate_structured_content
from mcp_common.testing import VirtualClock, virtual_http_clock
from mcp_kibana.client import KibanaClient
from mcp_kibana.read_api import KibanaReadApi
from mcp_kibana.server import build_server
from mcp_kibana.settings import Settings

__all__ = ["virtual_http_clock"]

CONTRACT = load_contract()


def _production_server(settings: Settings):
    common = CommonSettings()  # production numbers: 3s / 7s / 1 retry / 1s backoff / 25s
    api = KibanaReadApi(KibanaClient(settings, common=common), common)
    return build_server(api, common=common)


async def _call(server, name: str, args: dict[str, Any]):
    async with create_connected_server_and_client_session(server) as session:
        return await session.call_tool(name, arguments=args)


@pytest.mark.asyncio
async def test_NFR_002_dead_endpoint_21s_budget_two_attempts_and_vpn_hint(
    settings: Settings, readonly_respx_router: respx.MockRouter, virtual_http_clock: VirtualClock
) -> None:
    async def dead(request: httpx.Request) -> httpx.Response:
        virtual_http_clock.advance(3 + 7)
        raise httpx.ReadTimeout("dead endpoint", request=request)

    route = readonly_respx_router.get(find_url()).mock(side_effect=dead)
    result = await _call(_production_server(settings), "kibana_find_saved_objects", {"query": "p"})
    assert result.isError
    validate_structured_content(
        CONTRACT, "kibana_find_saved_objects", result.structuredContent, is_error=True
    )
    error = result.structuredContent["error"]
    assert error["code"] == "upstream_timeout"
    assert virtual_http_clock.now == pytest.approx(21.0) and virtual_http_clock.now < 25.0
    assert route.call_count == 2
    assert "VPN" in error["details"]["hint"] and "kibana.example.test" in error["details"]["hint"]


@pytest.mark.asyncio
async def test_NFR_002_connection_refused_is_upstream_unavailable_with_two_attempts(
    settings: Settings, readonly_respx_router: respx.MockRouter, virtual_http_clock: VirtualClock
) -> None:
    route = readonly_respx_router.get(f"{API}/saved_objects/dashboard/x").mock(
        side_effect=httpx.ConnectError("connection refused")
    )
    result = await _call(
        _production_server(settings), "kibana_get_saved_object", {"type": "dashboard", "id": "x"}
    )
    error = result.structuredContent["error"]
    assert error["code"] == "upstream_unavailable" and route.call_count == 2
    assert "VPN" in error["details"]["hint"] and virtual_http_clock.now < 25.0


@pytest.mark.asyncio
async def test_NFR_002_per_tool_deadline_override_is_honoured(
    settings: Settings, readonly_respx_router: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MCP_TOOL_DEADLINE_KIBANA_FIND_SAVED_OBJECTS", "0.3")

    async def hangs(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(30)
        raise AssertionError("deadline should have cut this call")

    readonly_respx_router.get(find_url()).mock(side_effect=hangs)
    started = time.monotonic()
    result = await _call(_production_server(settings), "kibana_find_saved_objects", {"query": "p"})
    assert time.monotonic() - started < 5
    error = result.structuredContent["error"]
    assert error["code"] == "upstream_timeout" and "0.3" in error["message"]
    assert (
        error["details"]["tool"] == "kibana_find_saved_objects"
        and "VPN" in error["details"]["hint"]
    )
