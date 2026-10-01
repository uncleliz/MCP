"""T-040 / NFR-001: the read-only surface of mcp-kibana (FR-014/AC-001, FR-014/AC-002)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import respx
from kibana_helpers import OK_CALLS, mock_ok
from mcp.shared.memory import create_connected_server_and_client_session
from mcp_common.contract_testing import load_contract, operations_by_id
from mcp_common.testing import (
    assert_readonly_tool_surface,
    assert_unknown_tool_rejected_at_protocol_layer,
)
from mcp_common.tooling import registered_tool_functions
from mcp_kibana.client import ALLOWED_OPERATIONS
from mcp_kibana.read_api import KibanaReadApi
from mcp_kibana.server import build_server

import mcp_kibana

PACKAGE_DIR = Path(mcp_kibana.__file__).parent
CONTRACT = load_contract()


@pytest.fixture
def server(read_api: KibanaReadApi):
    return build_server(read_api)


def test_FR_014_AC_001_tool_surface_is_readonly_and_in_contract(server) -> None:
    snapshot = json.loads((PACKAGE_DIR / "tools.snapshot.json").read_text(encoding="utf-8"))
    assert_readonly_tool_surface(
        registered_tools=registered_tool_functions(server),
        contract_operations=operations_by_id(CONTRACT),
        snapshot=snapshot,
        client_allowlist=ALLOWED_OPERATIONS,
    )


@pytest.mark.parametrize(
    "write_tool",
    [
        "kibana_create_dashboard",
        "kibana_update_saved_object",
        "kibana_delete_saved_object",
        "kibana_import_saved_objects",
        "kibana_create_space",
    ],
)
@pytest.mark.asyncio
async def test_FR_014_AC_002_unknown_write_tool_rejected_at_protocol_layer(
    server, write_tool: str
) -> None:
    await assert_unknown_tool_rejected_at_protocol_layer(server, write_tool, {"id": "x"})


@pytest.mark.asyncio
async def test_every_tool_call_only_issues_get_requests(server, kibana: respx.MockRouter) -> None:
    mock_ok(kibana)
    async with create_connected_server_and_client_session(server) as session:
        for name, args in OK_CALLS:
            result = await session.call_tool(name, arguments=args)
            assert not result.isError, (name, result.content)
    assert {call.request.method for call in kibana.calls} == {"GET"}
    assert all("kbn-xsrf" not in call.request.headers for call in kibana.calls)
    # build_dashboard_link issues exactly one GET (contract); the other two tools one each.
    assert kibana.calls.call_count == len(OK_CALLS)
