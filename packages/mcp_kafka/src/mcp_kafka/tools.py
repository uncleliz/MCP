"""The five Kafka tools (FR-007). Names, input schemas and outputs are dictated by
`api-contract.yaml`; `tools.snapshot.json` + `tests/test_contract.py` keep them in sync.

`kafka_peek_messages` takes a contract field called `from`, a Python keyword, so the parameter
is `from_` with the pydantic alias `from`.
"""

import functools
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp_common.config import CommonSettings
from mcp_common.readonly import readonly_tool
from mcp_common.tooling import LimitParam, MaxBytesParam, ToolOutcome, register_tool
from pydantic import Field

from mcp_kafka.client import SOURCE
from mcp_kafka.read_api import KafkaReadApi

__all__ = ["TOOL_DESCRIPTIONS", "make_tools", "register_tools"]

TOOL_DESCRIPTIONS: dict[str, str] = {
    "kafka_list_topics": "Liệt kê topic khớp pattern glob (mặc định bỏ topic nội bộ).",
    "kafka_describe_topic": (
        "Metadata chi tiết một topic: partition, replica, config, watermark và số message xấp xỉ. "
        "Topic không tồn tại trả not_found và không bao giờ tạo topic."
    ),
    "kafka_peek_messages": (
        "Đọc mẫu message mà không commit offset và không tham gia consumer group. "
        "Value đã redaction và bọc untrusted-content; vượt max_bytes trả status=partial."
    ),
    "kafka_list_consumer_groups": "Liệt kê consumer group khớp pattern, lọc tuỳ chọn theo state.",
    "kafka_describe_consumer_group": (
        "Chi tiết consumer group kèm lag theo từng partition (committed offset so với high "
        "watermark). Group không tồn tại trả not_found."
    ),
}

TopicName = Annotated[str, Field(min_length=1, max_length=249)]
GroupState = Literal[
    "PreparingRebalance", "CompletingRebalance", "Stable", "Dead", "Empty", "Unknown"
]

ApiFactory = Callable[[], KafkaReadApi]


def _accept_from_alias(
    fn: Callable[..., Awaitable[ToolOutcome]],
) -> Callable[..., Awaitable[ToolOutcome]]:
    """FastMCP hands the validated argument back under its alias (`from`), which is not a valid
    Python parameter name; rename it to `from_` before calling the tool function. The wrapped
    signature (and so the published `inputSchema`) is unchanged."""

    @functools.wraps(fn)
    async def shim(*args: Any, **kwargs: Any) -> ToolOutcome:
        if "from" in kwargs:
            kwargs["from_"] = kwargs.pop("from")
        return await fn(*args, **kwargs)

    return shim


def make_tools(api: ApiFactory) -> dict[str, Callable[..., Any]]:
    @readonly_tool
    async def kafka_list_topics(
        pattern: Annotated[str, Field(max_length=256, description="Glob trên tên topic.")] = "*",
        include_internal: bool = False,
        limit: LimitParam = 20,
    ) -> ToolOutcome:
        return await api().list_topics(
            pattern=pattern, include_internal=include_internal, limit=limit
        )

    @readonly_tool
    async def kafka_describe_topic(topic: TopicName, include_configs: bool = True) -> ToolOutcome:
        return await api().describe_topic(topic=topic, include_configs=include_configs)

    @_accept_from_alias
    @readonly_tool
    async def kafka_peek_messages(
        topic: TopicName,
        partition: Annotated[
            int | None, Field(ge=0, description="null = lấy đều từ mọi partition.")
        ] = None,
        from_: Annotated[
            Literal["latest", "earliest", "offset", "timestamp"],
            Field(
                alias="from",
                description="`latest` = n message cuối; `offset`/`timestamp` cần field tương ứng.",
            ),
        ] = "latest",
        offset: Annotated[
            str | None,
            Field(pattern=r"^[0-9]{1,19}$", description="Offset dạng string (có thể vượt 2^53)."),
        ] = None,
        timestamp: datetime | None = None,
        limit: Annotated[int, Field(ge=1, le=100)] = 10,
        value_format: Literal["auto", "json", "utf8", "base64"] = "auto",
        max_bytes: MaxBytesParam = 65536,
        timeout_s: Annotated[int, Field(ge=1, le=22)] = 20,
    ) -> ToolOutcome:
        return await api().peek_messages(
            topic=topic, partition=partition, from_=from_, offset=offset, timestamp=timestamp,
            limit=limit, value_format=value_format, max_bytes=max_bytes, timeout_s=timeout_s,
        )  # fmt: skip

    @readonly_tool
    async def kafka_list_consumer_groups(
        pattern: Annotated[str, Field(max_length=256)] = "*",
        states: Annotated[list[GroupState], Field(max_length=6)] = [],  # noqa: B006
        limit: LimitParam = 20,
    ) -> ToolOutcome:
        return await api().list_consumer_groups(pattern=pattern, states=list(states), limit=limit)

    @readonly_tool
    async def kafka_describe_consumer_group(
        group_id: Annotated[str, Field(min_length=1, max_length=256)],
        include_members: bool = True,
    ) -> ToolOutcome:
        return await api().describe_consumer_group(
            group_id=group_id, include_members=include_members
        )

    return {
        fn.__name__: fn
        for fn in (
            kafka_list_topics,
            kafka_describe_topic,
            kafka_peek_messages,
            kafka_list_consumer_groups,
            kafka_describe_consumer_group,
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
