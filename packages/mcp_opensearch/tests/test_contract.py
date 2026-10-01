"""T-035/T-036/T-037: `tools.snapshot.json` + `api-contract.yaml` (ADR-0013), 4 result branches.

Every tool is called through the real MCP protocol (in-memory client session) and its
`structuredContent` is validated against the contract's response schema.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from mcp_common.config import CommonSettings
from mcp_common.contract_testing import (
    assert_snapshot_matches_contract,
    build_snapshot,
    load_contract,
    validate_structured_content,
)
from mcp_opensearch.read_api import OpenSearchReadApi
from mcp_opensearch.server import build_server
from opensearchpy import exceptions as osx
from os_helpers import (
    ARGS_OK_SEARCH,
    OK_CALLS,
    FakeOpenSearch,
    install_ok_responses,
    search_response,
)

import mcp_opensearch

SNAPSHOT_PATH = Path(mcp_opensearch.__file__).parent / "tools.snapshot.json"
CONTRACT = load_contract()
EXPECTED_TOOLS = [
    "opensearch_aggregate",
    "opensearch_count",
    "opensearch_get_mapping",
    "opensearch_list_indices",
    "opensearch_search_dsl",
    "opensearch_search_logs",
]


def _snapshot() -> dict[str, Any]:
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def server(read_api: OpenSearchReadApi, fake: FakeOpenSearch):
    install_ok_responses(fake)
    return build_server(read_api, allow_dsl=True)


async def _call(server, name: str, args: dict[str, Any]):
    async with create_connected_server_and_client_session(server) as session:
        return await session.call_tool(name, arguments=args)


async def _tool_names(server) -> list[str]:
    async with create_connected_server_and_client_session(server) as session:
        return sorted(t.name for t in (await session.list_tools()).tools)


@pytest.mark.asyncio
async def test_snapshot_file_matches_live_tool_surface(read_api: OpenSearchReadApi) -> None:
    live = await build_snapshot(build_server(read_api, allow_dsl=True))
    assert _snapshot() == json.loads(json.dumps(live)), (
        "tools.snapshot.json is stale; regenerate with `uv run mcp-opensearch tools-dump`"
    )


def test_ADR_0013_snapshot_matches_contract_operations() -> None:
    assert_snapshot_matches_contract(_snapshot(), CONTRACT, tag="opensearch")


def test_snapshot_has_exactly_six_tools_within_r5_budget() -> None:
    assert sorted(_snapshot()) == EXPECTED_TOOLS and len(_snapshot()) <= 12


def test_tool_descriptions_are_at_most_three_sentences() -> None:
    for name, tool in _snapshot().items():
        sentences = [s for s in tool["description"].replace("...", "").split(". ") if s.strip()]
        assert len(sentences) <= 3, name


@pytest.mark.asyncio
async def test_dsl_tool_is_not_registered_unless_the_feature_flag_is_on(
    read_api: OpenSearchReadApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MCP_OPENSEARCH_ALLOW_DSL", raising=False)
    off = await _tool_names(build_server(read_api))
    assert off == [t for t in EXPECTED_TOOLS if t != "opensearch_search_dsl"]
    monkeypatch.setenv("MCP_OPENSEARCH_ALLOW_DSL", "true")
    assert await _tool_names(build_server(read_api)) == EXPECTED_TOOLS
    monkeypatch.setenv("MCP_OPENSEARCH_ALLOW_DSL", "false")
    assert await _tool_names(build_server(read_api, allow_dsl=True)) == EXPECTED_TOOLS


@pytest.mark.parametrize(("tool", "args"), OK_CALLS, ids=[c[0] for c in OK_CALLS])
@pytest.mark.asyncio
async def test_FR_004_AC_001_ok_branch_validates_against_contract(
    server, tool: str, args: dict[str, Any]
) -> None:
    result = await _call(server, tool, args)
    assert not result.isError, result.content
    validate_structured_content(CONTRACT, tool, result.structuredContent)
    assert result.structuredContent["status"] == "ok"
    assert result.structuredContent["meta"]["source"] == "opensearch"
    assert "Nguồn:" in result.content[0].text


EMPTY_CALLS: list[tuple[str, dict[str, Any], dict[str, Any]]] = [
    ("opensearch_list_indices", {"pattern": "zzz-*"}, {"cat.indices": []}),
    ("opensearch_search_logs", dict(ARGS_OK_SEARCH), {"search": search_response([])}),
    (
        "opensearch_count",
        {k: ARGS_OK_SEARCH[k] for k in ("index_pattern", "query", "time_from", "time_to")},
        {"count": {"count": 0}},
    ),
    (
        "opensearch_aggregate",
        {"index_pattern": "a", "agg_type": "terms", "field": "f",
         "time_from": "2026-09-30T10:00:00Z", "time_to": "2026-09-30T12:00:00Z"},
        {"search": search_response([], aggregations={"agg": {"buckets": []}})},
    ),
    (
        "opensearch_search_dsl",
        {"index_pattern": "a", "body": {"query": {"match_all": {}}}},
        {"search": search_response([])},
    ),
]  # fmt: skip


@pytest.mark.parametrize(
    ("tool", "args", "responses"), EMPTY_CALLS, ids=[c[0] for c in EMPTY_CALLS]
)
@pytest.mark.asyncio
async def test_FR_004_AC_002_empty_branch_validates_against_contract(
    server, fake: FakeOpenSearch, tool: str, args: dict[str, Any], responses: dict[str, Any]
) -> None:
    fake.responses.update(responses)
    result = await _call(server, tool, args)
    assert not result.isError
    validate_structured_content(CONTRACT, tool, result.structuredContent)
    payload = result.structuredContent
    assert payload["status"] == "empty" and payload["items"] == [] and payload["citations"] == []
    assert payload["meta"]["query_echo"]
    assert result.content[0].text.startswith("Không tìm thấy")


NOT_FOUND = osx.NotFoundError(404, "index_not_found_exception", {"error": {"type": "x"}})


@pytest.mark.parametrize(
    ("tool", "args", "key"),
    [
        ("opensearch_get_mapping", {"index": "nope"}, "indices.get_mapping"),
        ("opensearch_search_logs", dict(ARGS_OK_SEARCH), "search"),
        ("opensearch_search_dsl", {"index_pattern": "nope", "body": {"query": {}}}, "search"),
    ],
)
@pytest.mark.asyncio
async def test_FR_004_AC_002_not_found_branch_validates_against_contract(
    server, fake: FakeOpenSearch, tool: str, args: dict[str, Any], key: str
) -> None:
    fake.responses[key] = NOT_FOUND
    result = await _call(server, tool, args)
    assert not result.isError
    validate_structured_content(CONTRACT, tool, result.structuredContent)
    assert result.structuredContent["status"] == "not_found"
    assert "không tồn tại" in result.content[0].text


@pytest.mark.asyncio
async def test_error_branches_validate_against_contract(server, fake: FakeOpenSearch) -> None:
    fake.responses["search"] = osx.ConnectionTimeout("TIMEOUT", "t", Exception("x"))
    timeout = await _call(server, "opensearch_search_logs", dict(ARGS_OK_SEARCH))
    fake.responses["search"] = osx.AuthorizationException(403, "forbidden", {})
    forbidden = await _call(server, "opensearch_search_logs", dict(ARGS_OK_SEARCH))
    reversed_window = await _call(
        server,
        "opensearch_search_logs",
        {**ARGS_OK_SEARCH, "time_from": "2026-09-30T12:00:00Z", "time_to": "2026-09-30T10:00:00Z"},
    )
    scripted = await _call(
        server,
        "opensearch_search_dsl",
        {"index_pattern": "a", "body": {"query": {"bool": {"filter": [{"script": {}}]}}}},
    )
    for res, code in [
        (timeout, "upstream_timeout"),
        (forbidden, "forbidden"),
        (reversed_window, "invalid_input"),
        (scripted, "not_permitted"),
    ]:
        assert res.isError
        tool = "opensearch_search_dsl" if code == "not_permitted" else "opensearch_search_logs"
        validate_structured_content(CONTRACT, tool, res.structuredContent, is_error=True)
        assert res.structuredContent["error"]["code"] == code
        assert res.structuredContent["error"]["source"] == "opensearch"
    assert "VPN" in timeout.structuredContent["error"]["details"]["hint"]
    assert reversed_window.structuredContent["error"]["details"]["field"] == "time_from"
    details = scripted.structuredContent["error"]["details"]
    assert details["operation"] == "body.query.bool.filter[0].script"


@pytest.mark.asyncio
async def test_unconfigured_server_reports_source_misconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MCP_OPENSEARCH_HOSTS", raising=False)
    result = await _call(build_server(common=CommonSettings()), "opensearch_list_indices", {})
    assert result.isError
    error = result.structuredContent["error"]
    assert error["code"] == "source_misconfigured"
    assert "MCP_OPENSEARCH_HOSTS" in error["details"]["missing_env"]
