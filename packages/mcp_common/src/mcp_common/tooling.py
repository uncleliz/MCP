"""Shared tool plumbing for every `mcp_<source>` package (first used by Phase 1).

Nothing here talks to a source. It is the glue between a server's `read_api.py` (which
returns a :class:`ToolOutcome`) and the MCP SDK:

* result builders (`build_result`, `not_found_result`) that pick the right
  `ResultStatus` and fill `Meta` (ADR-0004);
* opaque pagination cursors (`encode_cursor` / `decode_cursor`, contract `CursorField`);
* output budgeting (`effective_max_bytes`, `TextBudget`) and free-text hygiene
  (`safe_text` = redact then `wrap_untrusted`, ADR-0015 — only at the result boundary);
* `register_tool`, which wraps a tool coroutine with the per-tool deadline
  (ADR-0006 A2), exception → `ErrorEnvelope` mapping, and converts the outcome to a
  `CallToolResult` carrying `structuredContent` plus the rendered text Claude reads
  (`info.x-text-rendering`). Errors come back with `isError=True` per the contract.
"""

from __future__ import annotations

import asyncio
import base64
import functools
import inspect
import json
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any, get_type_hints

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent
from pydantic import Field

from mcp_common.config import CommonSettings
from mcp_common.content import truncate_bytes, wrap_untrusted
from mcp_common.envelope import Citation, Meta, ResultStatus, SourceType, ToolResult
from mcp_common.errors import (
    ErrorCode,
    ToolError,
    map_exception_to_tool_error,
    to_error_envelope,
)
from mcp_common.logging import get_logger
from mcp_common.redact import scrub
from mcp_common.render import render_text
from mcp_common.runtime import tool_deadline_for

__all__ = [
    "ToolOutcome",
    "RedactionCounter",
    "TextBudget",
    "encode_cursor",
    "decode_cursor",
    "build_result",
    "not_found_result",
    "effective_max_bytes",
    "safe_text",
    "parse_aware_datetime",
    "register_tool",
    "registered_tool_functions",
    "NOT_FOUND_WARNING",
    "CallState",
    "parse_time_range",
    "sanitize_json",
    "LimitParam",
    "CursorParam",
    "MaxBytesParam",
    "invalid_input",
]

NOT_FOUND_WARNING = "identifier does not exist at source"

# Shared `inputSchema` building blocks mirroring contract `LimitField` / `CursorField` /
# `MaxBytesField` (defaults are set on each tool parameter: limit=20, max_bytes=65536).
LimitParam = Annotated[int, Field(ge=1, le=100, description="Số item tối đa trả về (1..100).")]
CursorParam = Annotated[
    str | None,
    Field(max_length=4096, description="Cursor opaque lấy từ meta.next_cursor của lần gọi trước."),
]
MaxBytesParam = Annotated[
    int,
    Field(
        ge=1024,
        le=131072,
        description="Trần byte cho nội dung tự do; min(max_bytes, MCP_MAX_OUTPUT_BYTES).",
    ),
]


@dataclass
class ToolOutcome:
    """What a tool coroutine returns: the envelope plus optional phrases used by the
    text renderer for the `empty` / `not_found` sentences."""

    result: ToolResult
    query_description: str | None = None
    identifier: str | None = None


class RedactionCounter:
    """Accumulates `scrub()` replacements into `meta.redactions`."""

    def __init__(self) -> None:
        self.count = 0


class CallState:
    """State of one tool call: clock, redaction counter, shared byte budget, warnings.

    Shared by the Phase-2 `read_api` modules (gitlab/confluence keep private copies).
    """

    def __init__(
        self,
        budget_bytes: int,
        warnings: Sequence[str] | None = None,
        *,
        redact_disabled: bool = False,
    ) -> None:
        self.started = time.monotonic()
        self.counter = RedactionCounter()
        self.budget = TextBudget(budget_bytes)
        self.warnings: list[str] = list(warnings or [])
        self.truncated_elsewhere = False
        self._redact_disabled = redact_disabled

    @property
    def truncated(self) -> bool:
        return self.budget.truncated or self.truncated_elsewhere

    def warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)

    def text(self, text: str | None, source: str, content_id: str) -> str | None:
        """Redact -> spend budget -> wrap as untrusted (ADR-0015; result boundary only)."""
        if not text:
            return None
        scrubbed, count = scrub(text, disabled=self._redact_disabled)
        self.counter.count += count
        cut, _ = self.budget.take(scrubbed)
        return wrap_untrusted(cut, source=source, content_id=content_id)

    def plain(self, text: str) -> str:
        """Redact + spend budget, no untrusted wrapper (short structured values)."""
        scrubbed, count = scrub(text, disabled=self._redact_disabled)
        self.counter.count += count
        cut, _ = self.budget.take(scrubbed)
        return cut


_FREE_TEXT_KEYS = frozenset(
    {
        "message", "msg", "log", "error", "exception", "stack_trace", "stacktrace",
        "body", "text", "description", "reason", "@message",
    }
)  # fmt: skip
_FREE_TEXT_MIN_LEN = 200


def sanitize_json(
    value: Any, call: CallState, *, source: str, content_id: str, key: str | None = None
) -> Any:
    """Recursively redact every string leaf of a JSON-like value and spend the byte budget.

    Free-text leaves (well-known message-like keys, multi-line or > 200 chars) are also
    wrapped as untrusted content; short structured values (level, ids) stay plain.
    """
    if isinstance(value, str):
        free = (key or "").lower() in _FREE_TEXT_KEYS or len(value) > _FREE_TEXT_MIN_LEN
        if free or "\n" in value:
            return call.text(value, source, content_id) or ""
        return call.plain(value)
    if isinstance(value, dict):
        return {
            k: sanitize_json(v, call, source=source, content_id=content_id, key=str(k))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [
            sanitize_json(v, call, source=source, content_id=content_id, key=key) for v in value
        ]
    return value


def parse_time_range(
    time_from: datetime, time_to: datetime, *, max_days: int, source: str
) -> tuple[datetime, datetime]:
    """Validate a mandatory tool time window: tz-aware, `from < to`, <= `max_days`."""
    for field, value in (("time_from", time_from), ("time_to", time_to)):
        if value.tzinfo is None:
            raise invalid_input(field, "thiếu timezone (ví dụ hậu tố Z hoặc +07:00)", source)
    if time_from >= time_to:
        raise invalid_input("time_from", "phải nhỏ hơn time_to", source)
    if (time_to - time_from).total_seconds() > max_days * 86400:
        raise invalid_input(
            "time_from", f"khoảng thời gian vượt MCP_MAX_TIME_RANGE_DAYS ({max_days} ngày)", source
        )
    return time_from, time_to


def encode_cursor(state: Mapping[str, Any]) -> str:
    raw = json.dumps(dict(state), separators=(",", ":"), sort_keys=True).encode()
    return base64.urlsafe_b64encode(raw).decode()


def decode_cursor(cursor: str | None, *, source: str) -> dict[str, Any]:
    if not cursor:
        return {}
    try:
        state = json.loads(base64.urlsafe_b64decode(cursor.encode()))
    except (ValueError, UnicodeDecodeError) as exc:
        raise invalid_input(
            "cursor", "cursor không hợp lệ; dùng đúng meta.next_cursor", source
        ) from exc
    if not isinstance(state, dict):
        raise invalid_input("cursor", "cursor không hợp lệ; dùng đúng meta.next_cursor", source)
    return state


def invalid_input(field: str, message: str, source: str) -> ToolError:
    return ToolError(
        ErrorCode.INVALID_INPUT, f"{field}: {message}", source, False, details={"field": field}
    )


def parse_aware_datetime(value: str, *, field: str, source: str = "none") -> datetime:
    """Parse an ISO-8601 timestamp that MUST carry a timezone (contract invariant 5)."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise invalid_input(field, "phải là ISO-8601 (ví dụ 2026-01-01T00:00:00Z)", source) from exc
    if parsed.tzinfo is None:
        raise invalid_input(field, "thiếu timezone (ví dụ hậu tố Z hoặc +07:00)", source)
    return parsed


def _elapsed_ms(started: float) -> int:
    return max(int((time.monotonic() - started) * 1000), 0)


def build_result(
    source: SourceType,
    items: list[dict[str, Any]],
    citations: list[Citation],
    *,
    started: float,
    query_echo: Mapping[str, Any],
    next_cursor: str | None = None,
    truncated: bool = False,
    warnings: Sequence[str] = (),
    redactions: int = 0,
    scope_citation: Citation | None = None,
) -> ToolResult:
    """Build a successful `ToolResult`. No items → `empty`; truncated → `partial`.

    `scope_citation` covers contract invariant 7: a *truncated* result with no items (e.g. a
    Logs Insights query that ran out of time before any row) is `partial`, not `empty`, and
    cites the query scope (log group + window) instead of an item.
    """
    if not items and truncated and scope_citation is not None:
        status = ResultStatus.PARTIAL
        citations = [scope_citation]
    elif not items:
        status = ResultStatus.EMPTY
        citations = []
    else:
        status = ResultStatus.PARTIAL if truncated else ResultStatus.OK
    meta = Meta(
        source=source,
        returned=len(items),
        has_more=bool(next_cursor),
        next_cursor=next_cursor or None,
        truncated=truncated,
        elapsed_ms=_elapsed_ms(started),
        as_of=datetime.now(UTC),
        query_echo=dict(query_echo),
        warnings=[w[:512] for w in warnings],
        redactions=redactions,
    )
    return ToolResult(status=status, items=items, citations=citations, meta=meta)


def not_found_result(
    source: SourceType, *, started: float, query_echo: Mapping[str, Any]
) -> ToolResult:
    meta = Meta(
        source=source,
        returned=0,
        has_more=False,
        next_cursor=None,
        truncated=False,
        elapsed_ms=_elapsed_ms(started),
        as_of=datetime.now(UTC),
        query_echo=dict(query_echo),
        warnings=[NOT_FOUND_WARNING],
    )
    return ToolResult(status=ResultStatus.NOT_FOUND, items=[], citations=[], meta=meta)


def effective_max_bytes(requested: int, common: CommonSettings) -> tuple[int, list[str]]:
    """`min(max_bytes, MCP_MAX_OUTPUT_BYTES)`; clamping is a warning, never an error."""
    effective = min(requested, common.max_output_bytes)
    warnings: list[str] = []
    if effective < requested:
        warnings.append(f"max_bytes clamped from {requested} to {effective} (MCP_MAX_OUTPUT_BYTES)")
    return effective, warnings


class TextBudget:
    """A shared byte budget for the free-text fields of one response."""

    def __init__(self, max_bytes: int) -> None:
        self._remaining = max_bytes
        self.truncated = False

    @property
    def exhausted(self) -> bool:
        return self._remaining <= 0

    def take(self, text: str) -> tuple[str, bool]:
        if self._remaining <= 0:
            if text:
                self.truncated = True
            return "", bool(text)
        cut, was_truncated = truncate_bytes(text, self._remaining)
        self._remaining -= len(cut.encode("utf-8"))
        if was_truncated:
            self._remaining = 0
            self.truncated = True
        return cut, was_truncated


def safe_text(
    text: str | None,
    *,
    source: str,
    content_id: str,
    counter: RedactionCounter,
    disabled: bool,
) -> str | None:
    """Redact, then wrap as untrusted content (ADR-0015). Result boundary only."""
    if text is None:
        return None
    scrubbed, count = scrub(text, disabled=disabled)
    counter.count += count
    return wrap_untrusted(scrubbed, source=source, content_id=content_id)


def _error_text(envelope: dict[str, Any]) -> str:
    error = envelope["error"]
    lines = [f"Lỗi {error['code']} ({error['source']}): {error['message']}"]
    hint = (error.get("details") or {}).get("hint")
    if hint:
        lines.append(f"Gợi ý: {hint}")
    if error.get("retryable"):
        retry = error.get("retry_after_s")
        lines.append(f"Có thể thử lại{f' sau {retry}s' if retry else ''}.")
    return "\n".join(lines)


def _error_result(error: ToolError) -> CallToolResult:
    envelope = to_error_envelope(error)
    return CallToolResult(
        content=[TextContent(type="text", text=_error_text(envelope))],
        structuredContent=envelope,
        isError=True,
    )


def _ok_result(outcome: ToolOutcome) -> CallToolResult:
    text = render_text(
        outcome.result,
        query_description=outcome.query_description,
        identifier=outcome.identifier,
    )
    return CallToolResult(
        content=[TextContent(type="text", text=text)],
        structuredContent=outcome.result.model_dump(mode="json"),
        isError=False,
    )


def register_tool(
    mcp: FastMCP,
    fn: Callable[..., Awaitable[ToolOutcome]],
    *,
    name: str,
    description: str,
    source: str,
    common_settings: CommonSettings | None = None,
) -> None:
    """Register `fn` on `mcp` as tool `name` with deadline + envelope handling.

    `fn`'s own signature is what FastMCP turns into the tool `inputSchema`; the
    `@readonly_tool` marker on `fn` is preserved on the registered wrapper.
    """
    logger = get_logger(source)

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> CallToolResult:
        started = time.monotonic()
        deadline = tool_deadline_for(name, common_settings)
        error: ToolError | None = None
        outcome: ToolOutcome | None = None
        try:
            async with asyncio.timeout(deadline):
                outcome = await fn(*args, **kwargs)
        except TimeoutError:
            error = ToolError(
                ErrorCode.UPSTREAM_TIMEOUT,
                f"Tool '{name}' vượt deadline {deadline}s.",
                source,
                True,
                details={"hint": "kiểm tra VPN/kết nối mạng nội bộ", "tool": name},
            )
        except ToolError as exc:
            error = exc
        except Exception as exc:  # noqa: BLE001 - tool boundary must never leak a traceback
            logger.error(
                f"unexpected {type(exc).__name__} in tool",
                extra={"tool": name, "error_code": ErrorCode.INTERNAL.value},
            )
            error = map_exception_to_tool_error(exc, source=source)

        duration_ms = int((time.monotonic() - started) * 1000)
        if error is not None:
            logger.warning(
                "tool failed",
                extra={
                    "tool": name,
                    "duration_ms": duration_ms,
                    "status": "error",
                    "error_code": error.code.value,
                    "upstream_status": error.details.get("upstream_status"),
                    "upstream_host": error.details.get("host"),
                },
            )
            return _error_result(error)
        assert outcome is not None
        meta = outcome.result.meta
        logger.info(
            "tool ok",
            extra={
                "tool": name,
                "duration_ms": duration_ms,
                "status": outcome.result.status.value,
                "items_returned": meta.returned,
                "truncated": meta.truncated,
                "redactions": meta.redactions,
            },
        )
        return _ok_result(outcome)

    # Resolve string annotations against the *tool module's* globals: FastMCP would
    # otherwise evaluate them against this module's globals and fail on e.g. `datetime`.
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


def registered_tool_functions(mcp: FastMCP) -> dict[str, Callable[..., Any]]:
    """The functions actually registered on the server (for the read-only surface scan)."""
    return {tool.name: tool.fn for tool in mcp._tool_manager.list_tools()}
