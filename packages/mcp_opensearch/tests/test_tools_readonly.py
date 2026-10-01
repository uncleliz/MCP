"""T-037 / NFR-001: the read-only surface of mcp-opensearch.

AC: FR-014/AC-001 (mutating/stateful operations refused), FR-014/AC-002 (unknown write tool
rejected at the MCP protocol layer), FR-004/AC-002 (script/scroll/PIT never reach the cluster).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from mcp_common.contract_testing import load_contract, operations_by_id
from mcp_common.testing import (
    assert_readonly_tool_surface,
    assert_unknown_tool_rejected_at_protocol_layer,
)
from mcp_common.tooling import registered_tool_functions
from mcp_opensearch.client import ALLOWED_OPERATIONS
from mcp_opensearch.read_api import OpenSearchReadApi
from mcp_opensearch.server import build_server
from os_helpers import FORBIDDEN_BODIES, OK_CALLS, FakeOpenSearch, install_ok_responses

import mcp_opensearch

PACKAGE_DIR = Path(mcp_opensearch.__file__).parent
CONTRACT = load_contract()


@pytest.fixture
def server(read_api: OpenSearchReadApi, fake: FakeOpenSearch):
    install_ok_responses(fake)
    return build_server(read_api, allow_dsl=True)


def test_FR_014_AC_001_tool_surface_is_readonly_and_in_contract(server) -> None:
    snapshot = json.loads((PACKAGE_DIR / "tools.snapshot.json").read_text(encoding="utf-8"))
    assert_readonly_tool_surface(
        registered_tools=registered_tool_functions(server),
        contract_operations=operations_by_id(CONTRACT),
        snapshot=snapshot,
        client_allowlist=ALLOWED_OPERATIONS,
        # mcp-ingest's OpenSearch connector calls client.py directly (ADR-0012 A4).
        additional_client_operations=ALLOWED_OPERATIONS,
    )


@pytest.mark.parametrize(
    "write_tool",
    [
        "opensearch_index_document",
        "opensearch_delete_index",
        "opensearch_update_document",
        "opensearch_bulk",
        "opensearch_reindex",
        "opensearch_scroll",
        "opensearch_create_pit",
    ],
)
@pytest.mark.asyncio
async def test_FR_014_AC_002_unknown_write_tool_rejected_at_protocol_layer(
    server, write_tool: str
) -> None:
    await assert_unknown_tool_rejected_at_protocol_layer(server, write_tool, {"index": "x"})


@pytest.mark.asyncio
async def test_every_tool_call_only_uses_search_count_mapping_and_cat(
    server, fake: FakeOpenSearch
) -> None:
    async with create_connected_server_and_client_session(server) as session:
        for name, args in OK_CALLS:
            result = await session.call_tool(name, arguments=args)
            assert not result.isError, (name, result.content)
    used = {name for name, _ in fake.calls}
    assert used <= {"search", "count", "cat.indices", "indices.get_mapping"}
    for _name, kwargs in fake.calls:
        assert "scroll" not in json.dumps(kwargs, default=str)
        assert "point_in_time" not in json.dumps(kwargs, default=str)


@pytest.mark.parametrize(
    "construct",
    ["script", "scripted_metric", "runtime_mappings", "scroll", "pit"],
)
@pytest.mark.asyncio
async def test_TC_018_forbidden_dsl_constructs_never_reach_the_cluster(
    server, fake: FakeOpenSearch, construct: str
) -> None:
    result = await _call(server, FORBIDDEN_BODIES[construct])
    assert result.isError and result.structuredContent["error"]["code"] == "not_permitted"
    assert fake.calls == []


async def _call(server, body: dict):
    async with create_connected_server_and_client_session(server) as session:
        return await session.call_tool(
            "opensearch_search_dsl", arguments={"index_pattern": "a", "body": body}
        )


def test_source_never_calls_a_write_api() -> None:
    pattern = re.compile(
        r"\.(index|create|update|delete|bulk|reindex|scroll|clear_scroll|create_pit|delete_pit|"
        r"delete_by_query|update_by_query|put_mapping|put_settings|open|close_index)\("
    )
    offenders: list[str] = []
    for path in PACKAGE_DIR.glob("*.py"):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{path.name}:{number}: {line.strip()}")
    assert offenders == []
