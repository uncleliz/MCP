"""T-011: `mcp_common.readonly` — the allowlist guard (ADR-0003 layer 2).

`enforce()` is called by every source's `client.py` before issuing an
operation/command/endpoint call: only what's in that source's allowlist is
permitted, everything else raises :class:`NotPermittedError` (→ `error.code =
not_permitted`). This module does **not** grant credentials (layer 3, ADR-0003) —
it only blocks *operations*.

`@readonly_tool` marks a tool function so `mcp_common.testing.assert_readonly_tool_surface`
(T-014) can scan the real registered tool surface and confirm every one of them is
marked, independent of what `tools.snapshot.json` claims.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Sequence
from functools import wraps
from typing import Any

from mcp_common.errors import NotPermittedError

__all__ = ["enforce", "readonly_tool", "is_readonly_tool"]

_READONLY_TOOL_MARKER = "__mcp_readonly_tool__"


def enforce(allowlist: Sequence[str], operation: str, *, source: str) -> None:
    """Raise `NotPermittedError` unless `operation` is exactly present in `allowlist`.

    `operation` is a source-defined string identifying what was attempted, e.g.
    `"GET /rest/api/content/search"`, `"SET"`, `"POST /_search"`, `"DescribeAlarms"`.
    """
    if operation not in allowlist:
        raise NotPermittedError(
            f"Operation '{operation}' is not in the read-only allowlist for '{source}'.",
            source=source,
            operation=operation,
            allowlist=list(allowlist),
        )


def readonly_tool[F: Callable[..., Any]](func: F) -> F:
    """Mark a tool function as read-only (works on both sync and async functions).

    Purely a marker + metadata-preserving passthrough — it does not itself enforce
    anything (that is `enforce()`'s job); it exists so the tool *surface* can be
    scanned later (T-014) and cross-checked against `tools.snapshot.json` /
    `api-contract.yaml`'s `x-readonly: true`.
    """
    if inspect.iscoroutinefunction(func):

        @wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            return await func(*args, **kwargs)

        marked: Any = async_wrapper
    else:

        @wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            return func(*args, **kwargs)

        marked = sync_wrapper

    setattr(marked, _READONLY_TOOL_MARKER, True)
    return marked  # type: ignore[return-value]


def is_readonly_tool(func: Callable[..., Any]) -> bool:
    """True if `func` (or the function it wraps) was decorated with `@readonly_tool`."""
    return bool(getattr(func, _READONLY_TOOL_MARKER, False))
