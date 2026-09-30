"""T-012: mcp_common.runtime — with_tool_deadline (per-call deadline + envelope errors)."""

from __future__ import annotations

import asyncio

import pytest
from mcp_common.config import CommonSettings
from mcp_common.errors import ErrorCode, ToolError
from mcp_common.runtime import tool_deadline_for, with_tool_deadline


def test_tool_deadline_for_falls_back_to_common_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MCP_TOOL_DEADLINE_MY_TOOL", raising=False)
    settings = CommonSettings()
    assert tool_deadline_for("my_tool", settings) == settings.tool_deadline


def test_tool_deadline_for_honors_per_tool_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_TOOL_DEADLINE_CLOUDWATCH_RUN_LOGS_INSIGHTS", "60")
    assert tool_deadline_for("cloudwatch_run_logs_insights") == 60.0


@pytest.mark.asyncio
async def test_successful_call_passes_through_unchanged() -> None:
    @with_tool_deadline("confluence_search_pages", source="confluence")
    async def tool(query: str) -> dict:
        return {"status": "ok", "query": query}

    result = await tool("x")
    assert result == {"status": "ok", "query": "x"}


@pytest.mark.asyncio
async def test_timeout_is_converted_to_upstream_timeout_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_TOOL_DEADLINE_SLOW_TOOL", "0.05")

    @with_tool_deadline("slow_tool", source="confluence")
    async def tool() -> dict:
        await asyncio.sleep(10)
        return {"status": "ok"}  # pragma: no cover - never reached

    envelope = await tool()

    assert envelope["status"] == "error"
    assert envelope["error"]["code"] == ErrorCode.UPSTREAM_TIMEOUT.value
    assert envelope["error"]["retryable"] is True


@pytest.mark.asyncio
async def test_tool_error_is_converted_to_its_own_envelope() -> None:
    @with_tool_deadline("gitlab_get_file", source="gitlab")
    async def tool() -> dict:
        raise ToolError(ErrorCode.NOT_PERMITTED, "denied", "gitlab", False, details={"x": 1})

    envelope = await tool()

    assert envelope["status"] == "error"
    assert envelope["error"]["code"] == "not_permitted"
    assert envelope["error"]["details"] == {"x": 1}


@pytest.mark.asyncio
async def test_unexpected_exception_is_mapped_to_internal_without_leaking_message() -> None:
    @with_tool_deadline("redis_get_key", source="redis")
    async def tool() -> dict:
        raise ValueError("leaking secret sql SELECT * FROM users")

    envelope = await tool()

    assert envelope["status"] == "error"
    assert envelope["error"]["code"] == "internal"
    assert "leaking secret sql" not in envelope["error"]["message"]


@pytest.mark.asyncio
async def test_function_metadata_is_preserved() -> None:
    @with_tool_deadline("confluence_search_pages", source="confluence")
    async def confluence_search_pages(query: str) -> dict:
        return {"status": "ok"}

    assert confluence_search_pages.__name__ == "confluence_search_pages"
