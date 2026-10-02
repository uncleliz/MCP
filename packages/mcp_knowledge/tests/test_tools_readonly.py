"""T-103 / TC-105: the mcp-knowledge tool surface is read-only at the JSON-RPC layer.

A fabricated write tool name must be rejected by the MCP SDK itself (not an ErrorEnvelope), and no
registered tool may be a write tool.
"""

from __future__ import annotations

import pytest
from knowledge_helpers import FakeKnowledgeClient, FakeRetriever
from mcp_common.readonly import is_readonly_tool
from mcp_common.testing import assert_unknown_tool_rejected_at_protocol_layer
from mcp_common.tooling import registered_tool_functions
from mcp_knowledge.server import build_server
from mcp_knowledge.tools.read_api import KnowledgeReadApi

pytestmark = pytest.mark.asyncio

_WRITE_PROBES = (
    "knowledge_delete_document", "knowledge_create_entity", "update_summary", "write_relationship",
)  # fmt: skip


def _server():
    return build_server(KnowledgeReadApi(FakeKnowledgeClient(), FakeRetriever([])))


async def test_unknown_write_tool_rejected_at_protocol_layer() -> None:
    server = _server()
    for probe in _WRITE_PROBES:
        await assert_unknown_tool_rejected_at_protocol_layer(server, probe, {"id": "x"})


async def test_all_registered_tools_are_readonly_marked() -> None:
    tools = registered_tool_functions(_server())
    assert len(tools) == 8
    for name, fn in tools.items():
        assert is_readonly_tool(fn), name


async def test_no_registered_tool_name_is_a_write_verb() -> None:
    for name in registered_tool_functions(_server()):
        assert not any(verb in name for verb in ("create", "update", "delete", "write", "put"))
