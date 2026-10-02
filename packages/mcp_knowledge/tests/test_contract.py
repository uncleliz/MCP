"""T-103: `tools.snapshot.json` + `api-contract.yaml` for mcp-knowledge (8 tools, ADR-0013/0004).

The snapshot checks need no database. The result-branch checks drive the tools through the real MCP
protocol (in-memory client session) with the FakeKnowledgeClient / FakeRetriever and validate the
`structuredContent` against the contract's response schema.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from knowledge_helpers import (
    DOCUMENT_ROW,
    ENTITY_ROW,
    NEIGHBOUR_ROWS,
    SUMMARY_ROW,
    VERSION_ROWS,
    FakeKnowledgeClient,
    FakeRetriever,
    make_candidate,
)
from mcp.shared.memory import create_connected_server_and_client_session
from mcp_common.contract_testing import (
    build_snapshot,
    load_contract,
    operations_by_id,
    validate_structured_content,
)
from mcp_common.testing import assert_readonly_tool_surface
from mcp_common.tooling import registered_tool_functions
from mcp_knowledge.server import build_server
from mcp_knowledge.tools import KnowledgeReadApi, make_tools
from mcp_knowledge.tools.read_api import KnowledgeReadApi as _ReadApi

import mcp_knowledge

SNAPSHOT_PATH = Path(mcp_knowledge.__file__).parent / "tools.snapshot.json"
CONTRACT = load_contract()
EXPECTED_TOOLS = [
    "find_related_knowledge", "get_document_version", "get_jira_context",
    "get_knowledge_summary", "get_repository", "get_service", "search_code",
    "search_company_knowledge",
]  # fmt: skip


def _snapshot() -> dict[str, Any]:
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


def _api(client: FakeKnowledgeClient, candidates=None) -> KnowledgeReadApi:
    return _ReadApi(client, FakeRetriever(candidates or []))


def _server(client: FakeKnowledgeClient, candidates=None):
    return build_server(_api(client, candidates))


async def _call(server, name: str, args: dict[str, Any]):
    async with create_connected_server_and_client_session(server) as session:
        return await session.call_tool(name, arguments=args)


# -- snapshot / surface (no DB) ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_snapshot_file_matches_live_tool_surface() -> None:
    live = await build_snapshot(build_server(_api(FakeKnowledgeClient())))
    assert _snapshot() == json.loads(json.dumps(live)), (
        "tools.snapshot.json is stale; regenerate with `uv run mcp-knowledge tools-dump`"
    )


def test_snapshot_has_exactly_eight_tools() -> None:
    assert sorted(_snapshot()) == EXPECTED_TOOLS


def test_there_is_no_tool_that_writes() -> None:
    for name, tool in _snapshot().items():
        assert not any(w in name for w in ("create", "update", "delete", "write", "transition"))
        assert not {"sql", "statement", "raw_query"} & set(tool["inputSchema"]["properties"])


def test_readonly_tool_surface_holds() -> None:
    assert_readonly_tool_surface(
        registered_tools=registered_tool_functions(build_server(_api(FakeKnowledgeClient()))),
        contract_operations=operations_by_id(CONTRACT),
        snapshot=_snapshot(),
    )


def test_every_new_operation_is_marked_read_only_in_the_contract() -> None:
    ops = operations_by_id(CONTRACT)
    for name in EXPECTED_TOOLS:
        assert ops[name].get("x-readonly") is True, name
        assert ops[name].get("x-side-effects") == "none", name


def test_all_eight_tool_functions_are_marked_readonly() -> None:
    from mcp_common.readonly import is_readonly_tool

    tools = make_tools(lambda: _api(FakeKnowledgeClient()))
    assert len(tools) == 8
    assert all(is_readonly_tool(fn) for fn in tools.values())


# -- result branches validate against the contract ----------------------------------------------


@pytest.mark.asyncio
async def test_get_service_ok_and_not_found_validate() -> None:
    ok = await _call(
        _server(FakeKnowledgeClient({"entity_by_type_name": [ENTITY_ROW],
                                     "entity_neighbours": NEIGHBOUR_ROWS})),
        "get_service", {"name": "payment-service"},
    )  # fmt: skip
    assert not ok.isError, ok.content
    validate_structured_content(CONTRACT, "get_service", ok.structuredContent)
    assert ok.structuredContent["status"] == "ok"
    assert ok.structuredContent["items"][0]["related"][0]["depth"] == 1

    missing = await _call(_server(FakeKnowledgeClient()), "get_service", {"name": "nope"})
    assert not missing.isError
    validate_structured_content(CONTRACT, "get_service", missing.structuredContent)
    assert missing.structuredContent["status"] == "not_found"


@pytest.mark.asyncio
async def test_get_repository_ok_validates() -> None:
    repo = dict(ENTITY_ROW, entity_type="repository", name="team/payment-service")
    res = await _call(
        _server(FakeKnowledgeClient({"entity_by_type_name": [repo], "entity_neighbours": []})),
        "get_repository", {"name": "team/payment-service"},
    )
    assert not res.isError
    validate_structured_content(CONTRACT, "get_repository", res.structuredContent)
    assert res.structuredContent["status"] == "ok"


@pytest.mark.asyncio
async def test_get_knowledge_summary_ok_and_not_found_validate() -> None:
    client = FakeKnowledgeClient(
        {"knowledge_summary": [SUMMARY_ROW], "document_citation": [DOCUMENT_ROW]}
    )
    ok = await _call(_server(client), "get_knowledge_summary", {"subject": "payment-service"})
    assert not ok.isError, ok.content
    validate_structured_content(CONTRACT, "get_knowledge_summary", ok.structuredContent)
    assert ok.structuredContent["status"] == "ok"

    nf = await _call(_server(FakeKnowledgeClient()), "get_knowledge_summary", {"subject": "x"})
    validate_structured_content(CONTRACT, "get_knowledge_summary", nf.structuredContent)
    assert nf.structuredContent["status"] == "not_found"


@pytest.mark.asyncio
async def test_get_document_version_ok_empty_and_not_found_validate() -> None:
    uid = "3f2504e0-4f89-11d3-9a0c-0305e82c3301"
    ok = await _call(
        _server(FakeKnowledgeClient({"live_document": [DOCUMENT_ROW],
                                     "document_versions": VERSION_ROWS})),
        "get_document_version", {"document_id": uid},
    )  # fmt: skip
    assert not ok.isError, ok.content
    validate_structured_content(CONTRACT, "get_document_version", ok.structuredContent)
    assert ok.structuredContent["status"] == "ok"
    assert ok.structuredContent["items"][0]["source_version"] == "12"

    # tombstoned/missing document -> not_found
    nf = await _call(_server(FakeKnowledgeClient()), "get_document_version", {"document_id": uid})
    validate_structured_content(CONTRACT, "get_document_version", nf.structuredContent)
    assert nf.structuredContent["status"] == "not_found"

    # live document but no versions yet -> empty
    empty = await _call(
        _server(FakeKnowledgeClient({"live_document": [DOCUMENT_ROW], "document_versions": []})),
        "get_document_version", {"document_id": uid},
    )
    validate_structured_content(CONTRACT, "get_document_version", empty.structuredContent)
    assert empty.structuredContent["status"] == "empty"


@pytest.mark.asyncio
async def test_search_code_ok_and_empty_validate() -> None:
    ok = await _call(
        _server(FakeKnowledgeClient(), [make_candidate()]),
        "search_code", {"query": "retry backoff"},
    )
    assert not ok.isError, ok.content
    validate_structured_content(CONTRACT, "search_code", ok.structuredContent)
    assert ok.structuredContent["status"] == "ok"
    assert ok.structuredContent["items"][0]["source_uri"].startswith("https://")
    assert ok.structuredContent["items"][0]["embedding_model"] == "fake/hashed-bow"

    empty = await _call(_server(FakeKnowledgeClient(), []), "search_code", {"query": "zzz"})
    validate_structured_content(CONTRACT, "search_code", empty.structuredContent)
    assert empty.structuredContent["status"] == "empty"


@pytest.mark.asyncio
async def test_find_related_knowledge_not_found_validates() -> None:
    res = await _call(
        _server(FakeKnowledgeClient()), "find_related_knowledge", {"entity": "ghost"}
    )
    validate_structured_content(CONTRACT, "find_related_knowledge", res.structuredContent)
    assert res.structuredContent["status"] == "not_found"


@pytest.mark.asyncio
async def test_input_schema_rejects_out_of_bounds() -> None:
    server = _server(FakeKnowledgeClient())
    bad_depth = await _call(server, "find_related_knowledge", {"entity": "x", "max_depth": 9})
    assert bad_depth.isError  # schema maximum=3
    short = await _call(server, "search_company_knowledge", {"query": "ab"})
    assert short.isError  # schema minLength=3
