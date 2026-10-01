"""T-027/T-028: `tools.snapshot.json` + `api-contract.yaml` (ADR-0013), 4 result branches.

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
from gitlab_helpers import API, OK_CALLS, PROJ, mock_ok
from mcp.shared.memory import create_connected_server_and_client_session
from mcp_common.config import CommonSettings
from mcp_common.contract_testing import (
    assert_snapshot_matches_contract,
    build_snapshot,
    load_contract,
    validate_structured_content,
)
from mcp_gitlab.read_api import GitLabReadApi
from mcp_gitlab.server import build_server

import mcp_gitlab

SNAPSHOT_PATH = Path(mcp_gitlab.__file__).parent / "tools.snapshot.json"
CONTRACT = load_contract()
P = "team/payment-service"
PR = f"{API}/projects/{PROJ}"

EXPECTED_TOOLS = [
    "gitlab_get_file",
    "gitlab_get_issue",
    "gitlab_get_merge_request",
    "gitlab_get_pipeline",
    "gitlab_list_commits",
    "gitlab_list_issues",
    "gitlab_list_merge_requests",
    "gitlab_list_pipelines",
    "gitlab_list_repository_tree",
    "gitlab_search_code",
    "gitlab_search_projects",
]


def _snapshot() -> dict[str, Any]:
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


@pytest.mark.asyncio
async def test_snapshot_file_matches_live_tool_surface(read_api: GitLabReadApi) -> None:
    live = await build_snapshot(build_server(read_api))
    assert _snapshot() == json.loads(json.dumps(live)), (
        "tools.snapshot.json is stale; regenerate with `uv run mcp-gitlab tools-dump`"
    )


def test_ADR_0013_snapshot_matches_contract_operations() -> None:
    assert_snapshot_matches_contract(_snapshot(), CONTRACT, tag="gitlab")


def test_snapshot_has_exactly_eleven_tools_within_r5_budget() -> None:
    assert sorted(_snapshot()) == EXPECTED_TOOLS
    assert len(_snapshot()) <= 12  # R5: no server above 12 tools


def test_tool_descriptions_are_at_most_three_sentences() -> None:
    for name, tool in _snapshot().items():
        sentences = [s for s in tool["description"].replace("...", "").split(". ") if s.strip()]
        assert len(sentences) <= 3, name


@pytest.fixture
def server(read_api: GitLabReadApi):
    return build_server(read_api)


async def _call(server, name: str, args: dict[str, Any]):
    async with create_connected_server_and_client_session(server) as session:
        return await session.call_tool(name, arguments=args)


@pytest.mark.parametrize(("tool", "args"), OK_CALLS, ids=[c[0] for c in OK_CALLS])
@pytest.mark.asyncio
async def test_ok_branch_validates_against_contract(
    server, gitlab: respx.MockRouter, tool: str, args: dict[str, Any]
) -> None:
    mock_ok(gitlab)
    result = await _call(server, tool, args)
    assert not result.isError, result.content
    validate_structured_content(CONTRACT, tool, result.structuredContent)
    assert result.structuredContent["status"] in {"ok", "partial"}
    text = result.content[0].text
    assert "Nguồn:" in text and "https://gitlab.example.test" in text
    assert "/api/v4" not in text  # citations are web links, never API URLs


EMPTY_CALLS: list[tuple[str, dict[str, Any], str]] = [
    ("gitlab_search_projects", {"query": "zzz"}, f"{API}/projects"),
    ("gitlab_search_code", {"query": "zzz"}, f"{API}/search"),
    ("gitlab_list_repository_tree", {"project": P}, f"{PR}/repository/tree"),
    ("gitlab_list_commits", {"project": P}, f"{PR}/repository/commits"),
    ("gitlab_list_merge_requests", {"project": P}, f"{PR}/merge_requests"),
    ("gitlab_list_issues", {"project": P}, f"{PR}/issues"),
    ("gitlab_list_pipelines", {"project": P}, f"{PR}/pipelines"),
]


@pytest.mark.parametrize(("tool", "args", "url"), EMPTY_CALLS, ids=[c[0] for c in EMPTY_CALLS])
@pytest.mark.asyncio
async def test_FR_002_AC_002_empty_branch_validates_against_contract(
    server, gitlab: respx.MockRouter, tool: str, args: dict[str, Any], url: str
) -> None:
    gitlab.get(url).mock(return_value=httpx.Response(200, json=[]))
    result = await _call(server, tool, args)
    assert not result.isError
    validate_structured_content(CONTRACT, tool, result.structuredContent)
    payload = result.structuredContent
    assert payload["status"] == "empty" and payload["items"] == [] and payload["citations"] == []
    assert payload["meta"]["query_echo"]
    assert result.content[0].text.startswith("Không tìm thấy")
    assert "Nguồn:" not in result.content[0].text


NOT_FOUND_CALLS: list[tuple[str, dict[str, Any], str]] = [
    ("gitlab_get_merge_request", {"project": P, "iid": "99"}, f"{PR}/merge_requests/99"),
    ("gitlab_get_issue", {"project": P, "iid": "99"}, f"{PR}/issues/99"),
    ("gitlab_get_pipeline", {"project": P, "pipeline_id": "99"}, f"{PR}/pipelines/99"),
    ("gitlab_get_file", {"project": P, "path": "nope.py"}, ""),
]


@pytest.mark.parametrize(
    ("tool", "args", "url"), NOT_FOUND_CALLS, ids=[c[0] for c in NOT_FOUND_CALLS]
)
@pytest.mark.asyncio
async def test_FR_002_AC_002_not_found_branch_validates_against_contract(
    server, gitlab: respx.MockRouter, tool: str, args: dict[str, Any], url: str
) -> None:
    if url:
        gitlab.get(url).mock(return_value=httpx.Response(404, json={"message": "404 Not found"}))
    else:
        gitlab.get(url__regex=r".*/repository/files/.*").mock(
            return_value=httpx.Response(404, json={"message": "404 File Not Found"})
        )
    result = await _call(server, tool, args)
    assert not result.isError
    validate_structured_content(CONTRACT, tool, result.structuredContent)
    assert result.structuredContent["status"] == "not_found"
    assert "không tồn tại" in result.content[0].text


@pytest.mark.asyncio
async def test_FR_002_AC_002_unknown_project_not_found_for_project_tools(
    server, readonly_respx_router: respx.MockRouter
) -> None:
    readonly_respx_router.get(url__regex=r".*/projects/.*").mock(
        return_value=httpx.Response(404, json={"message": "404 Project Not Found"})
    )
    for tool, args in [
        ("gitlab_list_repository_tree", {"project": "no/such"}),
        ("gitlab_list_pipelines", {"project": "no/such"}),
        ("gitlab_get_file", {"project": "no/such", "path": "a.py"}),
    ]:
        result = await _call(server, tool, args)
        assert result.structuredContent["status"] == "not_found", tool
        validate_structured_content(CONTRACT, tool, result.structuredContent)


@pytest.mark.asyncio
async def test_error_branch_validates_against_contract_timeout(
    server, readonly_respx_router: respx.MockRouter
) -> None:
    readonly_respx_router.get(f"{API}/projects").mock(side_effect=httpx.ConnectTimeout("timeout"))
    result = await _call(server, "gitlab_search_projects", {"query": "pay"})
    assert result.isError
    validate_structured_content(
        CONTRACT, "gitlab_search_projects", result.structuredContent, is_error=True
    )
    error = result.structuredContent["error"]
    assert error["code"] == "upstream_timeout" and error["source"] == "gitlab"
    assert "VPN" in error["details"]["hint"]


@pytest.mark.asyncio
async def test_error_branch_not_permitted_for_denied_file(server, readonly_respx_router) -> None:
    result = await _call(server, "gitlab_get_file", {"project": P, "path": "config/.env"})
    assert result.isError
    validate_structured_content(
        CONTRACT, "gitlab_get_file", result.structuredContent, is_error=True
    )
    error = result.structuredContent["error"]
    assert error["code"] == "not_permitted" and error["retryable"] is False
    assert readonly_respx_router.calls.call_count == 0


@pytest.mark.asyncio
async def test_error_branch_unauthorized_forbidden_invalid_input(
    server, readonly_respx_router: respx.MockRouter
) -> None:
    readonly_respx_router.get(f"{API}/projects").mock(return_value=httpx.Response(401, json={}))
    unauthorized = await _call(server, "gitlab_search_projects", {"query": "x"})
    assert unauthorized.structuredContent["error"]["code"] == "unauthorized"
    readonly_respx_router.get(f"{API}/search").mock(return_value=httpx.Response(403, json={}))
    forbidden = await _call(server, "gitlab_search_code", {"query": "xx"})
    assert forbidden.structuredContent["error"]["code"] == "forbidden"
    invalid = await _call(
        server, "gitlab_list_commits", {"project": P, "since": "2026-01-01T00:00:00"}
    )
    assert invalid.structuredContent["error"]["code"] == "invalid_input"
    assert invalid.structuredContent["error"]["details"]["field"] == "since"
    for res, tool in [
        (unauthorized, "gitlab_search_projects"),
        (forbidden, "gitlab_search_code"),
        (invalid, "gitlab_list_commits"),
    ]:
        validate_structured_content(CONTRACT, tool, res.structuredContent, is_error=True)


@pytest.mark.asyncio
async def test_unconfigured_server_reports_source_misconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for var in ("BASE_URL", "PRIVATE_TOKEN"):
        monkeypatch.delenv(f"MCP_GITLAB_{var}", raising=False)
    result = await _call(
        build_server(common=CommonSettings()), "gitlab_search_projects", {"query": "x"}
    )
    assert result.isError
    error = result.structuredContent["error"]
    assert error["code"] == "source_misconfigured"
    assert "MCP_GITLAB_BASE_URL" in error["details"]["missing_env"]
