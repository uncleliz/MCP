"""T-022 / NFR-001: the read-only surface of mcp-confluence.

AC: FR-014/AC-001 (mutating operations are refused), FR-014/AC-002 (an unknown "write"
tool is rejected at the MCP protocol layer, not via ErrorEnvelope).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from mcp.shared.memory import create_connected_server_and_client_session
from mcp_common.contract_testing import load_contract, operations_by_id
from mcp_common.errors import NotPermittedError
from mcp_common.testing import (
    assert_readonly_tool_surface,
    assert_unknown_tool_rejected_at_protocol_layer,
    warn_if_tool_name_matches_deny_regex,
)
from mcp_common.tooling import registered_tool_functions
from mcp_confluence.client import ALLOWED_OPERATIONS, ConfluenceClient
from mcp_confluence.read_api import ConfluenceReadApi
from mcp_confluence.server import build_server

import mcp_confluence

BASE = "https://acme.atlassian.net/wiki"
PACKAGE_DIR = Path(mcp_confluence.__file__).parent
CONTRACT = load_contract()


@pytest.fixture
def server(read_api: ConfluenceReadApi):
    return build_server(read_api)


def test_FR_014_AC_001_tool_surface_is_readonly_and_in_contract(server) -> None:
    snapshot = json.loads((PACKAGE_DIR / "tools.snapshot.json").read_text(encoding="utf-8"))
    assert_readonly_tool_surface(
        registered_tools=registered_tool_functions(server),
        contract_operations=operations_by_id(CONTRACT),
        snapshot=snapshot,
        client_allowlist=ALLOWED_OPERATIONS,
        # mcp-ingest's Confluence connector calls client.py directly (ADR-0012 A4).
        additional_client_operations=ALLOWED_OPERATIONS,
    )


def test_exactly_the_four_contract_tools_are_registered(server) -> None:
    assert sorted(registered_tool_functions(server)) == [
        "confluence_get_page",
        "confluence_list_page_children",
        "confluence_list_spaces",
        "confluence_search_pages",
    ]


def test_tool_names_carry_no_write_verbs(server) -> None:
    # Heuristic only (ADR-0003 A2: a warning signal, never a hard gate).
    assert warn_if_tool_name_matches_deny_regex(registered_tool_functions(server)) == []


@pytest.mark.parametrize(
    "write_tool",
    [
        "confluence_create_page",
        "confluence_update_page",
        "confluence_delete_page",
        "confluence_add_comment",
        "confluence_set_label",
    ],
)
@pytest.mark.asyncio
async def test_FR_014_AC_002_unknown_write_tool_rejected_at_protocol_layer(
    server, write_tool: str
) -> None:
    await assert_unknown_tool_rejected_at_protocol_layer(server, write_tool, {"page_id": "1"})


@pytest.mark.asyncio
async def test_every_tool_call_only_issues_get_requests(
    server, readonly_respx_router: respx.MockRouter, fixture
) -> None:
    responses = {
        f"{BASE}/rest/api/content/search": "search_results.json",
        f"{BASE}/rest/api/content/123456": "page.json",
        f"{BASE}/rest/api/space": "spaces.json",
        f"{BASE}/rest/api/content/100/child/page": "children.json",
    }
    for url, name in responses.items():
        readonly_respx_router.get(url).mock(return_value=httpx.Response(200, json=fixture(name)))
    calls: list[tuple[str, dict[str, Any]]] = [
        ("confluence_search_pages", {"query": "q"}),
        ("confluence_get_page", {"page_id": "123456"}),
        ("confluence_list_spaces", {}),
        ("confluence_list_page_children", {"page_id": "100"}),
    ]
    async with create_connected_server_and_client_session(server) as session:
        for name, args in calls:
            result = await session.call_tool(name, arguments=args)
            assert not result.isError, result.content
    methods = {call.request.method for call in readonly_respx_router.calls}
    assert methods == {"GET"}
    for call in readonly_respx_router.calls:
        assert "export_view" not in str(call.request.url)
    # (readonly_respx_router re-asserts at teardown that nothing non-GET/HEAD went out)


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH"])
async def test_transport_blocks_every_write_method(
    client: ConfluenceClient, readonly_respx_router: respx.MockRouter, method: str
) -> None:
    with pytest.raises(NotPermittedError):
        await client.http.request(method, f"{BASE}/rest/api/content/1")
    assert readonly_respx_router.calls.call_count == 0


def test_source_never_calls_a_write_verb_on_http() -> None:
    offenders = []
    pattern = re.compile(r"\.(post|put|delete|patch)\(|\"(POST|PUT|DELETE|PATCH)\"")
    for path in PACKAGE_DIR.glob("*.py"):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{path.name}:{number}: {line.strip()}")
    assert offenders == []
