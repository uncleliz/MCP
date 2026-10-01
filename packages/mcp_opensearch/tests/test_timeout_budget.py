"""T-053 / NFR-002: an unreachable or hung OpenSearch fails fast and explains itself.

Budget (ADR-0006 A2): cheap calls 7s total per request, `search`/`count` `timeout_s + 1`
(default 21s), one retry on connection errors only, tool deadline 25s.
`# THRESHOLD TBD (Open question 1)`: NFR-002 is pending PO confirmation.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from mcp_common.config import CommonSettings
from mcp_common.contract_testing import load_contract, validate_structured_content
from mcp_opensearch.client import CHEAP_REQUEST_TIMEOUT_S, OpenSearchClient
from mcp_opensearch.read_api import OpenSearchReadApi
from mcp_opensearch.server import build_server
from opensearchpy import exceptions as osx
from os_helpers import ARGS_OK_SEARCH, FakeOpenSearch

CONTRACT = load_contract()


def _server(settings, fake: FakeOpenSearch):
    common = CommonSettings()  # production numbers
    client = OpenSearchClient(settings, common=common, os_client=fake)
    return build_server(OpenSearchReadApi(client, common), common=common, allow_dsl=True)


async def _call(server, name: str, args: dict[str, Any]):
    async with create_connected_server_and_client_session(server) as session:
        return await session.call_tool(name, arguments=args)


def test_NFR_002_default_search_timeout_is_21s_below_the_25s_deadline() -> None:
    common = CommonSettings()
    default_timeout_s = 20  # contract TimeoutField default
    assert default_timeout_s + 1 == 21 < common.tool_deadline
    assert CHEAP_REQUEST_TIMEOUT_S == 7.0 and CHEAP_REQUEST_TIMEOUT_S < common.tool_deadline


@pytest.mark.asyncio
async def test_NFR_002_search_uses_timeout_s_plus_one_as_request_timeout(settings) -> None:
    fake = FakeOpenSearch({"search": {"hits": {"hits": []}}})
    await _call(_server(settings, fake), "opensearch_search_logs", dict(ARGS_OK_SEARCH))
    assert fake.last("search")["params"]["request_timeout"] == 21
    assert fake.last("search")["body"]["timeout"] == "20s"


@pytest.mark.asyncio
async def test_NFR_002_dead_endpoint_is_upstream_timeout_with_vpn_hint(settings) -> None:
    fake = FakeOpenSearch({"search": osx.ConnectionTimeout("TIMEOUT", "t", Exception("x"))})
    result = await _call(_server(settings, fake), "opensearch_search_logs", dict(ARGS_OK_SEARCH))
    assert result.isError
    validate_structured_content(
        CONTRACT, "opensearch_search_logs", result.structuredContent, is_error=True
    )
    error = result.structuredContent["error"]
    assert error["code"] == "upstream_timeout" and error["retryable"] is True
    hint = error["details"]["hint"]
    assert "VPN" in hint and "opensearch.example.test" in hint


@pytest.mark.asyncio
async def test_NFR_002_connection_refused_is_upstream_unavailable(settings) -> None:
    fake = FakeOpenSearch({"cat.indices": osx.ConnectionError("N/A", "refused", Exception("x"))})
    result = await _call(_server(settings, fake), "opensearch_list_indices", {})
    error = result.structuredContent["error"]
    assert error["code"] == "upstream_unavailable" and "VPN" in error["details"]["hint"]


@pytest.mark.asyncio
async def test_NFR_002_timeout_s_is_validated_against_the_per_tool_deadline(
    settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MCP_TOOL_DEADLINE_OPENSEARCH_SEARCH_DSL", "10")
    fake = FakeOpenSearch({"search": {"hits": {"hits": []}}})
    server = _server(settings, fake)
    args = {"index_pattern": "a", "body": {"query": {"match_all": {}}}}
    result = await _call(server, "opensearch_search_dsl", {**args, "timeout_s": 20})
    error = result.structuredContent["error"]
    assert error["code"] == "invalid_input" and error["details"]["field"] == "timeout_s"
    assert fake.calls == []
    monkeypatch.setenv("MCP_TOOL_DEADLINE_OPENSEARCH_SEARCH_DSL", "45")  # slow valid query
    ok = await _call(server, "opensearch_search_dsl", {**args, "timeout_s": 22})
    assert not ok.isError and fake.last("search")["params"]["request_timeout"] == 23


@pytest.mark.asyncio
async def test_NFR_002_hung_cluster_hits_the_per_tool_deadline_with_hint(
    settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MCP_TOOL_DEADLINE_OPENSEARCH_LIST_INDICES", "0.3")

    async def hangs(_kwargs: dict) -> list:
        await asyncio.sleep(30)
        raise AssertionError("deadline should have cut this call")

    fake = FakeOpenSearch({"cat.indices": hangs})
    started = time.monotonic()
    result = await _call(_server(settings, fake), "opensearch_list_indices", {})
    assert time.monotonic() - started < 5
    error = result.structuredContent["error"]
    assert error["code"] == "upstream_timeout" and "0.3" in error["message"]
    assert (
        error["details"]["tool"] == "opensearch_list_indices" and "VPN" in error["details"]["hint"]
    )
