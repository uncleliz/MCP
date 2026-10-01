"""The four Confluence tools (FR-001). Names, input schemas and outputs are dictated by
`api-contract.yaml`; `tools.snapshot.json` + `tests/test_contract.py` keep them in sync.

Each function only validates/forwards to `ConfluenceReadApi` and returns a `ToolOutcome`;
deadline, error envelope and text rendering are applied by `register_tool`.
"""

from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from mcp_common.config import CommonSettings
from mcp_common.readonly import readonly_tool
from mcp_common.tooling import CursorParam, LimitParam, ToolOutcome, register_tool
from pydantic import Field

from mcp_confluence.client import SOURCE
from mcp_confluence.read_api import ConfluenceReadApi

__all__ = ["TOOL_DESCRIPTIONS", "make_tools", "register_tools"]

TOOL_DESCRIPTIONS: dict[str, str] = {
    "confluence_search_pages": (
        "Tìm page Confluence theo từ khoá, lọc tuỳ chọn theo space, label và thời điểm cập nhật. "
        "Trả về page kèm excerpt và URL dùng làm citation. "
        "status=empty khi không có page nào khớp."
    ),
    "confluence_get_page": (
        "Lấy nội dung đầy đủ của một page theo id, chuẩn hoá sang markdown hoặc text. "
        "Nội dung bị cắt theo max_chars và được bọc trong untrusted-content."
    ),
    "confluence_list_spaces": "Liệt kê các space Confluence, lọc tuỳ chọn theo key hoặc tên.",
    "confluence_list_page_children": "Liệt kê các page con trực tiếp của một page.",
}

PageId = Annotated[str, Field(pattern=r"^[0-9A-Za-z._-]{1,64}$", description="Id của page.")]

ApiFactory = Callable[[], ConfluenceReadApi]


def make_tools(api: ApiFactory) -> dict[str, Callable[..., Any]]:
    @readonly_tool
    async def confluence_search_pages(
        query: Annotated[
            str,
            Field(min_length=1, max_length=512, description="Từ khoá (không phải CQL thô)."),
        ],
        space_key: Annotated[str | None, Field(description="Giới hạn trong một space.")] = None,
        updated_after: Annotated[
            datetime | None, Field(description="Chỉ page cập nhật sau mốc này (ISO-8601 có tz).")
        ] = None,
        labels: Annotated[list[str], Field(max_length=10)] = [],  # noqa: B006 (pydantic copies)
        include_excerpt: bool = True,
        limit: LimitParam = 20,
        cursor: CursorParam = None,
    ) -> ToolOutcome:
        return await api().search_pages(
            query=query,
            space_key=space_key,
            updated_after=updated_after,
            labels=list(labels),
            include_excerpt=include_excerpt,
            limit=limit,
            cursor=cursor,
        )

    @readonly_tool
    async def confluence_get_page(
        page_id: PageId,
        body_format: Annotated[
            str, Field(json_schema_extra={"enum": ["markdown", "text"]})
        ] = "markdown",
        max_chars: Annotated[int, Field(ge=500, le=100000)] = 20000,
    ) -> ToolOutcome:
        return await api().get_page(page_id=page_id, body_format=body_format, max_chars=max_chars)

    @readonly_tool
    async def confluence_list_spaces(
        query: Annotated[
            str | None, Field(max_length=128, description="Lọc theo key/name.")
        ] = None,
        limit: LimitParam = 20,
        cursor: CursorParam = None,
    ) -> ToolOutcome:
        return await api().list_spaces(query=query, limit=limit, cursor=cursor)

    @readonly_tool
    async def confluence_list_page_children(
        page_id: PageId,
        limit: LimitParam = 20,
        cursor: CursorParam = None,
    ) -> ToolOutcome:
        return await api().list_page_children(page_id=page_id, limit=limit, cursor=cursor)

    return {
        "confluence_search_pages": confluence_search_pages,
        "confluence_get_page": confluence_get_page,
        "confluence_list_spaces": confluence_list_spaces,
        "confluence_list_page_children": confluence_list_page_children,
    }


def register_tools(mcp: FastMCP, api: ApiFactory, common: CommonSettings | None = None) -> None:
    for name, fn in make_tools(api).items():
        register_tool(
            mcp,
            fn,
            name=name,
            description=TOOL_DESCRIPTIONS[name],
            source=SOURCE,
            common_settings=common,
        )
