"""The three kb tools (FR-011). Names, input schemas and outputs are dictated by
`api-contract.yaml`; `tools.snapshot.json` + `tests/test_contract.py` keep them in sync.

There is deliberately no tool that accepts SQL: only these three parameterised operations exist.
"""

from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp_common.config import CommonSettings
from mcp_common.readonly import readonly_tool
from mcp_common.tooling import ToolOutcome, register_tool
from pydantic import Field

from mcp_pgvector.client import SOURCE
from mcp_pgvector.read_api import PgVectorReadApi

__all__ = ["TOOL_DESCRIPTIONS", "make_tools", "register_tools"]

TOOL_DESCRIPTIONS: dict[str, str] = {
    "kb_semantic_search": (
        "Tìm đoạn tài liệu đã index (Confluence/GitLab/…) theo ngữ nghĩa và trả chunk kèm "
        "source_uri gốc để trích dẫn. status=empty chỉ khi không có gì đủ giống; nếu bộ lọc loại "
        "mất kết quả thì warning nói rõ là do bộ lọc."
    ),
    "kb_get_document": (
        "Lấy lại toàn văn một document đã index theo document_id hoặc source_uri, ghép các chunk "
        "theo thứ tự. Document không có hoặc đã bị xoá ở nguồn trả not_found; bị cắt theo "
        "max_chars thì trả status=partial."
    ),
    "kb_list_sources": (
        "Thống kê kho embedding theo nguồn: số document/chunk, lần ingest gần nhất và số giờ đã "
        "cũ. Dùng để nêu độ mới của dữ liệu semantic và nguồn nào chưa được index."
    ),
}

SourceTypeName = Literal[
    "confluence", "gitlab", "opensearch", "kibana", "cloudwatch", "kafka", "redis", "sqs",
    "sns", "pgvector", "jira",
]  # fmt: skip
SourceTypes = Annotated[
    list[SourceTypeName],
    Field(max_length=10, json_schema_extra={"uniqueItems": True}),
]

ApiFactory = Callable[[], PgVectorReadApi]


def make_tools(api: ApiFactory) -> dict[str, Callable[..., Any]]:
    @readonly_tool
    async def kb_semantic_search(
        query: Annotated[str, Field(min_length=3, max_length=4096)],
        top_k: Annotated[int, Field(ge=1, le=50)] = 8,
        min_similarity: Annotated[float, Field(ge=0, le=1)] = 0.30,
        source_types: SourceTypes = [],  # noqa: B006 (pydantic copies defaults)
        container: Annotated[
            str | None, Field(description="Lọc theo space key / project path.")
        ] = None,
        updated_after: datetime | None = None,
        max_chars_per_chunk: Annotated[int, Field(ge=200, le=8000)] = 2000,
    ) -> ToolOutcome:
        return await api().semantic_search(
            query=query, top_k=top_k, min_similarity=min_similarity,
            source_types=list(source_types), container=container, updated_after=updated_after,
            max_chars_per_chunk=max_chars_per_chunk,
        )  # fmt: skip

    @readonly_tool
    async def kb_get_document(
        document_id: Annotated[
            str | None, Field(description="Có cả hai thì document_id thắng source_uri.")
        ] = None,
        source_uri: Annotated[str | None, Field(max_length=2048)] = None,
        max_chars: Annotated[int, Field(ge=500, le=100000)] = 20000,
    ) -> ToolOutcome:
        return await api().get_document(
            document_id=document_id, source_uri=source_uri, max_chars=max_chars
        )

    @readonly_tool
    async def kb_list_sources(
        source_types: SourceTypes = [],  # noqa: B006
    ) -> ToolOutcome:
        return await api().list_sources(source_types=list(source_types))

    return {fn.__name__: fn for fn in (kb_semantic_search, kb_get_document, kb_list_sources)}


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
