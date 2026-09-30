"""T-011: mcp_common.readonly — allowlist guard + @readonly_tool marker."""

from __future__ import annotations

import pytest
from mcp_common.errors import ErrorCode, NotPermittedError
from mcp_common.readonly import enforce, is_readonly_tool, readonly_tool


def test_enforce_allows_operation_in_allowlist() -> None:
    enforce(["GET /rest/api/content/search", "GET /space"], "GET /space", source="confluence")


def test_enforce_rejects_operation_outside_allowlist_with_operation_name() -> None:
    with pytest.raises(NotPermittedError) as exc_info:
        enforce(["GET /rest/api/content/search"], "POST /rest/api/content", source="confluence")

    error = exc_info.value
    assert error.code == ErrorCode.NOT_PERMITTED
    assert error.retryable is False
    assert error.details["operation"] == "POST /rest/api/content"
    assert "POST /rest/api/content" in str(error)


def test_enforce_rejects_export_view_excluded_from_allowlist() -> None:
    # ADR-0007 A1: body.export_view renders macros server-side — excluded on purpose.
    allowlist = ["GET /rest/api/content/search", "GET /content/{id}"]
    with pytest.raises(NotPermittedError):
        enforce(allowlist, "GET /content/{id}?expand=body.export_view", source="confluence")


@pytest.mark.asyncio
async def test_readonly_tool_marks_async_function_and_preserves_behavior() -> None:
    @readonly_tool
    async def confluence_search_pages(query: str) -> str:
        return f"searched:{query}"

    assert is_readonly_tool(confluence_search_pages) is True
    assert await confluence_search_pages("x") == "searched:x"
    assert confluence_search_pages.__name__ == "confluence_search_pages"


def test_readonly_tool_marks_sync_function_and_preserves_behavior() -> None:
    @readonly_tool
    def redis_get_key(key: str) -> str:
        return f"value-of:{key}"

    assert is_readonly_tool(redis_get_key) is True
    assert redis_get_key("k") == "value-of:k"
    assert redis_get_key.__name__ == "redis_get_key"


def test_is_readonly_tool_false_for_undecorated_function() -> None:
    def plain_function() -> None:
        return None

    assert is_readonly_tool(plain_function) is False
