"""Register the 8 knowledge tools on a FastMCP server (T-100/T-101/T-102).

Six tools return the standard envelope (:class:`ToolResult`) and go through
:func:`mcp_common.tooling.register_tool`. Two tools (``search_company_knowledge`` /
``get_jira_context``) return the grounded envelope (:class:`GroundedResult`), which the shared
``register_tool`` does not render, so they go through :func:`register_grounded_tool` here — a
local peer that reuses the same per-tool deadline and error-envelope mapping but renders grounded
text (UNKNOWN surfaces the fixed message, never a fabricated answer).

Every function is marked ``@readonly_tool`` so ``assert_readonly_tool_surface`` sees a 0-write
surface. The input schemas mirror ``api-contract.yaml``; ``tools.snapshot.json`` keeps them in sync.
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import time
from collections.abc import Callable
from typing import Annotated, Any, get_type_hints

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent
from mcp_common.config import CommonSettings
from mcp_common.errors import (
    ErrorCode,
    ToolError,
    map_exception_to_tool_error,
    to_error_envelope,
)
from mcp_common.logging import get_logger
from mcp_common.readonly import readonly_tool
from mcp_common.runtime import tool_deadline_for
from mcp_common.tooling import ToolOutcome, register_tool
from pydantic import Field

from mcp_knowledge.client import SOURCE
from mcp_knowledge.tools.grounded import GroundedOutcome, render_grounded_text
from mcp_knowledge.tools.read_api import KnowledgeReadApi

__all__ = ["TOOL_DESCRIPTIONS", "make_tools", "register_tools"]

GROUNDED_TOOLS = frozenset({"search_company_knowledge", "get_jira_context"})

TOOL_DESCRIPTIONS: dict[str, str] = {
    "search_company_knowledge": (
        "Hybrid-RAG grounded search về công ty: trả claim có verdict + provenance per-claim, kèm "
        "citation về nguồn gốc. Không có nguồn ⇒ UNKNOWN với thông điệp cố định, không bịa."
    ),
    "get_service": (
        "Hồ sơ một service (entity): owner, repo, dependencies và tài liệu liên quan (≤3 hop). "
        "status=not_found nếu không có service khớp."
    ),
    "get_repository": (
        "Hồ sơ một repository (entity): service liên quan, tài liệu, trạng thái. status=not_found "
        "nếu không khớp."
    ),
    "search_code": (
        "Tìm code trong corpus đã index (chunk source_type=gitlab) bằng keyword simple ∪ vector, "
        "RRF. Citation trỏ về source_uri GitLab gốc. status=empty khi không khớp."
    ),
    "get_jira_context": (
        "Ngữ cảnh công việc hiện tại cho một entity/topic: hợp nhất Jira live + snapshot, "
        "trả grounded claim. UNKNOWN khi không nguồn."
    ),
    "find_related_knowledge": (
        "Graph traversal nông (recursive CTE ≤3 hop, cycle-detect, fan-out LIMIT) từ một entity. "
        "not_found nếu entity gốc không tồn tại; empty nếu không có cạnh."
    ),
    "get_knowledge_summary": (
        "Summary đã sinh cho một entity/topic kèm provenance (đọc kb.knowledge_summaries). "
        "not_found nếu chưa có summary cho subject."
    ),
    "get_document_version": (
        "Lịch sử version của một document (current/superseded) kèm source_version/author/"
        "source_updated_at và citation. not_found nếu document không tồn tại hoặc đã tombstone."
    ),
}

ApiFactory = Callable[[], KnowledgeReadApi]

# ---- input-schema aliases mirroring api-contract.yaml -----------------------------------------

SourceTypeName = Annotated[
    str,
    Field(
        json_schema_extra={
            "enum": [
                "confluence", "gitlab", "opensearch", "kibana", "cloudwatch", "kafka", "redis",
                "sqs", "sns", "pgvector", "jira",
            ]
        }
    ),
]  # fmt: skip
RelType = Annotated[
    str, Field(json_schema_extra={"enum": ["depends_on", "documented_by", "owns", "related_to"]})
]


def make_tools(api: ApiFactory) -> dict[str, Callable[..., Any]]:
    @readonly_tool
    async def search_company_knowledge(
        query: Annotated[str, Field(min_length=3, max_length=4096)],
        top_k: Annotated[int, Field(ge=1, le=50)] = 8,
        source_types: Annotated[
            list[SourceTypeName], Field(max_length=11, json_schema_extra={"uniqueItems": True})
        ] = [],  # noqa: B006
        fact_type: Annotated[str | None, Field()] = None,
        include_live: bool = True,
    ) -> GroundedOutcome:
        return await api().search_company_knowledge(
            query=query, top_k=top_k, source_types=list(source_types),
            fact_type=fact_type, include_live=include_live,
        )  # fmt: skip

    @readonly_tool
    async def get_service(
        name: Annotated[str, Field(min_length=1, max_length=256)],
    ) -> ToolOutcome:
        return await api().get_service(name=name)

    @readonly_tool
    async def get_repository(
        name: Annotated[str, Field(min_length=1, max_length=512)],
    ) -> ToolOutcome:
        return await api().get_repository(name=name)

    @readonly_tool
    async def search_code(
        query: Annotated[str, Field(min_length=2, max_length=2048)],
        top_k: Annotated[int, Field(ge=1, le=50)] = 10,
    ) -> ToolOutcome:
        return await api().search_code(query=query, top_k=top_k)

    @readonly_tool
    async def get_jira_context(
        subject: Annotated[str, Field(min_length=1, max_length=256)],
        top_k: Annotated[int, Field(ge=1, le=50)] = 10,
    ) -> GroundedOutcome:
        return await api().get_jira_context(subject=subject, top_k=top_k)

    @readonly_tool
    async def find_related_knowledge(
        entity: Annotated[str, Field(min_length=1, max_length=256)],
        max_depth: Annotated[int, Field(ge=1, le=3)] = 2,
        rel_types: Annotated[
            list[RelType], Field(json_schema_extra={"uniqueItems": True})
        ] = [],  # noqa: B006
    ) -> ToolOutcome:
        return await api().find_related_knowledge(
            entity=entity, max_depth=max_depth, rel_types=list(rel_types)
        )

    @readonly_tool
    async def get_knowledge_summary(
        subject: Annotated[str, Field(min_length=1, max_length=256)],
        subject_type: Annotated[
            str, Field(json_schema_extra={"enum": ["entity", "topic"]})
        ] = "entity",
    ) -> ToolOutcome:
        return await api().get_knowledge_summary(subject=subject, subject_type=subject_type)

    @readonly_tool
    async def get_document_version(
        document_id: Annotated[str, Field(json_schema_extra={"format": "uuid"})],
        version: Annotated[int | None, Field(ge=1)] = None,
    ) -> ToolOutcome:
        return await api().get_document_version(document_id=document_id, version=version)

    return {
        "search_company_knowledge": search_company_knowledge,
        "get_service": get_service,
        "get_repository": get_repository,
        "search_code": search_code,
        "get_jira_context": get_jira_context,
        "find_related_knowledge": find_related_knowledge,
        "get_knowledge_summary": get_knowledge_summary,
        "get_document_version": get_document_version,
    }


def register_tools(mcp: FastMCP, api: ApiFactory, common: CommonSettings | None = None) -> None:
    for name, fn in make_tools(api).items():
        if name in GROUNDED_TOOLS:
            register_grounded_tool(
                mcp, fn, name=name, description=TOOL_DESCRIPTIONS[name], common_settings=common
            )
        else:
            register_tool(
                mcp, fn, name=name, description=TOOL_DESCRIPTIONS[name],
                source=SOURCE, common_settings=common,
            )  # fmt: skip


def register_grounded_tool(
    mcp: FastMCP,
    fn: Callable[..., Any],
    *,
    name: str,
    description: str,
    common_settings: CommonSettings | None = None,
) -> None:
    """Peer of :func:`mcp_common.tooling.register_tool` for tools returning a grounded envelope.

    Same per-tool deadline (ADR-0006 A2) and the same error → ``ErrorEnvelope`` mapping; the only
    difference is it renders a :class:`GroundedResult` (not a :class:`ToolResult`).
    """
    logger = get_logger(SOURCE)

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> CallToolResult:
        deadline = tool_deadline_for(name, common_settings)
        error: ToolError | None = None
        outcome: GroundedOutcome | None = None
        try:
            async with asyncio.timeout(deadline):
                outcome = await fn(*args, **kwargs)
        except TimeoutError:
            error = ToolError(
                ErrorCode.UPSTREAM_TIMEOUT, f"Tool '{name}' vượt deadline {deadline}s.",
                SOURCE, True, details={"hint": "kiểm tra VPN/kết nối mạng nội bộ", "tool": name},
            )  # fmt: skip
        except ToolError as exc:
            error = exc
        except Exception as exc:  # noqa: BLE001 - tool boundary must never leak a traceback
            logger.error(
                f"unexpected {type(exc).__name__} in tool",
                extra={"tool": name, "error_code": ErrorCode.INTERNAL.value},
            )
            error = map_exception_to_tool_error(exc, source=SOURCE)

        if error is not None:
            logger.warning(
                "tool failed",
                extra={"tool": name, "status": "error", "error_code": error.code.value},
            )
            envelope = to_error_envelope(error)
            return CallToolResult(
                content=[TextContent(type="text", text=_error_text(envelope))],
                structuredContent=envelope,
                isError=True,
            )
        assert outcome is not None
        logger.info(
            "tool ok",
            extra={
                "tool": name,
                "status": outcome.result.status.value,
                "claims": len(outcome.result.claims),
            },
        )
        text = render_grounded_text(outcome.result, query_description=outcome.query_description)
        return CallToolResult(
            content=[TextContent(type="text", text=text)],
            structuredContent=outcome.result.model_dump(mode="json"),
            isError=False,
        )

    hints = get_type_hints(fn, include_extras=True)
    signature = inspect.signature(fn)
    params = [
        p.replace(annotation=hints.get(p.name, p.annotation)) for p in signature.parameters.values()
    ]
    wrapper.__signature__ = signature.replace(  # type: ignore[attr-defined]
        parameters=params, return_annotation=CallToolResult
    )
    wrapper.__annotations__ = {**hints, "return": CallToolResult}
    mcp.tool(name=name, description=description)(wrapper)


def _error_text(envelope: dict[str, Any]) -> str:
    error = envelope["error"]
    lines = [f"Lỗi {error['code']} ({error['source']}): {error['message']}"]
    hint = (error.get("details") or {}).get("hint")
    if hint:
        lines.append(f"Gợi ý: {hint}")
    return "\n".join(lines)


# `time` imported for parity with register_tool's duration logging; referenced to satisfy linters.
_ = time
