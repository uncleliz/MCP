"""T-030 / NFR-002: an unreachable GitLab fails fast and explains itself.

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
from gitlab_helpers import API
from mcp.shared.memory import create_connected_server_and_client_session
from mcp_common.config import CommonSettings
from mcp_common.contract_testing import load_contract, validate_structured_content
from mcp_common.testing import VirtualClock, virtual_http_clock
from mcp_gitlab.client import GitLabClient
from mcp_gitlab.read_api import GitLabReadApi
from mcp_gitlab.server import build_server
from mcp_gitlab.settings import Settings

__all__ = ["virtual_http_clock"]

CONTRACT = load_contract()


def _production_server(settings: Settings):
    common = CommonSettings()  # production numbers: 3s / 7s / 1 retry / 1s backoff / 25s
    api = GitLabReadApi(GitLabClient(settings, common=common), common)
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

    route = readonly_respx_router.get(f"{API}/projects").mock(side_effect=dead)
    result = await _call(_production_server(settings), "gitlab_search_projects", {"query": "pay"})

    assert result.isError
    validate_structured_content(
        CONTRACT, "gitlab_search_projects", result.structuredContent, is_error=True
    )
    error = result.structuredContent["error"]
    assert error["code"] == "upstream_timeout"  # (a)
    assert virtual_http_clock.now == pytest.approx(21.0)  # (b) 2*(3+7)+1
    assert virtual_http_clock.now < 25.0
    assert route.call_count == 2  # (c)
    assert (
        "VPN" in error["details"]["hint"] and "gitlab.example.test" in error["details"]["hint"]
    )  # (d)


@pytest.mark.asyncio
async def test_NFR_002_connection_refused_is_upstream_unavailable_with_two_attempts(
    settings: Settings, readonly_respx_router: respx.MockRouter, virtual_http_clock: VirtualClock
) -> None:
    route = readonly_respx_router.get(f"{API}/search").mock(
        side_effect=httpx.ConnectError("connection refused")
    )
    result = await _call(_production_server(settings), "gitlab_search_code", {"query": "retry"})
    error = result.structuredContent["error"]
    assert error["code"] == "upstream_unavailable" and route.call_count == 2
    assert "VPN" in error["details"]["hint"] and virtual_http_clock.now < 25.0


@pytest.mark.asyncio
async def test_NFR_002_per_tool_deadline_override_is_honoured(
    settings: Settings, readonly_respx_router: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MCP_TOOL_DEADLINE_GITLAB_LIST_PIPELINES", "0.3")

    async def hangs(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(30)
        raise AssertionError("deadline should have cut this call")

    readonly_respx_router.get(f"{API}/projects/42").mock(side_effect=hangs)
    started = time.monotonic()
    result = await _call(_production_server(settings), "gitlab_list_pipelines", {"project": "42"})
    assert time.monotonic() - started < 5
    error = result.structuredContent["error"]
    assert error["code"] == "upstream_timeout" and "0.3" in error["message"]
    assert error["details"]["tool"] == "gitlab_list_pipelines" and "VPN" in error["details"]["hint"]
