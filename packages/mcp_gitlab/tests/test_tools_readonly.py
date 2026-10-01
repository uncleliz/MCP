"""T-028 / NFR-001: the read-only surface of mcp-gitlab.

AC: FR-014/AC-001 (mutating operations refused), FR-014/AC-002 (unknown write tool rejected
at the MCP protocol layer), FR-002/AC-003 (no tool can write; deny-glob enforced).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import respx
from gitlab_helpers import API, OK_CALLS, PROJ, json_response, mock_ok
from mcp.shared.memory import create_connected_server_and_client_session
from mcp_common.contract_testing import load_contract, operations_by_id
from mcp_common.errors import NotPermittedError
from mcp_common.testing import (
    assert_readonly_tool_surface,
    assert_unknown_tool_rejected_at_protocol_layer,
    warn_if_tool_name_matches_deny_regex,
)
from mcp_common.tooling import registered_tool_functions
from mcp_gitlab.client import ALLOWED_OPERATIONS, GitLabClient
from mcp_gitlab.read_api import GitLabReadApi
from mcp_gitlab.server import build_server

import mcp_gitlab

PACKAGE_DIR = Path(mcp_gitlab.__file__).parent
CONTRACT = load_contract()
BASE_PR = f"{API}/projects/{PROJ}"


@pytest.fixture
def server(read_api: GitLabReadApi):
    return build_server(read_api)


def test_FR_014_AC_001_tool_surface_is_readonly_and_in_contract(server) -> None:
    snapshot = json.loads((PACKAGE_DIR / "tools.snapshot.json").read_text(encoding="utf-8"))
    assert_readonly_tool_surface(
        registered_tools=registered_tool_functions(server),
        contract_operations=operations_by_id(CONTRACT),
        snapshot=snapshot,
        client_allowlist=ALLOWED_OPERATIONS,
        # mcp-ingest's GitLab connector calls client.py directly (ADR-0012 A4).
        additional_client_operations=ALLOWED_OPERATIONS,
    )


def test_ADR_0003_A2_merge_in_tool_name_is_only_a_warning_not_a_failure(server) -> None:
    names = registered_tool_functions(server)
    assert {"gitlab_list_merge_requests", "gitlab_get_merge_request"} <= set(names)
    # The surface assertion above already passed: name heuristics never gate (ADR-0003 A2).
    assert set(warn_if_tool_name_matches_deny_regex(names)) == {
        "gitlab_list_merge_requests",
        "gitlab_get_merge_request",
    }


@pytest.mark.parametrize(
    "write_tool",
    [
        "gitlab_create_issue",
        "gitlab_create_merge_request",
        "gitlab_merge_merge_request",
        "gitlab_push_files",
        "gitlab_delete_branch",
        "gitlab_update_issue",
        "gitlab_add_note",
        "gitlab_retry_pipeline",
    ],
)
@pytest.mark.asyncio
async def test_FR_014_AC_002_unknown_write_tool_rejected_at_protocol_layer(
    server, write_tool: str
) -> None:
    await assert_unknown_tool_rejected_at_protocol_layer(server, write_tool, {"project": "x"})


@pytest.mark.asyncio
async def test_every_tool_call_only_issues_get_requests(server, gitlab: respx.MockRouter) -> None:
    mock_ok(gitlab)
    async with create_connected_server_and_client_session(server) as session:
        for name, args in OK_CALLS:
            result = await session.call_tool(name, arguments=args)
            assert not result.isError, (name, result.content)
    assert {call.request.method for call in gitlab.calls} == {"GET"}
    assert gitlab.calls.call_count > len(OK_CALLS)
    # readonly_respx_router also re-asserts at teardown that nothing non-GET/HEAD went out.


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/projects/42/issues"),  # create issue
        ("PUT", "/projects/42/merge_requests/12/merge"),  # merge
        ("POST", "/projects/42/repository/commits"),  # push
        ("DELETE", "/projects/42/repository/branches/main"),
        ("PATCH", "/projects/42"),
    ],
)
@pytest.mark.asyncio
async def test_NFR_001_typical_write_operations_blocked_by_transport(
    client: GitLabClient, readonly_respx_router: respx.MockRouter, method: str, path: str
) -> None:
    with pytest.raises(NotPermittedError):
        await client.http.request(method, f"{API}{path}")
    assert readonly_respx_router.calls.call_count == 0


@pytest.mark.parametrize(
    "operation",
    [
        "POST /api/v4/projects/{id}/issues",
        "PUT /api/v4/projects/{id}/merge_requests/{iid}/merge",
        "POST /api/v4/projects/{id}/repository/commits",
        "GET /api/v4/projects/{id}/variables",  # a GET that would expose CI secrets
        "GET /api/v4/projects/{id}/deploy_tokens",
        "GET /api/v4/user",
    ],
)
@pytest.mark.asyncio
async def test_operation_allowlist_refuses_anything_not_listed(
    client: GitLabClient, readonly_respx_router: respx.MockRouter, operation: str
) -> None:
    with pytest.raises(NotPermittedError) as exc:
        await client.get(operation, {"id": "42", "iid": "1"})
    assert exc.value.details["operation"] == operation
    assert readonly_respx_router.calls.call_count == 0


@pytest.mark.asyncio
async def test_FR_002_AC_003_deny_glob_applies_across_tools(
    server, gitlab: respx.MockRouter
) -> None:
    gitlab.get(f"{API}/search").mock(return_value=json_response("search_blobs.json"))
    gitlab.get(f"{BASE_PR}/merge_requests/12").mock(return_value=json_response("mr_12.json"))
    gitlab.get(f"{BASE_PR}/merge_requests/12/changes").mock(
        return_value=json_response("mr_12_changes.json")
    )
    async with create_connected_server_and_client_session(server) as session:
        direct = await session.call_tool(
            "gitlab_get_file", {"project": "team/payment-service", "path": "deploy/prod.env"}
        )
        search = await session.call_tool("gitlab_search_code", {"query": "password"})
        mr = await session.call_tool(
            "gitlab_get_merge_request", {"project": "team/payment-service", "iid": "12"}
        )
    assert direct.isError and direct.structuredContent["error"]["code"] == "not_permitted"
    leaked = json.dumps([search.structuredContent, mr.structuredContent])
    assert "hunter2" not in leaked and "newsecretvalue" not in leaked


def test_source_never_calls_a_write_verb_on_http() -> None:
    pattern = re.compile(r"\.(post|put|delete|patch)\(|\"(POST|PUT|DELETE|PATCH)\"")
    offenders: list[str] = []
    for path in PACKAGE_DIR.glob("*.py"):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{path.name}:{number}: {line.strip()}")
    assert offenders == []


def test_allowlist_has_only_get() -> None:
    assert all(op.startswith("GET ") for op in ALLOWED_OPERATIONS)
