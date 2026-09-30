"""T-020/T-022: `tools.snapshot.json` + `api-contract.yaml` (ADR-0013), 4 result branches.

Every tool is exercised through the real MCP protocol (in-memory client session) and its
`structuredContent` is validated against the contract's response schema for the `ok`,
`empty`, `not_found` and `error` branches.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from mcp.shared.memory import create_connected_server_and_client_session
from mcp_common.config import CommonSettings
from mcp_common.contract_testing import (
    assert_snapshot_matches_contract,
    build_snapshot,
    load_contract,
    validate_structured_content,
)
from mcp_confluence.read_api import ConfluenceReadApi
from mcp_confluence.server import build_server

import mcp_confluence

BASE = "https://acme.atlassian.net/wiki"
SNAPSHOT_PATH = Path(mcp_confluence.__file__).parent / "tools.snapshot.json"
CONTRACT = load_contract()


def _load_snapshot() -> dict[str, Any]:
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


@pytest.mark.asyncio
async def test_snapshot_file_matches_live_tool_surface(read_api: ConfluenceReadApi) -> None:
    live = await build_snapshot(build_server(read_api))
    assert _load_snapshot() == json.loads(json.dumps(live)), (
        "tools.snapshot.json is stale; regenerate with `uv run mcp-confluence tools-dump`"
    )


def test_ADR_0013_snapshot_matches_contract_operations() -> None:
    assert_snapshot_matches_contract(_load_snapshot(), CONTRACT, tag="confluence")


def test_snapshot_has_exactly_four_tools() -> None:
    assert sorted(_load_snapshot()) == [
        "confluence_get_page",
        "confluence_list_page_children",
        "confluence_list_spaces",
        "confluence_search_pages",
    ]


@pytest.fixture
def server(read_api: ConfluenceReadApi):
    return build_server(read_api)


async def _call(server, name: str, args: dict[str, Any]):
    async with create_connected_server_and_client_session(server) as session:
        return await session.call_tool(name, arguments=args)


@pytest.mark.parametrize(
    ("tool", "args", "url", "fixture_name"),
    [
        (
            "confluence_search_pages",
            {"query": "payment retry", "limit": 2},
            f"{BASE}/rest/api/content/search",
            "search_results.json",
        ),
        (
            "confluence_get_page",
            {"page_id": "123456"},
            f"{BASE}/rest/api/content/123456",
            "page.json",
        ),
        (
            "confluence_list_spaces",
            {},
            f"{BASE}/rest/api/space",
            "spaces.json",
        ),
        (
            "confluence_list_page_children",
            {"page_id": "100"},
            f"{BASE}/rest/api/content/100/child/page",
            "children.json",
        ),
    ],
)
@pytest.mark.asyncio
async def test_ok_branch_validates_against_contract(
    server, readonly_respx_router: respx.MockRouter, fixture, tool, args, url, fixture_name
) -> None:
    readonly_respx_router.get(url).mock(
        return_value=httpx.Response(200, json=fixture(fixture_name))
    )
    result = await _call(server, tool, args)
    assert not result.isError
    validate_structured_content(CONTRACT, tool, result.structuredContent)
    assert result.structuredContent["status"] == "ok"
    text = result.content[0].text
    assert "Nguồn:" in text and "https://acme.atlassian.net/wiki" in text


@pytest.mark.parametrize(
    ("tool", "args", "url", "fixture_name"),
    [
        (
            "confluence_search_pages",
            {"query": "nothing"},
            f"{BASE}/rest/api/content/search",
            "search_empty.json",
        ),
        (
            "confluence_list_page_children",
            {"page_id": "101"},
            f"{BASE}/rest/api/content/101/child/page",
            "children_empty.json",
        ),
    ],
)
@pytest.mark.asyncio
async def test_FR_001_AC_002_empty_branch_validates_against_contract(
    server, readonly_respx_router: respx.MockRouter, fixture, tool, args, url, fixture_name
) -> None:
    readonly_respx_router.get(url).mock(
        return_value=httpx.Response(200, json=fixture(fixture_name))
    )
    result = await _call(server, tool, args)
    assert not result.isError
    validate_structured_content(CONTRACT, tool, result.structuredContent)
    payload = result.structuredContent
    assert payload["status"] == "empty"
    assert payload["items"] == [] and payload["citations"] == []
    assert payload["meta"]["query_echo"]
    assert "Nguồn:" not in result.content[0].text
    assert result.content[0].text.startswith("Không tìm thấy")


@pytest.mark.parametrize(
    ("tool", "url"),
    [
        ("confluence_get_page", f"{BASE}/rest/api/content/999"),
        ("confluence_list_page_children", f"{BASE}/rest/api/content/999/child/page"),
    ],
)
@pytest.mark.asyncio
async def test_not_found_branch_validates_against_contract(
    server, readonly_respx_router: respx.MockRouter, fixture, tool, url
) -> None:
    readonly_respx_router.get(url).mock(
        return_value=httpx.Response(404, json=fixture("error_404.json"))
    )
    result = await _call(server, tool, {"page_id": "999"})
    assert not result.isError
    validate_structured_content(CONTRACT, tool, result.structuredContent)
    assert result.structuredContent["status"] == "not_found"
    assert "999" in result.content[0].text and "không tồn tại" in result.content[0].text


@pytest.mark.asyncio
async def test_error_branch_validates_against_contract_timeout(
    server, readonly_respx_router: respx.MockRouter
) -> None:
    readonly_respx_router.get(f"{BASE}/rest/api/content/search").mock(
        side_effect=httpx.ConnectTimeout("connect timeout")
    )
    result = await _call(server, "confluence_search_pages", {"query": "x"})
    assert result.isError
    validate_structured_content(
        CONTRACT, "confluence_search_pages", result.structuredContent, is_error=True
    )
    error = result.structuredContent["error"]
    assert error["code"] == "upstream_timeout" and error["source"] == "confluence"
    assert "VPN" in error["details"]["hint"]


@pytest.mark.asyncio
async def test_error_branch_unauthorized_and_invalid_input(
    server, readonly_respx_router: respx.MockRouter
) -> None:
    readonly_respx_router.get(f"{BASE}/rest/api/space").mock(
        return_value=httpx.Response(401, json={"message": "Unauthorized"})
    )
    unauthorized = await _call(server, "confluence_list_spaces", {})
    assert unauthorized.structuredContent["error"]["code"] == "unauthorized"
    validate_structured_content(
        CONTRACT, "confluence_list_spaces", unauthorized.structuredContent, is_error=True
    )
    # a tz-less updated_after passes the SDK schema (format only) and is rejected by us
    invalid = await _call(
        server, "confluence_search_pages", {"query": "x", "updated_after": "2026-01-01T00:00:00"}
    )
    assert invalid.isError
    assert invalid.structuredContent["error"]["code"] == "invalid_input"
    assert invalid.structuredContent["error"]["details"]["field"] == "updated_after"
    validate_structured_content(
        CONTRACT, "confluence_search_pages", invalid.structuredContent, is_error=True
    )


@pytest.mark.asyncio
async def test_unconfigured_server_reports_source_misconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for var in ("BASE_URL", "EMAIL", "API_TOKEN"):
        monkeypatch.delenv(f"MCP_CONFLUENCE_{var}", raising=False)
    result = await _call(build_server(common=CommonSettings()), "confluence_list_spaces", {})
    assert result.isError
    error = result.structuredContent["error"]
    assert error["code"] == "source_misconfigured"
    assert "MCP_CONFLUENCE_BASE_URL" in error["details"]["missing_env"]
