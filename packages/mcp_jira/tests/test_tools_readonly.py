"""T-094 / NFR-006: the read-only surface of mcp-jira.

AC: FR-017/AC-003 (no write path), FR-014/AC-001 (mutating operations refused),
FR-014/AC-002 (an unknown "write" tool is rejected at the MCP protocol layer, not via
ErrorEnvelope). Adversarial: create/transition/comment must not exist and must be rejected.
"""

from __future__ import annotations

import json
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
from mcp_jira.client import ALLOWED_OPERATIONS, JiraClient
from mcp_jira.read_api import JiraReadApi
from mcp_jira.server import build_server

import mcp_jira

from .jira_helpers import issue_url, project_search_url, project_url, search_url, sprint_url

PACKAGE_DIR = Path(mcp_jira.__file__).parent
CONTRACT = load_contract()


@pytest.fixture
def server(read_api: JiraReadApi):
    return build_server(read_api)


def test_FR_017_AC_003_tool_surface_is_readonly_and_in_contract(server) -> None:
    snapshot = json.loads((PACKAGE_DIR / "tools.snapshot.json").read_text(encoding="utf-8"))
    assert_readonly_tool_surface(
        registered_tools=registered_tool_functions(server),
        contract_operations=operations_by_id(CONTRACT),
        snapshot=snapshot,
        client_allowlist=ALLOWED_OPERATIONS,
        # mcp-ingest's Jira connector calls client.py directly (ADR-0012 A4).
        additional_client_operations=ALLOWED_OPERATIONS,
    )


def test_exactly_the_five_contract_tools_are_registered(server) -> None:
    assert sorted(registered_tool_functions(server)) == [
        "jira_get_issue",
        "jira_get_sprint",
        "jira_list_board_sprints",
        "jira_list_projects",
        "jira_search_issues",
    ]


def test_no_write_tool_is_registered(server) -> None:
    names = set(registered_tool_functions(server))
    for forbidden in ("jira_create_issue", "jira_transition_issue", "jira_add_comment"):
        assert forbidden not in names


def test_tool_names_carry_no_write_verbs(server) -> None:
    # Heuristic only (ADR-0003 A2): a warning signal, never a hard gate.
    assert warn_if_tool_name_matches_deny_regex(registered_tool_functions(server)) == []


@pytest.mark.parametrize(
    "write_tool",
    [
        "jira_create_issue",
        "jira_transition_issue",
        "jira_add_comment",
        "jira_update_issue",
        "jira_delete_issue",
    ],
)
@pytest.mark.asyncio
async def test_FR_014_AC_002_unknown_write_tool_rejected_at_protocol_layer(
    server, write_tool: str
) -> None:
    await assert_unknown_tool_rejected_at_protocol_layer(server, write_tool, {"key": "PAY-1"})


@pytest.mark.asyncio
async def test_every_tool_call_only_issues_get_requests(
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

    calls: list[tuple[str, dict[str, Any]]] = [
        ("jira_search_issues", {"jql": "project = PAY"}),
        ("jira_get_issue", {"key": "PAY-1234"}),
        ("jira_list_projects", {}),
        ("jira_get_sprint", {"sprint_id": 42}),
    ]
    async with create_connected_server_and_client_session(server) as session:
        for name, args in calls:
            result = await session.call_tool(name, arguments=args)
            assert not result.isError, result.content
    methods = {call.request.method for call in readonly_respx_router.calls}
    assert methods == {"GET"}


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH"])
async def test_transport_blocks_every_write_method(
    client: JiraClient, readonly_respx_router: respx.MockRouter, base: str, method: str
) -> None:
    with pytest.raises(NotPermittedError):
        await client.http.request(method, f"{base}/rest/api/2/issue/PAY-1")
    assert readonly_respx_router.calls.call_count == 0


def test_contract_marks_all_jira_tools_readonly() -> None:
    ops = operations_by_id(CONTRACT)
    jira_ops = {k: v for k, v in ops.items() if "jira" in v.get("tags", [])}
    assert len(jira_ops) == 5
    for name, op in jira_ops.items():
        assert op.get("x-readonly") is True, name
        assert op.get("x-side-effects") == "none", name
