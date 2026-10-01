"""The six OpenSearch tools (FR-004). Names, input schemas and outputs are dictated by
`api-contract.yaml`; `tools.snapshot.json` + `tests/test_contract.py` keep them in sync.

`opensearch_search_dsl` is an escape hatch and is only registered when
`MCP_OPENSEARCH_ALLOW_DSL=true` (contract `x-feature-flag`); the other five always are.
"""

from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp_common.config import CommonSettings
from mcp_common.readonly import readonly_tool
from mcp_common.tooling import CursorParam, LimitParam, ToolOutcome, register_tool
from pydantic import Field

from mcp_opensearch.client import SOURCE
from mcp_opensearch.read_api import OpenSearchReadApi

__all__ = ["DSL_TOOL", "TOOL_DESCRIPTIONS", "make_tools", "register_tools"]

DSL_TOOL = "opensearch_search_dsl"

TOOL_DESCRIPTIONS: dict[str, str] = {
    "opensearch_list_indices": (
        "Liệt kê index khớp pattern (mặc định bỏ index hệ thống). "
        "Gọi trước khi search để không phải đoán tên index."
    ),
    "opensearch_get_mapping": (
        "Lấy mapping dạng phẳng field -> type của index. "
        "Dùng để viết query đúng field; index không tồn tại trả not_found."
    ),
    "opensearch_search_logs": (
        "Tìm log bằng query_string với khoảng thời gian bắt buộc, phân trang bằng cursor. "
        "Mỗi kết quả trích dẫn index, _id và timestamp; không có hit trả status=empty."
    ),
    "opensearch_count": (
        "Đếm số document khớp query trong khoảng thời gian, để ước lượng quy mô trước khi lấy mẫu. "
        "Đếm 0 trả status=empty."
    ),
    "opensearch_aggregate": (
        "Aggregation giới hạn: terms (top giá trị một field) hoặc date_histogram "
        "(phân bố theo thời gian). Không nhận aggregation tuỳ ý."
    ),
    DSL_TOOL: (
        "Escape hatch chạy query DSL trên _search, chỉ khi người vận hành bật. "
        "Chặn script, runtime_mappings, scroll, point_in_time và terms-lookup ở mọi độ sâu."
    ),
}

TimeoutParam = Annotated[
    int, Field(ge=1, le=22, description="Deadline nội bộ (giây); phải nhỏ hơn deadline của tool.")
]
IndexPattern = Annotated[str, Field(min_length=1, max_length=256)]
QueryString = Annotated[str, Field(min_length=1, max_length=2048)]

ApiFactory = Callable[[], OpenSearchReadApi]


def make_tools(api: ApiFactory, *, include_dsl: bool = False) -> dict[str, Callable[..., Any]]:
    @readonly_tool
    async def opensearch_list_indices(
        pattern: Annotated[str, Field(max_length=256)] = "*",
        include_system: bool = False,
        limit: LimitParam = 20,
    ) -> ToolOutcome:
        return await api().list_indices(pattern=pattern, include_system=include_system, limit=limit)

    @readonly_tool
    async def opensearch_get_mapping(
        index: IndexPattern,
        field_filter: Annotated[
            str | None, Field(description="Lọc theo tiền tố tên field.")
        ] = None,
    ) -> ToolOutcome:
        return await api().get_mapping(index=index, field_filter=field_filter)

    @readonly_tool
    async def opensearch_search_logs(
        index_pattern: IndexPattern,
        query: QueryString,
        time_from: datetime,
        time_to: datetime,
        time_field: str = "@timestamp",
        fields: Annotated[list[str], Field(max_length=30)] = [],  # noqa: B006
        sort_order: Literal["asc", "desc"] = "desc",
        highlight: bool = False,
        limit: LimitParam = 20,
        cursor: CursorParam = None,
        timeout_s: TimeoutParam = 20,
    ) -> ToolOutcome:
        return await api().search_logs(
            index_pattern=index_pattern, query=query, time_from=time_from, time_to=time_to,
            time_field=time_field, fields=list(fields), sort_order=sort_order,
            highlight=highlight, limit=limit, cursor=cursor, timeout_s=timeout_s,
        )  # fmt: skip

    @readonly_tool
    async def opensearch_count(
        index_pattern: IndexPattern,
        query: QueryString,
        time_from: datetime,
        time_to: datetime,
        time_field: str = "@timestamp",
        timeout_s: TimeoutParam = 20,
    ) -> ToolOutcome:
        return await api().count(
            index_pattern=index_pattern, query=query, time_from=time_from, time_to=time_to,
            time_field=time_field, timeout_s=timeout_s,
        )  # fmt: skip

    @readonly_tool
    async def opensearch_aggregate(
        index_pattern: IndexPattern,
        agg_type: Literal["terms", "date_histogram"],
        field: Annotated[str, Field(min_length=1, max_length=256)],
        time_from: datetime,
        time_to: datetime,
        query: Annotated[str, Field(max_length=2048)] = "*",
        time_field: str = "@timestamp",
        interval: Annotated[
            Literal["1m", "5m", "15m", "1h", "1d"] | None,
            Field(description="Bắt buộc khi agg_type=date_histogram."),
        ] = None,
        size: Annotated[int, Field(ge=1, le=100, description="Chỉ dùng với terms.")] = 10,
        timeout_s: TimeoutParam = 20,
    ) -> ToolOutcome:
        return await api().aggregate(
            index_pattern=index_pattern, agg_type=agg_type, field=field, time_from=time_from,
            time_to=time_to, query=query, time_field=time_field, interval=interval, size=size,
            timeout_s=timeout_s,
        )  # fmt: skip

    @readonly_tool
    async def opensearch_search_dsl(
        index_pattern: IndexPattern,
        body: dict[str, Any],
        limit: LimitParam = 20,
        timeout_s: TimeoutParam = 20,
    ) -> ToolOutcome:
        return await api().search_dsl(
            index_pattern=index_pattern, body=body, limit=limit, timeout_s=timeout_s
        )

    tools: dict[str, Callable[..., Any]] = {
        fn.__name__: fn
        for fn in (
            opensearch_list_indices,
            opensearch_get_mapping,
            opensearch_search_logs,
            opensearch_count,
            opensearch_aggregate,
        )
    }
    if include_dsl:
        tools[DSL_TOOL] = opensearch_search_dsl
    return tools


def register_tools(
    mcp: FastMCP,
    api: ApiFactory,
    common: CommonSettings | None = None,
    *,
    include_dsl: bool = False,
) -> None:
    for name, fn in make_tools(api, include_dsl=include_dsl).items():
        register_tool(
            mcp,
            fn,
            name=name,
            description=TOOL_DESCRIPTIONS[name],
            source=SOURCE,
            common_settings=common,
        )
