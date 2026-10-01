"""The seven CloudWatch tools (FR-006). Names, input schemas and outputs are dictated by
`api-contract.yaml`; `tools.snapshot.json` + `tests/test_contract.py` keep them in sync.
"""

from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp_common.config import CommonSettings
from mcp_common.readonly import readonly_tool
from mcp_common.tooling import (
    CursorParam,
    LimitParam,
    MaxBytesParam,
    ToolOutcome,
    register_tool,
)
from pydantic import BaseModel, ConfigDict, Field

from mcp_cloudwatch.client import SOURCE
from mcp_cloudwatch.read_api import CloudWatchReadApi

__all__ = ["TOOL_DESCRIPTIONS", "Dimension", "make_tools", "register_tools"]

TOOL_DESCRIPTIONS: dict[str, str] = {
    "cloudwatch_list_log_groups": (
        "Liệt kê log group theo tiền tố. Dùng để phân giải tên log group thật trước khi lọc log."
    ),
    "cloudwatch_filter_log_events": (
        "Lọc log event trong một log group theo pattern và khoảng thời gian bắt buộc. "
        "Log group không tồn tại trả not_found; không có event trả status=empty."
    ),
    "cloudwatch_run_logs_insights": (
        "Chạy truy vấn Logs Insights chỉ đọc (fields, filter, stats, sort, limit, parse, dedup, "
        "display) và trả các hàng kết quả. Hết timeout_s thì dừng query và trả status=partial."
    ),
    "cloudwatch_list_metrics": (
        "Liệt kê metric đang tồn tại (namespace, tên, dimension). "
        "Dùng để chọn đúng namespace và dimension trước khi lấy datapoint."
    ),
    "cloudwatch_get_metric_data": (
        "Lấy chuỗi datapoint của một metric trong khoảng thời gian. "
        "Metric không tồn tại trả not_found; tồn tại nhưng không có datapoint trả status=empty."
    ),
    "cloudwatch_describe_alarms": (
        "Liệt kê alarm và trạng thái hiện tại theo tiền tố hoặc state. "
        "Không có alarm nào khớp trả status=empty để nêu rõ khoảng trống."
    ),
    "cloudwatch_describe_alarm_history": (
        "Lịch sử chuyển trạng thái của một alarm trong khoảng thời gian, để dựng timeline. "
        "Alarm không tồn tại trả not_found."
    ),
}


class Dimension(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(max_length=255)]
    value: Annotated[str, Field(max_length=1024)]


Dimensions = Annotated[list[Dimension], Field(max_length=10)]
TimeoutParam = Annotated[
    int, Field(ge=1, le=22, description="Deadline nội bộ (giây); phải nhỏ hơn deadline của tool.")
]

ApiFactory = Callable[[], CloudWatchReadApi]


def _dims(dimensions: list[Dimension]) -> list[dict[str, str]]:
    return [d.model_dump() for d in dimensions]


def make_tools(api: ApiFactory) -> dict[str, Callable[..., Any]]:
    @readonly_tool
    async def cloudwatch_list_log_groups(
        name_prefix: Annotated[str | None, Field(max_length=512)] = None,
        limit: LimitParam = 20,
        cursor: CursorParam = None,
    ) -> ToolOutcome:
        return await api().list_log_groups(name_prefix=name_prefix, limit=limit, cursor=cursor)

    @readonly_tool
    async def cloudwatch_filter_log_events(
        log_group_name: Annotated[str, Field(min_length=1, max_length=512)],
        time_from: datetime,
        time_to: datetime,
        filter_pattern: Annotated[
            str | None,
            Field(
                max_length=1024, description="CloudWatch filter pattern, ví dụ `?ERROR ?Exception`."
            ),
        ] = None,
        log_stream_name_prefix: str | None = None,
        limit: LimitParam = 20,
        cursor: CursorParam = None,
        max_bytes: MaxBytesParam = 65536,
    ) -> ToolOutcome:
        return await api().filter_log_events(
            log_group_name=log_group_name, time_from=time_from, time_to=time_to,
            filter_pattern=filter_pattern, log_stream_name_prefix=log_stream_name_prefix,
            limit=limit, cursor=cursor, max_bytes=max_bytes,
        )  # fmt: skip

    @readonly_tool
    async def cloudwatch_run_logs_insights(
        log_group_names: Annotated[
            list[Annotated[str, Field(max_length=512)]], Field(min_length=1, max_length=20)
        ],
        query: Annotated[str, Field(min_length=1, max_length=4096)],
        time_from: datetime,
        time_to: datetime,
        limit: LimitParam = 20,
        timeout_s: TimeoutParam = 20,
    ) -> ToolOutcome:
        return await api().run_logs_insights(
            log_group_names=list(log_group_names), query=query, time_from=time_from,
            time_to=time_to, limit=limit, timeout_s=timeout_s,
        )  # fmt: skip

    @readonly_tool
    async def cloudwatch_list_metrics(
        namespace: Annotated[str | None, Field(max_length=256)] = None,
        metric_name_prefix: Annotated[str | None, Field(max_length=256)] = None,
        dimensions: Dimensions = [],  # noqa: B006 (pydantic copies defaults)
        limit: LimitParam = 20,
        cursor: CursorParam = None,
    ) -> ToolOutcome:
        return await api().list_metrics(
            namespace=namespace, metric_name_prefix=metric_name_prefix,
            dimensions=_dims(dimensions), limit=limit, cursor=cursor,
        )  # fmt: skip

    @readonly_tool
    async def cloudwatch_get_metric_data(
        namespace: Annotated[str, Field(min_length=1, max_length=256)],
        metric_name: Annotated[str, Field(min_length=1, max_length=256)],
        time_from: datetime,
        time_to: datetime,
        dimensions: Dimensions = [],  # noqa: B006
        stat: Literal[
            "Average", "Sum", "Minimum", "Maximum", "SampleCount", "p50", "p90", "p95", "p99"
        ] = "Average",
        period_s: Literal[60, 300, 900, 3600, 21600, 86400] = 300,
    ) -> ToolOutcome:
        return await api().get_metric_data(
            namespace=namespace, metric_name=metric_name, time_from=time_from, time_to=time_to,
            dimensions=_dims(dimensions), stat=stat, period_s=period_s,
        )  # fmt: skip

    @readonly_tool
    async def cloudwatch_describe_alarms(
        alarm_name_prefix: Annotated[str | None, Field(max_length=256)] = None,
        state_value: Literal["OK", "ALARM", "INSUFFICIENT_DATA"] | None = None,
        limit: LimitParam = 20,
        cursor: CursorParam = None,
    ) -> ToolOutcome:
        return await api().describe_alarms(
            alarm_name_prefix=alarm_name_prefix, state_value=state_value, limit=limit,
            cursor=cursor,
        )  # fmt: skip

    @readonly_tool
    async def cloudwatch_describe_alarm_history(
        alarm_name: Annotated[str, Field(min_length=1, max_length=256)],
        time_from: datetime,
        time_to: datetime,
        history_item_type: Literal["ConfigurationUpdate", "StateUpdate", "Action"]
        | None = "StateUpdate",  # fmt: skip
        limit: LimitParam = 20,
        cursor: CursorParam = None,
    ) -> ToolOutcome:
        return await api().describe_alarm_history(
            alarm_name=alarm_name, time_from=time_from, time_to=time_to,
            history_item_type=history_item_type, limit=limit, cursor=cursor,
        )  # fmt: skip

    return {
        fn.__name__: fn
        for fn in (
            cloudwatch_list_log_groups,
            cloudwatch_filter_log_events,
            cloudwatch_run_logs_insights,
            cloudwatch_list_metrics,
            cloudwatch_get_metric_data,
            cloudwatch_describe_alarms,
            cloudwatch_describe_alarm_history,
        )
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
