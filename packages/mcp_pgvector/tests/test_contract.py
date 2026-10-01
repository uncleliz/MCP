"""T-063/T-064/T-066: `tools.snapshot.json` + `api-contract.yaml` (ADR-0013), 4 result branches.

Every tool is called through the real MCP protocol (in-memory client session) against a real
PostgreSQL+pgvector (seeded with the deterministic fake embedder) and its `structuredContent` is
validated against the contract's response schema. The snapshot checks need no database.
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
from mcp_pgvector.server import build_server
from pg_helpers import (
    CONFLUENCE_URI,
    DOC_ROW,
    FakeClient,
    as_user,
    chunk_row,
    chunks_of,
    freshness_row,
    make_api,
    real_api,
)

import mcp_pgvector

SNAPSHOT_PATH = Path(mcp_pgvector.__file__).parent / "tools.snapshot.json"
CONTRACT = load_contract()
EXPECTED_TOOLS = ["kb_get_document", "kb_list_sources", "kb_semantic_search"]


def _snapshot() -> dict[str, Any]:
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


async def _call(server, name: str, args: dict[str, Any]):
    async with create_connected_server_and_client_session(server) as session:
        return await session.call_tool(name, arguments=args)


def _api(common: CommonSettings, client: FakeClient):
    return make_api(client, common)


@pytest.mark.asyncio
async def test_snapshot_file_matches_live_tool_surface(common: CommonSettings) -> None:
    live = await build_snapshot(build_server(_api(common, FakeClient())))
    assert _snapshot() == json.loads(json.dumps(live)), (
        "tools.snapshot.json is stale; regenerate with `uv run mcp-pgvector tools-dump`"
    )


def test_ADR_0013_snapshot_matches_contract_operations() -> None:
    assert_snapshot_matches_contract(_snapshot(), CONTRACT, tag="pgvector")


def test_snapshot_has_exactly_three_tools_within_r5_budget() -> None:
    assert sorted(_snapshot()) == EXPECTED_TOOLS and len(_snapshot()) <= 12


def test_there_is_no_tool_that_accepts_sql() -> None:
    for name, tool in _snapshot().items():
        assert "sql" not in name and "query_sql" not in name
        assert not {"sql", "statement", "raw_query"} & set(tool["inputSchema"]["properties"])


def test_tool_descriptions_are_at_most_three_sentences() -> None:
    for name, tool in _snapshot().items():
        sentences = [s for s in tool["description"].replace("...", "").split(". ") if s.strip()]
        assert len(sentences) <= 3, name


@pytest.mark.asyncio
async def test_FR_011_AC_001_search_ok_branch_validates_against_contract(
    ro_dsn: str, seeded_ids, common: CommonSettings
) -> None:
    server = build_server(real_api(ro_dsn, common))
    result = await _call(server, "kb_semantic_search", {"query": "payment worker retry backoff"})
    assert not result.isError, result.content
    validate_structured_content(CONTRACT, "kb_semantic_search", result.structuredContent)
    payload = result.structuredContent
    assert payload["status"] == "ok" and payload["meta"]["source"] == "pgvector"
    assert payload["items"][0]["source_uri"] == CONFLUENCE_URI
    assert payload["citations"][0]["uri"] == CONFLUENCE_URI
    assert payload["meta"]["data_freshness"]["embedding_model"] == "fake/hashed-bow"
    text = result.content[0].text
    assert "Nguồn:" in text and CONFLUENCE_URI in text and "Độ mới dữ liệu" in text


@pytest.mark.asyncio
async def test_FR_011_AC_002_both_empty_causes_validate_against_contract(
    ro_dsn: str, seeded_ids, common: CommonSettings
) -> None:
    server = build_server(real_api(ro_dsn, common))
    nothing = await _call(server, "kb_semantic_search", {"query": "zzqx quuxbar vlorp"})
    excluded = await _call(
        server, "kb_semantic_search",
        {"query": "payment worker retry backoff", "source_types": ["gitlab"]},
    )  # fmt: skip
    for res in (nothing, excluded):
        assert not res.isError
        validate_structured_content(CONTRACT, "kb_semantic_search", res.structuredContent)
        payload = res.structuredContent
        assert (
            payload["status"] == "empty" and payload["items"] == [] and payload["citations"] == []
        )
        assert res.content[0].text.startswith("Không tìm thấy")
    assert "best_similarity" in nothing.structuredContent["meta"]["warnings"][0]
    assert "filters may have excluded" in excluded.structuredContent["meta"]["warnings"][0]
    assert "Lưu ý:" in nothing.content[0].text  # the warning reaches the text Claude reads


@pytest.mark.asyncio
async def test_get_document_branches_validate_against_contract(
    ro_dsn: str, seeded_ids, common: CommonSettings
) -> None:
    server = build_server(real_api(ro_dsn, common))
    ok = await _call(server, "kb_get_document", {"document_id": seeded_ids["123456"]})
    partial = await _call(
        server, "kb_get_document", {"document_id": seeded_ids["123456"], "max_chars": 500}
    )
    missing = await _call(
        server, "kb_get_document", {"document_id": "00000000-0000-0000-0000-000000000000"}
    )
    dead = await _call(server, "kb_get_document", {"document_id": seeded_ids["dead"]})
    for res, status in ((ok, "ok"), (missing, "not_found"), (dead, "not_found")):
        assert not res.isError
        validate_structured_content(CONTRACT, "kb_get_document", res.structuredContent)
        assert res.structuredContent["status"] == status
    assert "không tồn tại" in missing.content[0].text
    assert partial.structuredContent["status"] in {"ok", "partial"}
    validate_structured_content(CONTRACT, "kb_get_document", partial.structuredContent)


@pytest.mark.asyncio
async def test_get_document_partial_branch_validates_against_contract(
    common: CommonSettings,
) -> None:
    client = FakeClient(
        {"document_by_key": [DOC_ROW], "document_chunks": chunks_of("a" * 900, "b" * 900),
         "freshness": [freshness_row()]}
    )  # fmt: skip
    server = build_server(_api(common, client))
    res = await _call(
        server, "kb_get_document", {"document_id": str(DOC_ROW["document_id"]), "max_chars": 500}
    )
    assert not res.isError
    validate_structured_content(CONTRACT, "kb_get_document", res.structuredContent)
    assert res.structuredContent["status"] == "partial"
    assert res.structuredContent["meta"]["truncated"] is True


@pytest.mark.asyncio
async def test_list_sources_branches_validate_against_contract(
    ro_dsn: str, seeded_ids, common: CommonSettings, admin_dsn: str, pg_database_factory
) -> None:
    server = build_server(real_api(ro_dsn, common))
    ok = await _call(server, "kb_list_sources", {})
    assert not ok.isError
    validate_structured_content(CONTRACT, "kb_list_sources", ok.structuredContent)
    assert ok.structuredContent["status"] == "ok"
    assert ok.structuredContent["meta"]["data_freshness"]["embedding_model"] == "fake/hashed-bow"


@pytest.mark.asyncio
async def test_TC_068_list_sources_empty_branch_validates_against_contract(
    pg_database_factory, migrated_template: str, common: CommonSettings
) -> None:
    dsn = as_user(pg_database_factory(template=migrated_template), "mcp_query_ro")
    res = await _call(build_server(real_api(dsn, common)), "kb_list_sources", {})
    assert not res.isError
    validate_structured_content(CONTRACT, "kb_list_sources", res.structuredContent)
    assert res.structuredContent["status"] == "empty"
    assert res.content[0].text.startswith("Không tìm thấy")


@pytest.mark.asyncio
async def test_error_branches_validate_against_contract(common: CommonSettings) -> None:
    server = build_server(_api(common, FakeClient({"search": [chunk_row()], "freshness": []})))
    short = await _call(server, "kb_semantic_search", {"query": "ab"})
    no_key = await _call(server, "kb_get_document", {})
    bad_uuid = await _call(server, "kb_get_document", {"document_id": "nope"})
    bad_k = await _call(server, "kb_semantic_search", {"query": "abc", "top_k": 99})
    for res, tool in ((no_key, "kb_get_document"), (bad_uuid, "kb_get_document")):
        assert res.isError
        validate_structured_content(CONTRACT, tool, res.structuredContent, is_error=True)
    for res in (short, bad_k):  # rejected by the input schema (minLength / maximum) up front
        assert res.isError
    # `query` shorter than minLength is rejected by the input schema before reaching our code;
    # the missing key is rejected by our own validation with a field name.
    assert no_key.structuredContent["error"]["code"] == "invalid_input"
    assert no_key.structuredContent["error"]["details"]["field"] == "document_id"
    assert bad_uuid.structuredContent["error"]["source"] == "pgvector"


@pytest.mark.asyncio
async def test_database_down_is_a_contract_valid_error_with_a_vpn_hint(
    common: CommonSettings,
) -> None:
    dsn = "postgresql://mcp_query_ro:pw@127.0.0.1:1/none"
    server = build_server(real_api(dsn, common))
    res = await _call(server, "kb_semantic_search", {"query": "payment retry"})
    assert res.isError
    validate_structured_content(
        CONTRACT, "kb_semantic_search", res.structuredContent, is_error=True
    )
    error = res.structuredContent["error"]
    assert error["code"] == "upstream_unavailable" and "VPN" in error["details"]["hint"]
    assert "pw" not in json.dumps(error)


@pytest.mark.asyncio
async def test_unconfigured_server_reports_source_misconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MCP_PGVECTOR_DSN", raising=False)
    res = await _call(build_server(common=CommonSettings()), "kb_list_sources", {})
    assert res.isError
    error = res.structuredContent["error"]
    assert error["code"] == "source_misconfigured"
    assert "MCP_PGVECTOR_DSN" in error["details"]["missing_env"]
