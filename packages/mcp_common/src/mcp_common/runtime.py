"""T-012: `mcp_common.runtime` — `build_server()`/`serve()` + the bounded executor +
the startup credential gate.

Three independent pieces (ADR-0002, ADR-0003 A1, ADR-0006 A1/A2):

* :func:`build_server` — the one-line factory every `mcp_<source>.server` module
  calls (ADR-0002: "mỗi package server chỉ export một hàm `build_server() -> FastMCP`
  thuần"). Tool/prompt registration happens in each source package, not here.
* :func:`with_tool_deadline` — the decorator each tool function is wrapped in before
  being registered: applies the per-call deadline (`MCP_TOOL_DEADLINE`, overridable
  per-tool via `MCP_TOOL_DEADLINE_<TOOL>`) and turns any exception into an
  `ErrorEnvelope` instead of letting it propagate as a raw JSON-RPC error.
* :class:`BoundedExecutor` — a `ThreadPoolExecutor` with a hard cap on *total*
  in-flight work (running, not queued): `asyncio.timeout` cancels the *coroutine*
  awaiting a thread, never the thread itself, so an unbounded default executor would
  let already-hung synchronous SDK calls (boto3, confluent-kafka) silently block every
  subsequent tool call (R16). Submitting past capacity fails immediately with
  `upstream_unavailable`, never waits for a free worker.
* :func:`serve` — wires all of the above together: fail-closed on any transport other
  than `stdio` (ADR-0002/ADR-0013), runs the startup credential check as a condition
  of serving (ADR-0003 A1, escape hatch `MCP_ALLOW_UNVERIFIED_CREDENTIALS=true`),
  installs the stdout guard + structured logging, then hands off to the MCP SDK's
  stdio transport.
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import logging
import os
import threading
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any, ParamSpec, TypeVar

from mcp.server.fastmcp import FastMCP

from mcp_common.config import CommonSettings, SourceMisconfiguredError
from mcp_common.errors import ErrorCode, ToolError, map_exception_to_tool_error, to_error_envelope
from mcp_common.logging import get_logger, install_stdout_guard, setup_logging

__all__ = [
    "build_server",
    "tool_deadline_for",
    "with_tool_deadline",
    "BoundedExecutor",
    "CredentialCheck",
    "run_credential_check",
    "serve",
]

P = ParamSpec("P")
T = TypeVar("T")

CredentialCheck = Callable[[], "Awaitable[bool] | bool"]


def build_server(name: str, *, instructions: str | None = None, **kwargs: Any) -> FastMCP:
    """The one-line factory every `mcp_<source>.server.build_server()` calls.

    Returns a plain `FastMCP` instance with no transport wired in (ADR-0002) — tool
    and prompt registration is each source package's job, built in Phase 1/2/3.
    """
    return FastMCP(name=name, instructions=instructions, **kwargs)


def tool_deadline_for(tool_name: str, settings: CommonSettings | None = None) -> float:
    """`MCP_TOOL_DEADLINE_<TOOL>` overrides `MCP_TOOL_DEADLINE` for one specific tool
    (ADR-0006 A2) — e.g. `cloudwatch_run_logs_insights`, `opensearch_search_dsl`."""
    settings = settings or CommonSettings()
    override_var = f"MCP_TOOL_DEADLINE_{tool_name.upper()}"
    override = os.environ.get(override_var)
    if override:
        return float(override)
    return settings.tool_deadline


def with_tool_deadline(
    tool_name: str,
    *,
    source: str,
    settings: CommonSettings | None = None,
) -> Callable[[Callable[P, Awaitable[dict[str, Any]]]], Callable[P, Awaitable[dict[str, Any]]]]:
    """Decorator applying the per-tool-call deadline and exception→envelope mapping.

    Every source package wraps its `@mcp.tool()` functions with this before
    registering them. On success, the wrapped function's own return value (already a
    `ToolResult`-shaped dict) passes through unchanged. On timeout or any exception,
    returns an `ErrorEnvelope` dict instead of raising — the caller (MCP SDK) always
    gets a well-formed tool result.
    """

    def decorator(
        func: Callable[P, Awaitable[dict[str, Any]]],
    ) -> Callable[P, Awaitable[dict[str, Any]]]:
        @functools.wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> dict[str, Any]:
            deadline = tool_deadline_for(tool_name, settings)
            try:
                async with asyncio.timeout(deadline):
                    return await func(*args, **kwargs)
            except TimeoutError:
                error = ToolError(
                    ErrorCode.UPSTREAM_TIMEOUT,
                    f"Tool '{tool_name}' vượt deadline {deadline}s.",
                    source,
                    True,
                    details={"hint": "kiểm tra VPN/kết nối nội bộ", "tool": tool_name},
                )
                return to_error_envelope(error)
            except ToolError as exc:
                return to_error_envelope(exc)
            except Exception as exc:  # noqa: BLE001 - intentional: the tool boundary must never leak a raw traceback
                mapped = map_exception_to_tool_error(exc, source=source)
                return to_error_envelope(mapped)

        return wrapper

    return decorator


class BoundedExecutor:
    """A `ThreadPoolExecutor` with a hard cap on total in-flight work.

    `max_queue` defaults to `max_workers` — i.e. there is no extra queueing beyond one
    unit of work per worker thread; a submission past capacity fails immediately
    rather than waiting (ADR-0006 A1 / R16).
    """

    def __init__(self, max_workers: int = 4, max_queue: int | None = None) -> None:
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._capacity = max_queue if max_queue is not None else max_workers
        self._semaphore = threading.Semaphore(self._capacity)

    async def run(
        self,
        func: Callable[..., T],
        *args: Any,
        source: str,
        host: str | None = None,
        **kwargs: Any,
    ) -> T:
        acquired = self._semaphore.acquire(blocking=False)
        if not acquired:
            raise ToolError(
                ErrorCode.UPSTREAM_UNAVAILABLE,
                "Hàng đợi thread pool nội bộ đã đầy.",
                source,
                True,
                details={
                    "host": host,
                    "hint": f"đang có call tới {host or 'nguồn'} bị treo",
                },
            )
        loop = asyncio.get_running_loop()

        def worker() -> T:
            # The slot is freed when the *thread* finishes, not when the awaiting coroutine
            # is cancelled: `asyncio.timeout` cannot stop a hung SDK call, so releasing on
            # cancellation would let later calls queue silently behind hung threads (R16).
            try:
                return func(*args, **kwargs)
            finally:
                self._semaphore.release()

        try:
            future = loop.run_in_executor(self._executor, worker)
        except BaseException:
            self._semaphore.release()  # the worker never started
            raise
        return await future

    def shutdown(self, wait: bool = True) -> None:
        self._executor.shutdown(wait=wait)


async def run_credential_check(
    check: CredentialCheck,
    *,
    settings: CommonSettings,
    server_name: str,
    logger: logging.LoggerAdapter | None = None,
) -> None:
    """Run the startup read-only credential self-check; refuse to serve on failure.

    The only escape is `MCP_ALLOW_UNVERIFIED_CREDENTIALS=true`, which logs one WARN
    per startup and proceeds anyway (ADR-0003 A1).
    """
    logger = logger or get_logger(server_name)
    try:
        result = check()
        if inspect.isawaitable(result):
            result = await result
    except Exception as exc:  # noqa: BLE001 - any check failure means "not verified"
        logger.error(
            "startup credential check raised an exception",
            extra={"tool": None, "error_code": ErrorCode.SOURCE_MISCONFIGURED.value},
        )
        result = False
        check_error: Exception | None = exc
    else:
        check_error = None

    if result:
        return

    if settings.allow_unverified_credentials:
        logger.warning(
            f"{server_name}: startup credential check failed or was skipped, but "
            "MCP_ALLOW_UNVERIFIED_CREDENTIALS=true — serving anyway.",
        )
        return

    raise SourceMisconfiguredError(
        ["MCP_ALLOW_UNVERIFIED_CREDENTIALS"], source=server_name
    ) from check_error


async def serve(
    build_server_fn: Callable[[], FastMCP],
    *,
    server_name: str,
    credential_check: CredentialCheck | None = None,
    settings: CommonSettings | None = None,
) -> None:
    """Fail-closed startup sequence, then hand off to the MCP SDK's stdio transport.

    Order matters: transport check (cheapest, no I/O) → logging/stdout guard →
    credential gate (ADR-0003 A1) → build the server → serve. Nothing here ever
    starts listening if an earlier step fails.
    """
    settings = settings or CommonSettings()

    if settings.transport != "stdio":
        raise SourceMisconfiguredError(
            [f"MCP_TRANSPORT={settings.transport} (v1 only supports 'stdio', ADR-0002)"],
            source=server_name,
        )

    logger = setup_logging(server_name, level=settings.log_level, log_file=settings.log_file)
    install_stdout_guard()

    if credential_check is not None:
        await run_credential_check(
            credential_check, settings=settings, server_name=server_name, logger=logger
        )

    mcp_server = build_server_fn()
    await mcp_server.run_stdio_async()
