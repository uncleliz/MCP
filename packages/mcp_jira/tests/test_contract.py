"""T-092 / T-094: `tools.snapshot.json` + `api-contract.yaml` (ADR-0013), 4 result branches.

Every tool is exercised through the real MCP protocol (in-memory client session) and its
`structuredContent` is validated against the contract's response schema for the `ok`, `empty`,
`not_found` and `error` branches (both flavors where it matters).
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
from mcp_jira.read_api import JiraReadApi
from mcp_jira.server import build_server

import mcp_jira

from .jira_helpers import (
    board_sprints_url,
    issue_url,
    project_search_url,
    project_url,
    search_url,
    sprint_url,
)

SNAPSHOT_PATH = Path(mcp_jira.__file__).parent / "tools.snapshot.json"
CONTRACT = load_contract()


def _load_snapshot() -> dict[str, Any]:
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


@pytest.mark.asyncio
async def test_snapshot_file_matches_live_tool_surface() -> None:
    live = await build_snapshot(build_server())
    assert _load_snapshot() == json.loads(json.dumps(live)), (
        "tools.snapshot.json is stale; regenerate with `uv run mcp-jira tools-dump`"
    )


def test_ADR_0013_snapshot_matches_contract_operations() -> None:
    assert_snapshot_matches_contract(_load_snapshot(), CONTRACT, tag="jira")


def test_snapshot_has_exactly_five_tools() -> None:
    assert sorted(_load_snapshot()) == [
        "jira_get_issue",
        "jira_get_sprint",
        "jira_list_board_sprints",
        "jira_list_projects",
        "jira_search_issues",
    ]


@pytest.fixture
def server(read_api: JiraReadApi):
    return build_server(read_api)


async def _call(server, name: str, args: dict[str, Any]):
    async with create_connected_server_and_client_session(server) as session:
        return await session.call_tool(name, arguments=args)


@pytest.mark.asyncio
async def test_ok_branches_validate_against_contract(
    server, readonly_respx_router: respx.MockRouter, fixture, base, flavor
) -> None:
    search_name = "search_cloud.json" if flavor == "cloud" else "search_server.json"
    readonly_respx_router.get(search_url(base, flavor)).mock(
        return_value=httpx.Response(200, json=fixture(search_name))
    )
    readonly_respx_router.get(issue_url(base, flavor, "PAY-1234")).mock(
        return_value=httpx.Response(200, json=fixture("issue.json"))
    )
    if flavor == "cloud":
        readonly_respx_router.get(project_search_url(base, flavor)).mock(
            return_value=httpx.Response(200, json=fixture("projects_cloud.json"))
        )
    else:
        readonly_respx_router.get(project_url(base, flavor)).mock(
            return_value=httpx.Response(200, json=fixture("projects_server.json"))
        )
    readonly_respx_router.get(sprint_url(base, 42)).mock(
        return_value=httpx.Response(200, json=fixture("sprint.json"))
    )
    readonly_respx_router.get(board_sprints_url(base, 7)).mock(
        return_value=httpx.Response(200, json=fixture("board_sprints.json"))
    )

    calls = [
        ("jira_search_issues", {"jql": "project = PAY", "limit": 1}),
        ("jira_get_issue", {"key": "PAY-1234"}),
        ("jira_list_projects", {}),
        ("jira_get_sprint", {"sprint_id": 42}),
        ("jira_list_board_sprints", {"board_id": 7, "state": "active"}),
    ]
    for name, args in calls:
        result = await _call(server, name, args)
        assert not result.isError, result.content
        validate_structured_content(CONTRACT, name, result.structuredContent)
        assert result.structuredContent["status"] == "ok"
        assert "Nguồn:" in result.content[0].text


@pytest.mark.asyncio
async def test_empty_branch_validates_against_contract(
    server, readonly_respx_router: respx.MockRouter, fixture, base, flavor
) -> None:
    readonly_respx_router.get(search_url(base, flavor)).mock(
        return_value=httpx.Response(200, json=fixture("search_empty.json"))
    )
    result = await _call(server, "jira_search_issues", {"jql": "project = NONE"})
    assert not result.isError
    validate_structured_content(CONTRACT, "jira_search_issues", result.structuredContent)
    payload = result.structuredContent
    assert payload["status"] == "empty"
    assert payload["items"] == [] and payload["citations"] == []
    assert "Nguồn:" not in result.content[0].text


@pytest.mark.asyncio
async def test_not_found_branch_validates_against_contract(
    server, readonly_respx_router: respx.MockRouter, base, flavor
) -> None:
    readonly_respx_router.get(issue_url(base, flavor, "ZZZ-9999")).mock(
        return_value=httpx.Response(404, json={"errorMessages": ["gone"]})
    )
    result = await _call(server, "jira_get_issue", {"key": "ZZZ-9999"})
    assert not result.isError
    validate_structured_content(CONTRACT, "jira_get_issue", result.structuredContent)
    assert result.structuredContent["status"] == "not_found"


@pytest.mark.asyncio
async def test_error_branch_validates_against_contract_timeout(
    server, readonly_respx_router: respx.MockRouter, base, flavor
) -> None:
    readonly_respx_router.get(search_url(base, flavor)).mock(
        side_effect=httpx.ConnectTimeout("connect timeout")
    )
    result = await _call(server, "jira_search_issues", {"jql": "x"})
    assert result.isError
    validate_structured_content(
        CONTRACT, "jira_search_issues", result.structuredContent, is_error=True
    )
    error = result.structuredContent["error"]
    assert error["code"] == "upstream_timeout" and error["source"] == "jira"


@pytest.mark.asyncio
async def test_error_branch_invalid_input(server) -> None:
    # A JQL over the 2048-char cap passes the SDK schema's maxLength? No — maxLength is enforced
    # by the SDK. Instead use a bad issue key that passes the SDK (no pattern violation at the
    # protocol boundary for get_issue because the pattern IS in the schema) — so assert the
    # board state enum rejection at the protocol layer here.
    bad = await _call(server, "jira_list_board_sprints", {"board_id": 1, "state": "nope"})
    assert bad.isError


@pytest.mark.asyncio
async def test_unconfigured_server_reports_source_misconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for var in ("BASE_URL", "EMAIL", "TOKEN", "FLAVOR"):
        monkeypatch.delenv(f"MCP_JIRA_{var}", raising=False)
    result = await _call(
        build_server(common=CommonSettings()), "jira_list_projects", {}
    )
    assert result.isError
    error = result.structuredContent["error"]
    assert error["code"] == "source_misconfigured"
    assert "MCP_JIRA_BASE_URL" in error["details"]["missing_env"]
