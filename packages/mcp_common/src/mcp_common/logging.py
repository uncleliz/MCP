"""T-006: structured JSON logging (stderr only) + the stdout guard (R2).

stdio is the JSON-RPC transport channel for every MCP server; any stray byte written
to stdout outside the protocol framing kills the session (NFR-005's most common
failure mode, per architecture.md). Two independent defenses:

1. :func:`setup_logging` configures a JSON formatter that only ever attaches to a
   stderr (or optional file) handler — there is no stdout handler to misconfigure.
2. :func:`install_stdout_guard` replaces `sys.stdout` with a proxy whose `.write()`/
   `.writelines()` raise :class:`StdoutGuardViolation`. This is safe to install even
   while the real MCP stdio transport is running: `mcp.server.stdio.stdio_server`
   writes JSON-RPC frames through `sys.stdout.buffer` (a `TextIOWrapper` built
   directly on the raw buffer), never through `sys.stdout.write()` — so the guard
   only ever catches misuse (a stray `print()`, a misdirected log handler), never
   the protocol traffic itself. The guard proxies `.buffer` straight through to the
   real stdout for that reason.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any, TextIO

__all__ = [
    "STANDARD_LOG_FIELDS",
    "JSONStderrFormatter",
    "StdoutGuardViolation",
    "setup_logging",
    "get_logger",
    "install_stdout_guard",
    "uninstall_stdout_guard",
]

# architecture.md "Logging & observability" — the fixed field set for every log line.
STANDARD_LOG_FIELDS: tuple[str, ...] = (
    "server",
    "tool",
    "request_id",
    "duration_ms",
    "status",
    "error_code",
    "upstream_status",
    "upstream_host",
    "items_returned",
    "truncated",
    "redactions",
)


class JSONStderrFormatter(logging.Formatter):
    """Renders one JSON object per log record with a fixed, documented field set.

    Only the fields in `STANDARD_LOG_FIELDS` are ever emitted (plus `ts`/`level`/
    `message`) — any other `extra=` a call site passes (e.g. a full query string) is
    silently dropped by construction, which is how "không log query đầy đủ ở INFO" is
    enforced structurally rather than by convention.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "message": record.getMessage(),
        }
        for field in STANDARD_LOG_FIELDS:
            payload[field] = getattr(record, field, None)
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class _ServerLoggerAdapter(logging.LoggerAdapter):
    """Injects `server` onto every record without every call site repeating it."""

    def process(self, msg: Any, kwargs: Any) -> tuple[Any, Any]:
        extra = kwargs.setdefault("extra", {})
        extra.setdefault("server", self.extra["server"] if self.extra else None)
        return msg, kwargs


def setup_logging(
    server: str, *, level: str = "INFO", log_file: str | None = None
) -> logging.LoggerAdapter:
    """Configure `mcp.<server>` to log structured JSON to stderr (+ optional file).

    Never attaches a stdout handler — stdout is reserved for the JSON-RPC transport.
    """
    logger = logging.getLogger(f"mcp.{server}")
    logger.setLevel(level)
    logger.handlers.clear()
    logger.propagate = False

    stderr_handler = logging.StreamHandler(stream=sys.stderr)
    stderr_handler.setFormatter(JSONStderrFormatter())
    logger.addHandler(stderr_handler)

    if log_file:
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(JSONStderrFormatter())
        logger.addHandler(file_handler)

    return get_logger(server)


def get_logger(server: str) -> logging.LoggerAdapter:
    """Return a logger bound to `server`, already configured or not."""
    return _ServerLoggerAdapter(logging.getLogger(f"mcp.{server}"), {"server": server})


class StdoutGuardViolation(RuntimeError):
    """Raised when something writes to stdout outside the JSON-RPC transport (R2)."""


class _StdoutGuard:
    """Proxy for `sys.stdout` that blocks `.write()`/`.writelines()` but forwards
    `.buffer` (and everything else) straight through to the real stdout, so the real
    MCP stdio transport — which writes via `sys.stdout.buffer` — is unaffected."""

    def __init__(self, real: TextIO) -> None:
        object.__setattr__(self, "_mcp_guard_real", real)

    @property
    def buffer(self) -> Any:
        return self._mcp_guard_real.buffer

    def write(self, data: str) -> int:
        raise StdoutGuardViolation(
            "blocked write() to stdout — stdout is the JSON-RPC transport channel "
            f"(R2); got: {data!r}"
        )

    def writelines(self, lines: Any) -> None:
        raise StdoutGuardViolation(
            "blocked writelines() to stdout — stdout is the JSON-RPC transport channel (R2)"
        )

    def flush(self) -> None:
        return None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._mcp_guard_real, name)


def install_stdout_guard() -> None:
    """Replace `sys.stdout` with the guard, idempotently."""
    if isinstance(sys.stdout, _StdoutGuard):
        return
    sys.stdout = _StdoutGuard(sys.stdout)  # type: ignore[assignment]


def uninstall_stdout_guard() -> None:
    """Restore the real `sys.stdout`, idempotently."""
    current = sys.stdout
    if isinstance(current, _StdoutGuard):
        sys.stdout = current._mcp_guard_real
