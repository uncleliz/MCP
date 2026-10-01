"""The three Kibana tools (FR-005). Names, input schemas and outputs are dictated by
`api-contract.yaml`; `tools.snapshot.json` + `tests/test_contract.py` keep them in sync.
"""

from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp_common.config import CommonSettings
from mcp_common.readonly import readonly_tool
from mcp_common.tooling import CursorParam, LimitParam, ToolOutcome, register_tool
from pydantic import Field

from mcp_kibana.client import SOURCE
from mcp_kibana.read_api import KibanaReadApi

__all__ = ["TOOL_DESCRIPTIONS", "make_tools", "register_tools"]

TOOL_DESCRIPTIONS: dict[str, str] = {
    "kibana_find_saved_objects": (
        "Tìm dashboard, visualization, lens hoặc saved search theo từ khoá. "
        "Kết quả có link Kibana; không khớp trả status=empty."
    ),
    "kibana_get_saved_object": (
        "Chi tiết một saved object: panel và các object/index pattern liên quan. "
        "Dùng để chọn đúng index cho truy vấn OpenSearch; id không tồn tại trả not_found."
    ),
    "kibana_build_dashboard_link": (
        "Dựng deep link tới dashboard với đúng khung thời gian (và query tuỳ chọn). "
        "Chỉ xác minh dashboard tồn tại bằng một GET; không tồn tại trả not_found."
    ),
}

ObjectType = Literal["dashboard", "visualization", "lens", "search", "index-pattern"]
Space = Annotated[str | None, Field(description="Space id; null = space mặc định.")]

ApiFactory = Callable[[], KibanaReadApi]


def make_tools(api: ApiFactory) -> dict[str, Callable[..., Any]]:
    @readonly_tool
    async def kibana_find_saved_objects(
        query: Annotated[str, Field(min_length=1, max_length=256)],
        types: Annotated[list[ObjectType], Field(min_length=1, max_length=5)] = ["dashboard"],  # noqa: B006
        space: Space = None,
        limit: LimitParam = 20,
        cursor: CursorParam = None,
    ) -> ToolOutcome:
        return await api().find_saved_objects(
            query=query, types=list(types), space=space, limit=limit, cursor=cursor
        )

    @readonly_tool
    async def kibana_get_saved_object(
        type: ObjectType,  # noqa: A002 (contract field name)
        id: Annotated[str, Field(min_length=1, max_length=128)],  # noqa: A002
        space: Space = None,
    ) -> ToolOutcome:
        return await api().get_saved_object(type=type, id=id, space=space)

    @readonly_tool
    async def kibana_build_dashboard_link(
        dashboard_id: Annotated[str, Field(min_length=1, max_length=128)],
        time_from: datetime,
        time_to: datetime,
        space: Space = None,
        query: Annotated[
            str | None, Field(description="Query bar KQL/Lucene chèn vào `_a`.")
        ] = None,
    ) -> ToolOutcome:
        return await api().build_dashboard_link(
            dashboard_id=dashboard_id,
            time_from=time_from,
            time_to=time_to,
            space=space,
            query=query,
        )

    return {
        fn.__name__: fn
        for fn in (kibana_find_saved_objects, kibana_get_saved_object, kibana_build_dashboard_link)
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
