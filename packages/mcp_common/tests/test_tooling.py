"""Phase-1 shared tool plumbing (mcp_common.tooling): cursors, result builders,
byte budget, safe text, and the registration wrapper that turns a tool coroutine into a
`CallToolResult` with structuredContent + rendered text (contract `x-text-rendering`).
"""

from __future__ import annotations

import asyncio
import base64

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session
from mcp_common.config import CommonSettings
from mcp_common.envelope import Citation, SourceType
from mcp_common.errors import ErrorCode, ToolError
from mcp_common.readonly import is_readonly_tool, readonly_tool
from mcp_common.tooling import (
    RedactionCounter,
    TextBudget,
    ToolOutcome,
    build_result,
    decode_cursor,
    effective_max_bytes,
    encode_cursor,
    not_found_result,
    parse_aware_datetime,
    register_tool,
    registered_tool_functions,
    safe_text,
)


def _cite(label: str = "Doc") -> Citation:
    return Citation(source_type=SourceType.CONFLUENCE, label=label, uri="https://x.test/d")


def test_cursor_roundtrip() -> None:
    cursor = encode_cursor({"start": 40})
    assert decode_cursor(cursor, source="confluence") == {"start": 40}
    assert decode_cursor(None, source="confluence") == {}


@pytest.mark.parametrize("bad", ["!!!not-base64!!!", base64.urlsafe_b64encode(b"[1]").decode()])
def test_bad_cursor_is_invalid_input(bad: str) -> None:
    with pytest.raises(ToolError) as exc:
        decode_cursor(bad, source="confluence")
    assert exc.value.code == ErrorCode.INVALID_INPUT
    assert exc.value.details["field"] == "cursor"


def test_build_result_empty_has_no_citations() -> None:
    result = build_result(SourceType.CONFLUENCE, [], [], started=0.0, query_echo={"query": "zzz"})
    assert result.status.value == "empty"
    assert result.items == [] and result.citations == []
    assert result.meta.query_echo == {"query": "zzz"}


def test_build_result_ok_and_partial_when_truncated() -> None:
    ok = build_result(
        SourceType.CONFLUENCE, [{"citation_ref": 0}], [_cite()], started=0.0, query_echo={}
    )
    assert ok.status.value == "ok" and ok.meta.returned == 1
    partial = build_result(
        SourceType.CONFLUENCE,
        [{"citation_ref": 0}],
        [_cite()],
        started=0.0,
        query_echo={},
        truncated=True,
    )
    assert partial.status.value == "partial" and partial.meta.truncated


def test_build_result_has_more_needs_cursor() -> None:
    result = build_result(
        SourceType.CONFLUENCE,
        [{"citation_ref": 0}],
        [_cite()],
        started=0.0,
        query_echo={},
        next_cursor="abc",
    )
    assert result.meta.has_more is True and result.meta.next_cursor == "abc"


def test_not_found_result() -> None:
    result = not_found_result(SourceType.GITLAB, started=0.0, query_echo={"project": "a/b"})
    assert result.status.value == "not_found"
    assert result.meta.warnings == ["identifier does not exist at source"]


def test_effective_max_bytes_clamps_and_warns() -> None:
    common = CommonSettings(max_output_bytes=2048)
    effective, warnings = effective_max_bytes(65536, common)
    assert effective == 2048 and len(warnings) == 1 and "2048" in warnings[0]
    effective, warnings = effective_max_bytes(1024, common)
    assert effective == 1024 and warnings == []


def test_text_budget_truncates_and_tracks() -> None:
    budget = TextBudget(10)
    assert budget.take("hello") == ("hello", False)
    text, truncated = budget.take("world!!!")
    assert truncated and len(text.encode()) <= 5
    assert budget.exhausted
    assert budget.take("more") == ("", True)
    assert budget.truncated


def test_safe_text_scrubs_wraps_and_counts() -> None:
    counter = RedactionCounter()
    out = safe_text(
        "deploy key glpat-abcdefghijklmnopqrstuvwxyz0123",
        source="gitlab",
        content_id="1",
        counter=counter,
        disabled=False,
    )
    assert "glpat-" not in out
    assert out.startswith('<untrusted-content source="gitlab" id="1">')
    assert counter.count == 1


def test_safe_text_none_passthrough() -> None:
    assert (
        safe_text(None, source="gitlab", content_id="1", counter=RedactionCounter(), disabled=False)
        is None
    )


def test_parse_aware_datetime() -> None:
    assert parse_aware_datetime("2026-01-01T00:00:00Z", field="updated_after").year == 2026
    for bad in ("2026-01-01T00:00:00", "garbage"):
        with pytest.raises(ToolError) as exc:
            parse_aware_datetime(bad, field="updated_after")
        assert exc.value.code == ErrorCode.INVALID_INPUT
        assert exc.value.details["field"] == "updated_after"


def _server(fn, name: str = "demo_tool") -> FastMCP:
    mcp = FastMCP("demo")
    register_tool(
        mcp,
        fn,
        name=name,
        description="demo",
        source="confluence",
        common_settings=CommonSettings(),
    )
    return mcp


async def _call(mcp: FastMCP, name: str, args: dict):
    async with create_connected_server_and_client_session(mcp) as session:
        return await session.call_tool(name, arguments=args)


@pytest.mark.asyncio
async def test_register_tool_ok_returns_structured_and_text() -> None:
    @readonly_tool
    async def demo_tool(query: str) -> ToolOutcome:
        result = build_result(
            SourceType.CONFLUENCE,
            [{"citation_ref": 0, "title": "T"}],
            [_cite("T")],
            started=0.0,
            query_echo={"query": query},
        )
        return ToolOutcome(result)

    mcp = _server(demo_tool)
    assert is_readonly_tool(registered_tool_functions(mcp)["demo_tool"])
    res = await _call(mcp, "demo_tool", {"query": "q"})
    assert not res.isError
    assert res.structuredContent["status"] == "ok"
    assert "Nguồn:" in res.content[0].text


@pytest.mark.asyncio
async def test_register_tool_empty_uses_query_description() -> None:
    async def demo_tool(query: str) -> ToolOutcome:
        result = build_result(SourceType.CONFLUENCE, [], [], started=0.0, query_echo={})
        return ToolOutcome(result, query_description="tài liệu Confluence cho 'q'")

    res = await _call(_server(demo_tool), "demo_tool", {"query": "q"})
    assert res.structuredContent["status"] == "empty"
    assert "Không tìm thấy tài liệu Confluence cho 'q' ở Confluence." in res.content[0].text


@pytest.mark.asyncio
async def test_register_tool_maps_tool_error_to_error_envelope() -> None:
    async def demo_tool(query: str) -> ToolOutcome:
        raise ToolError(
            ErrorCode.UPSTREAM_TIMEOUT, "boom", "confluence", True, details={"hint": "VPN?"}
        )

    res = await _call(_server(demo_tool), "demo_tool", {"query": "q"})
    assert res.isError
    assert res.structuredContent["error"]["code"] == "upstream_timeout"
    assert "VPN?" in res.content[0].text


@pytest.mark.asyncio
async def test_register_tool_unexpected_exception_is_internal() -> None:
    async def demo_tool(query: str) -> ToolOutcome:
        raise RuntimeError("secret stack detail")

    res = await _call(_server(demo_tool), "demo_tool", {"query": "q"})
    assert res.isError
    assert res.structuredContent["error"]["code"] == "internal"
    assert "secret stack detail" not in res.content[0].text


@pytest.mark.asyncio
async def test_R_003_register_tool_error_envelope_scrubs_secret_in_message() -> None:
    """End-to-end: a `ToolError` whose message embeds a raw upstream secret (as
    `_NON_HTTPX_SDK_RULES` builds for psycopg/redis/boto3) must come back scrubbed in
    both the rendered text and `structuredContent` — the envelope is the only place
    the tool boundary can still enforce the redaction guarantee for error paths."""

    async def demo_tool(query: str) -> ToolOutcome:
        raise ToolError(
            ErrorCode.UPSTREAM_UNAVAILABLE,
            "Postgres operational error: connection to server failed: FATAL: password "
            "authentication failed for user 'ingest' (token=glpat-abcdefghijklmnopqrst)",
            "pgvector",
            True,
        )

    res = await _call(_server(demo_tool), "demo_tool", {"query": "q"})
    assert res.isError
    assert "glpat-abcdefghijklmnopqrst" not in res.structuredContent["error"]["message"]
    assert "glpat-abcdefghijklmnopqrst" not in res.content[0].text
    assert "«redacted:" in res.structuredContent["error"]["message"]


@pytest.mark.asyncio
async def test_register_tool_deadline_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_TOOL_DEADLINE_DEMO_TOOL", "0.05")

    async def demo_tool(query: str) -> ToolOutcome:
        await asyncio.sleep(5)
        raise AssertionError("unreachable")

    res = await _call(_server(demo_tool), "demo_tool", {"query": "q"})
    assert res.isError
    assert res.structuredContent["error"]["code"] == "upstream_timeout"
    assert "VPN" in res.structuredContent["error"]["details"]["hint"]
