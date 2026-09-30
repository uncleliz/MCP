"""T-030 / NFR-002: an unreachable Confluence fails fast and explains itself.

Thresholds are the ADR-0006 A2 set (connect 3s / read 7s / 2 attempts / 1s backoff = 21s,
deadline 25s). NOTE: the NFR-002 threshold is still pending PO confirmation; until then
these numbers follow ADR-0006 A2 (see implementation-plan T-030).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
import pytest
import respx
from mcp.shared.memory import create_connected_server_and_client_session
from mcp_common.config import CommonSettings
from mcp_common.contract_testing import load_contract, validate_structured_content
from mcp_common.testing import VirtualClock, virtual_http_clock
from mcp_confluence.client import ConfluenceClient
from mcp_confluence.read_api import ConfluenceReadApi
from mcp_confluence.server import build_server
from mcp_confluence.settings import Settings

__all__ = ["virtual_http_clock"]

BASE = "https://acme.atlassian.net/wiki"
CONTRACT = load_contract()


def _production_server(settings: Settings):
    common = CommonSettings()  # production numbers: 3s / 7s / 1 retry / 1s backoff / 25s
    api = ConfluenceReadApi(ConfluenceClient(settings, common=common), settings, common)
    return build_server(api, common=common)


async def _call(server, name: str, args: dict[str, Any]):
    async with create_connected_server_and_client_session(server) as session:
        return await session.call_tool(name, arguments=args)


@pytest.mark.asyncio
async def test_NFR_002_dead_endpoint_21s_budget_two_attempts_and_vpn_hint(
    settings: Settings, readonly_respx_router: respx.MockRouter, virtual_http_clock: VirtualClock
) -> None:
    async def dead(request: httpx.Request) -> httpx.Response:
        virtual_http_clock.advance(3 + 7)  # one attempt burns connect + read timeout
        raise httpx.ReadTimeout("dead endpoint", request=request)

    route = readonly_respx_router.get(f"{BASE}/rest/api/content/search").mock(side_effect=dead)
    result = await _call(
        _production_server(settings), "confluence_search_pages", {"query": "retry"}
    )

    assert result.isError
    validate_structured_content(
        CONTRACT, "confluence_search_pages", result.structuredContent, is_error=True
    )
    error = result.structuredContent["error"]
    assert error["code"] == "upstream_timeout"  # (a)
    assert virtual_http_clock.now == pytest.approx(21.0)  # (b) 2*(3+7)+1
    assert virtual_http_clock.now < 25.0
    assert route.call_count == 2  # (c) exactly 2 attempts
    assert (
        "VPN" in error["details"]["hint"] and "acme.atlassian.net" in error["details"]["hint"]
    )  # (d)
    assert "VPN" in result.content[0].text


@pytest.mark.asyncio
async def test_NFR_002_connection_refused_is_upstream_unavailable_with_two_attempts(
    settings: Settings, readonly_respx_router: respx.MockRouter, virtual_http_clock: VirtualClock
) -> None:
    route = readonly_respx_router.get(f"{BASE}/rest/api/space").mock(
        side_effect=httpx.ConnectError("connection refused")
    )
    result = await _call(_production_server(settings), "confluence_list_spaces", {})
    error = result.structuredContent["error"]
    assert error["code"] == "upstream_unavailable" and route.call_count == 2
    assert "VPN" in error["details"]["hint"]
    assert virtual_http_clock.now < 25.0


@pytest.mark.asyncio
async def test_NFR_002_per_tool_deadline_override_is_honoured(
    settings: Settings, readonly_respx_router: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MCP_TOOL_DEADLINE_CONFLUENCE_GET_PAGE", "0.3")

    async def hangs(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(30)
        raise AssertionError("deadline should have cut this call")

    readonly_respx_router.get(f"{BASE}/rest/api/content/123").mock(side_effect=hangs)
    started = time.monotonic()
    result = await _call(_production_server(settings), "confluence_get_page", {"page_id": "123"})
    assert time.monotonic() - started < 5  # the 0.3s override, not the 25s default
    error = result.structuredContent["error"]
    assert error["code"] == "upstream_timeout"
    assert "0.3" in error["message"] and error["details"]["tool"] == "confluence_get_page"
    assert "VPN" in error["details"]["hint"]
