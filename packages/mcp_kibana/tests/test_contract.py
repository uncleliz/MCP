"""T-039/T-040: `tools.snapshot.json` + `api-contract.yaml` (ADR-0013), 4 result branches.

Every tool is called through the real MCP protocol (in-memory client session) and its
`structuredContent` is validated against the contract's response schema.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from kibana_helpers import (
    DASH_ID,
    FIND_EMPTY,
    NOT_FOUND_BODY,
    OK_CALLS,
    find_url,
    get_url,
    mock_ok,
    ok,
)
from mcp.shared.memory import create_connected_server_and_client_session
from mcp_common.config import CommonSettings
from mcp_common.contract_testing import (
    assert_snapshot_matches_contract,
    build_snapshot,
    load_contract,
    validate_structured_content,
)
from mcp_kibana.read_api import KibanaReadApi
from mcp_kibana.server import build_server

import mcp_kibana

SNAPSHOT_PATH = Path(mcp_kibana.__file__).parent / "tools.snapshot.json"
CONTRACT = load_contract()
EXPECTED_TOOLS = [
    "kibana_build_dashboard_link",
    "kibana_find_saved_objects",
    "kibana_get_saved_object",
]


def _snapshot() -> dict[str, Any]:
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def server(read_api: KibanaReadApi):
    return build_server(read_api)


async def _call(server, name: str, args: dict[str, Any]):
    async with create_connected_server_and_client_session(server) as session:
        return await session.call_tool(name, arguments=args)


@pytest.mark.asyncio
async def test_snapshot_file_matches_live_tool_surface(read_api: KibanaReadApi) -> None:
    live = await build_snapshot(build_server(read_api))
    assert _snapshot() == json.loads(json.dumps(live)), (
        "tools.snapshot.json is stale; regenerate with `uv run mcp-kibana tools-dump`"
    )


def test_ADR_0013_snapshot_matches_contract_operations() -> None:
    assert_snapshot_matches_contract(_snapshot(), CONTRACT, tag="kibana")


def test_snapshot_has_exactly_three_tools_within_r5_budget() -> None:
    assert sorted(_snapshot()) == EXPECTED_TOOLS and len(_snapshot()) <= 12


def test_tool_descriptions_are_at_most_three_sentences() -> None:
    for name, tool in _snapshot().items():
        sentences = [s for s in tool["description"].replace("...", "").split(". ") if s.strip()]
        assert len(sentences) <= 3, name


@pytest.mark.parametrize(("tool", "args"), OK_CALLS, ids=[c[0] for c in OK_CALLS])
@pytest.mark.asyncio
async def test_FR_005_AC_001_ok_branch_validates_against_contract(
    server, kibana: respx.MockRouter, tool: str, args: dict[str, Any]
) -> None:
    mock_ok(kibana)
    result = await _call(server, tool, args)
    assert not result.isError, result.content
    validate_structured_content(CONTRACT, tool, result.structuredContent)
    assert result.structuredContent["status"] == "ok"
    text = result.content[0].text
    assert "Nguồn:" in text and "https://kibana.example.test" in text
    assert "/api/" not in text  # citations are Kibana web links, never API URLs


@pytest.mark.asyncio
async def test_FR_005_AC_002_empty_branch_validates_against_contract(
    server, kibana: respx.MockRouter
) -> None:
    kibana.get(find_url()).mock(return_value=ok(FIND_EMPTY))
    result = await _call(server, "kibana_find_saved_objects", {"query": "zzz"})
    validate_structured_content(CONTRACT, "kibana_find_saved_objects", result.structuredContent)
    payload = result.structuredContent
    assert payload["status"] == "empty" and payload["items"] == [] and payload["citations"] == []
    assert payload["meta"]["query_echo"]["query"] == "zzz"
    assert result.content[0].text.startswith("Không tìm thấy")


@pytest.mark.parametrize(
    ("tool", "args", "url"),
    [
        ("kibana_get_saved_object", {"type": "dashboard", "id": "nope"},
         get_url("dashboard", "nope")),
        ("kibana_build_dashboard_link",
         {"dashboard_id": "nope", "time_from": "2026-09-30T10:00:00Z",
          "time_to": "2026-09-30T12:00:00Z"}, get_url("dashboard", "nope")),
    ],
)  # fmt: skip
@pytest.mark.asyncio
async def test_FR_005_AC_002_not_found_branch_validates_against_contract(
    server, kibana: respx.MockRouter, tool: str, args: dict[str, Any], url: str
) -> None:
    kibana.get(url).mock(return_value=httpx.Response(404, json=NOT_FOUND_BODY))
    result = await _call(server, tool, args)
    assert not result.isError
    validate_structured_content(CONTRACT, tool, result.structuredContent)
    assert result.structuredContent["status"] == "not_found"
    assert "không tồn tại" in result.content[0].text


@pytest.mark.asyncio
async def test_error_branch_validates_against_contract(server, kibana: respx.MockRouter) -> None:
    kibana.get(find_url()).mock(side_effect=httpx.ConnectTimeout("timeout"))
    timeout = await _call(server, "kibana_find_saved_objects", {"query": "p"})
    kibana.get(get_url("lens", DASH_ID)).mock(return_value=httpx.Response(401, json={}))
    unauthorized = await _call(server, "kibana_get_saved_object", {"type": "lens", "id": DASH_ID})
    invalid = await _call(
        server,
        "kibana_build_dashboard_link",
        {"dashboard_id": DASH_ID, "time_from": "2026-09-30T12:00:00Z",
         "time_to": "2026-09-30T10:00:00Z"},
    )  # fmt: skip
    for res, tool, code in [
        (timeout, "kibana_find_saved_objects", "upstream_timeout"),
        (unauthorized, "kibana_get_saved_object", "unauthorized"),
        (invalid, "kibana_build_dashboard_link", "invalid_input"),
    ]:
        assert res.isError
        validate_structured_content(CONTRACT, tool, res.structuredContent, is_error=True)
        assert res.structuredContent["error"]["code"] == code
        assert res.structuredContent["error"]["source"] == "kibana"
    assert "VPN" in timeout.structuredContent["error"]["details"]["hint"]
    assert invalid.structuredContent["error"]["details"]["field"] == "time_from"


@pytest.mark.asyncio
async def test_unconfigured_server_reports_source_misconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MCP_KIBANA_BASE_URL", raising=False)
    result = await _call(
        build_server(common=CommonSettings()), "kibana_find_saved_objects", {"query": "x"}
    )
    assert result.isError
    error = result.structuredContent["error"]
    assert error["code"] == "source_misconfigured"
    assert "MCP_KIBANA_BASE_URL" in error["details"]["missing_env"]
