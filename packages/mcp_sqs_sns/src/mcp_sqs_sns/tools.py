"""The six SQS/SNS tools (FR-010). Names, input schemas and outputs are dictated by
`api-contract.yaml`; `tools.snapshot.json` + `tests/test_contract.py` keep them in sync.

No tool reads message content: `sqs:ReceiveMessage` has a side effect (visibility timeout) and is
excluded by design (BR-001).
"""

from collections.abc import Callable
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from mcp_common.config import CommonSettings
from mcp_common.readonly import readonly_tool
from mcp_common.tooling import CursorParam, LimitParam, ToolOutcome, register_tool
from pydantic import Field

from mcp_sqs_sns.read_api import SqsSnsReadApi

__all__ = ["TOOL_DESCRIPTIONS", "TOOL_SOURCES", "make_tools", "register_tools"]

TOOL_DESCRIPTIONS: dict[str, str] = {
    "sqs_list_queues": (
        "Liệt kê SQS queue theo tiền tố tên. Chỉ trả metadata (tên, URL, ARN), không đọc nội "
        "dung message."
    ),
    "sqs_get_queue_attributes": (
        "Thuộc tính một queue: số message xấp xỉ, ARN, dead-letter queue, retention. "
        "Queue không tồn tại trả not_found; không bao giờ đọc nội dung message."
    ),
    "sqs_list_dead_letter_source_queues": (
        "Liệt kê các queue dùng queue này làm dead-letter queue, để biết DLQ đang hứng message "
        "từ đâu. Không queue nào trỏ tới thì trả status=empty."
    ),
    "sns_list_topics": "Liệt kê SNS topic, lọc theo tiền tố tên trong ARN.",
    "sns_get_topic_attributes": (
        "Thuộc tính một SNS topic: số subscription, FIFO, delivery policy. "
        "ARN không tồn tại trả not_found."
    ),
    "sns_list_subscriptions_by_topic": (
        "Liệt kê subscription của một SNS topic để lần theo đường đi của message (fan-out tới "
        "queue/endpoint nào). Topic chưa có subscription trả status=empty."
    ),
}

# Error/log source per tool (contract: `meta.source` is `sqs` or `sns`).
TOOL_SOURCES: dict[str, str] = {name: name.split("_", 1)[0] for name in TOOL_DESCRIPTIONS}

ApiFactory = Callable[[], SqsSnsReadApi]


def make_tools(api: ApiFactory) -> dict[str, Callable[..., Any]]:
    @readonly_tool
    async def sqs_list_queues(
        name_prefix: Annotated[str | None, Field(max_length=80)] = None,
        limit: LimitParam = 20,
        cursor: CursorParam = None,
    ) -> ToolOutcome:
        return await api().list_queues(name_prefix=name_prefix, limit=limit, cursor=cursor)

    @readonly_tool
    async def sqs_get_queue_attributes(
        queue_name: Annotated[str | None, Field(max_length=80)] = None,
        queue_url: Annotated[
            str | None,
            Field(max_length=512, description="Có cả hai thì queue_url thắng queue_name."),
        ] = None,
        include_tags: bool = False,
    ) -> ToolOutcome:
        return await api().get_queue_attributes(
            queue_name=queue_name, queue_url=queue_url, include_tags=include_tags
        )

    @readonly_tool
    async def sqs_list_dead_letter_source_queues(
        queue_name: Annotated[str | None, Field(max_length=80)] = None,
        queue_url: Annotated[str | None, Field(max_length=512)] = None,
        limit: LimitParam = 20,
        cursor: CursorParam = None,
    ) -> ToolOutcome:
        return await api().list_dead_letter_source_queues(
            queue_name=queue_name, queue_url=queue_url, limit=limit, cursor=cursor
        )

    @readonly_tool
    async def sns_list_topics(
        name_prefix: Annotated[str | None, Field(max_length=256)] = None,
        limit: LimitParam = 20,
        cursor: CursorParam = None,
    ) -> ToolOutcome:
        return await api().list_topics(name_prefix=name_prefix, limit=limit, cursor=cursor)

    @readonly_tool
    async def sns_get_topic_attributes(
        topic_arn: Annotated[str, Field(pattern=r"^arn:aws[a-zA-Z-]*:sns:", max_length=512)],
    ) -> ToolOutcome:
        return await api().get_topic_attributes(topic_arn=topic_arn)

    @readonly_tool
    async def sns_list_subscriptions_by_topic(
        topic_arn: Annotated[str, Field(pattern=r"^arn:aws[a-zA-Z-]*:sns:", max_length=512)],
        limit: LimitParam = 20,
        cursor: CursorParam = None,
    ) -> ToolOutcome:
        return await api().list_subscriptions_by_topic(
            topic_arn=topic_arn, limit=limit, cursor=cursor
        )

    return {
        fn.__name__: fn
        for fn in (
            sqs_list_queues,
            sqs_get_queue_attributes,
            sqs_list_dead_letter_source_queues,
            sns_list_topics,
            sns_get_topic_attributes,
            sns_list_subscriptions_by_topic,
        )
    }


def register_tools(mcp: FastMCP, api: ApiFactory, common: CommonSettings | None = None) -> None:
    for name, fn in make_tools(api).items():
        register_tool(
            mcp,
            fn,
            name=name,
            description=TOOL_DESCRIPTIONS[name],
            source=TOOL_SOURCES[name],
            common_settings=common,
        )
