"""T-062/T-066 / NFR-001: the read-only surface of mcp-pgvector (FR-011/AC-003, FR-014/AC-001,
FR-014/AC-002)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from mcp_common.config import CommonSettings
from mcp_common.contract_testing import load_contract, operations_by_id
from mcp_common.testing import (
    assert_readonly_tool_surface,
    assert_unknown_tool_rejected_at_protocol_layer,
)
from mcp_common.tooling import registered_tool_functions
from mcp_pgvector.client import ALLOWED_STATEMENTS
from mcp_pgvector.server import build_server
from pg_helpers import FakeClient, make_api

import mcp_pgvector

PACKAGE_DIR = Path(mcp_pgvector.__file__).parent
CONTRACT = load_contract()


@pytest.fixture
def server(common: CommonSettings):
    return build_server(make_api(FakeClient(), common))


def test_FR_014_AC_001_tool_surface_is_readonly_and_in_contract(server) -> None:
    snapshot = json.loads((PACKAGE_DIR / "tools.snapshot.json").read_text(encoding="utf-8"))
    assert_readonly_tool_surface(
        registered_tools=registered_tool_functions(server),
        contract_operations=operations_by_id(CONTRACT),
        snapshot=snapshot,
        client_allowlist=ALLOWED_STATEMENTS,
    )


@pytest.mark.parametrize(
    "write_tool",
    [
        "kb_insert_document",
        "kb_delete_document",
        "kb_update_chunk",
        "kb_upsert",
        "kb_reindex",
        "kb_reembed",
        "kb_purge",
        "postgres_query",
        "pgvector_execute_sql",
        "kb_run_sql",
    ],
)
@pytest.mark.asyncio
async def test_FR_014_AC_002_unknown_write_tool_rejected_at_protocol_layer(
    server, write_tool: str
) -> None:
    await assert_unknown_tool_rejected_at_protocol_layer(server, write_tool, {"sql": "DELETE 1"})


def test_the_three_contract_operations_are_marked_readonly_in_the_contract() -> None:
    operations = operations_by_id(CONTRACT)
    for name in ("kb_semantic_search", "kb_get_document", "kb_list_sources"):
        assert operations[name]["x-readonly"] is True
        assert operations[name]["x-side-effects"] == "none"
