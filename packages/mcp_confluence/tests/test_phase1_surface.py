"""T-031 (automated part): the Phase 1 surface as Claude would see it — 15 tools, 0 write tools.

The manual half of sign-off (register in Claude Desktop/Code, live `doctor`) is tracked in
docs/signoff/phase-1.md; it needs real credentials and cannot run in CI.
"""

from __future__ import annotations

import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from mcp_common.contract_testing import load_contract, operations_by_id
from mcp_common.tooling import registered_tool_functions
from mcp_confluence.server import build_server as build_confluence
from mcp_gitlab.server import build_server as build_gitlab


@pytest.mark.asyncio
async def test_NFR_005_both_servers_answer_tools_list_with_15_tools() -> None:
    names: list[str] = []
    for build in (build_confluence, build_gitlab):
        async with create_connected_server_and_client_session(build()) as session:
            names += [tool.name for tool in (await session.list_tools()).tools]
    assert len(names) == 15 and len(set(names)) == 15
    assert sum(n.startswith("confluence_") for n in names) == 4
    assert sum(n.startswith("gitlab_") for n in names) == 11


def test_NFR_001_every_registered_tool_is_a_readonly_contract_operation() -> None:
    operations = operations_by_id(load_contract())
    for build in (build_confluence, build_gitlab):
        for name in registered_tool_functions(build()):
            assert operations[name]["x-readonly"] is True
            assert operations[name]["x-side-effects"] == "none"
