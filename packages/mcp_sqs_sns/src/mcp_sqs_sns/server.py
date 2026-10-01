"""`build_server()` for mcp-sqs-sns (ADR-0002): a plain `FastMCP` with the 6 metadata tools;
transport and startup gating are `mcp_common.runtime.serve`'s job."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP
from mcp_common.config import CommonSettings, SourceMisconfiguredError, load_settings
from mcp_common.errors import ErrorCode, ToolError
from mcp_common.runtime import build_server as build_base_server

from mcp_sqs_sns.client import SOURCE, SqsSnsClient
from mcp_sqs_sns.read_api import SqsSnsReadApi
from mcp_sqs_sns.settings import Settings
from mcp_sqs_sns.tools import register_tools

__all__ = ["SERVER_INSTRUCTIONS", "SERVER_NAME", "build_read_api", "build_server"]

SERVER_NAME = "mcp-sqs-sns"

SERVER_INSTRUCTIONS = (
    "Server chỉ đọc metadata của AWS SQS/SNS: queue, topic, subscription và số message xấp xỉ. "
    "Server KHÔNG đọc nội dung message (ReceiveMessage làm đổi visibility timeout nên bị loại). "
    'Mọi câu trả lời dựa trên kết quả tool phải kèm mục "Nguồn" (ARN của queue/topic); số message '
    "là xấp xỉ nên phải nói rõ như vậy. Nếu tool trả status=empty hoặc not_found, hãy nói rõ là "
    "không có dữ liệu, không được tự suy đoán."
)


def build_read_api(
    settings: Settings | None = None, common: CommonSettings | None = None
) -> SqsSnsReadApi:
    common = common or CommonSettings()
    settings = settings or load_settings(Settings, source=SOURCE)
    return SqsSnsReadApi(SqsSnsClient(settings, common=common), common, settings)


def build_server(
    read_api: SqsSnsReadApi | None = None,
    *,
    settings: Settings | None = None,
    common: CommonSettings | None = None,
) -> FastMCP:
    """Register tools lazily: the read API (and therefore the credentials) is only built on the
    first tool call, so `tools/list` and `tools-dump` never need a configured source."""
    holder: list[SqsSnsReadApi] = [read_api] if read_api else []

    def api() -> SqsSnsReadApi:
        if not holder:
            try:
                holder.append(build_read_api(settings, common))
            except SourceMisconfiguredError as exc:
                raise ToolError(
                    ErrorCode.SOURCE_MISCONFIGURED,
                    str(exc),
                    SOURCE,
                    False,
                    details={"missing_env": exc.missing_vars},
                ) from exc
        return holder[0]

    mcp = build_base_server(SERVER_NAME, instructions=SERVER_INSTRUCTIONS)
    register_tools(mcp, api, common)
    return mcp
