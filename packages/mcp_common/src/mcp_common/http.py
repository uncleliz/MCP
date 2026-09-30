"""T-010: the one shared HTTP client — timeout budget (ADR-0006) + the transport-level
read-only assertion (ADR-0003 A2).

Two independent pieces:

* :func:`build_client` builds the single `httpx.AsyncClient` every HTTP-based server
  uses. No package may construct its own `AsyncClient` — this is the only place a
  timeout is wired in, and the only place the transport assertion is installed.
* :func:`request_with_retry` implements the ADR-0006 A2 budget:
  `connect=3s / read=7s / 2 attempts (1 retry) / 1s backoff` ⇒
  `2 * (3 + 7) + 1 = 21s < MCP_TOOL_DEADLINE (25s)`. Only `GET`/`HEAD` (idempotent)
  requests are retried, and only for `ConnectError`/`ReadTimeout`/429/502/503/504
  (ADR-0006 Decision). `Retry-After` is clamped to the remaining budget (ADR-0006 A3):
  if there isn't enough budget left to honor it, this returns `rate_limited`
  immediately instead of sleeping past the deadline.
"""

from __future__ import annotations

import asyncio
import fnmatch
import time
from collections.abc import Mapping, Sequence
from typing import Any

import httpx

from mcp_common.config import CommonSettings
from mcp_common.errors import ErrorCode, NotPermittedError, ToolError, map_exception_to_tool_error

__all__ = [
    "TransportAllowlistEntry",
    "DEFAULT_TRANSPORT_ALLOWLIST",
    "assert_transport_allowed",
    "build_client",
    "request_with_retry",
]

# (host_pattern, method, path_pattern) — fnmatch globs. Only non-GET/HEAD requests are
# checked against this; everything GET/HEAD is always allowed (ADR-0003 A2).
TransportAllowlistEntry = tuple[str, str, str]

# The only entries today: OpenSearch's `_search`/`_count` are POST-with-body endpoints
# that are still read-only (ADR-0003 A2 note in architecture.md NFR-001 mapping).
DEFAULT_TRANSPORT_ALLOWLIST: tuple[TransportAllowlistEntry, ...] = (
    ("*", "POST", "*/_search"),
    ("*", "POST", "*/_search/*"),
    ("*", "POST", "*/_count"),
)

_RETRYABLE_STATUS_CODES = frozenset({429, 502, 503, 504})
_IDEMPOTENT_METHODS = frozenset({"GET", "HEAD"})


def assert_transport_allowed(
    method: str,
    url: httpx.URL,
    allowlist: Sequence[TransportAllowlistEntry] = DEFAULT_TRANSPORT_ALLOWLIST,
) -> None:
    """Raise :class:`NotPermittedError` unless `method` is GET/HEAD or explicitly
    allowlisted for this `(host, method, path)` (ADR-0003 A2)."""
    method_upper = method.upper()
    if method_upper in _IDEMPOTENT_METHODS:
        return

    host = url.host or ""
    path = url.path or ""
    for host_pattern, allowed_method, path_pattern in allowlist:
        if allowed_method.upper() != method_upper:
            continue
        if fnmatch.fnmatch(host, host_pattern) and fnmatch.fnmatch(path, path_pattern):
            return

    raise NotPermittedError(
        f"{method_upper} {url} is not GET/HEAD and is not in the transport allowlist",
        source="none",
        operation=f"{method_upper} {path}",
        allowlist=[f"{h} {m} {p}" for h, m, p in allowlist],
    )


def build_client(
    *,
    settings: CommonSettings | None = None,
    allowlist: Sequence[TransportAllowlistEntry] = DEFAULT_TRANSPORT_ALLOWLIST,
    **client_kwargs: Any,
) -> httpx.AsyncClient:
    """Build the one `httpx.AsyncClient` a server should ever construct.

    Always passes an explicit `httpx.Timeout` (ADR-0006 Decision: no package may rely
    on httpx's default of "no timeout") and installs the transport read-only
    assertion as a request event hook, so it applies no matter which code path issues
    the request.
    """
    settings = settings or CommonSettings()
    timeout = httpx.Timeout(
        connect=settings.http_connect_timeout,
        read=settings.http_read_timeout,
        write=settings.http_write_timeout,
        pool=settings.http_pool_timeout,
    )

    async def _assert_readonly_transport(request: httpx.Request) -> None:
        assert_transport_allowed(request.method, request.url, allowlist)

    event_hooks = dict(client_kwargs.pop("event_hooks", {}) or {})
    event_hooks.setdefault("request", [])
    event_hooks["request"] = [*event_hooks["request"], _assert_readonly_transport]

    return httpx.AsyncClient(timeout=timeout, event_hooks=event_hooks, **client_kwargs)


def _parse_retry_after(response: httpx.Response) -> int | None:
    raw = response.headers.get("Retry-After")
    if raw and raw.isdigit():
        return int(raw)
    return None


def _remaining_budget(start: float, deadline_s: float) -> float:
    return deadline_s - (time.monotonic() - start)


async def _sleep_backoff(backoff_base: float, start: float, deadline_s: float) -> None:
    remaining_for_sleep = max(_remaining_budget(start, deadline_s) - 1.0, 0.0)
    sleep_s = min(backoff_base, remaining_for_sleep)
    if sleep_s > 0:
        await asyncio.sleep(sleep_s)


def _clamp_retry_after(retry_after_s: int, start: float, deadline_s: float) -> float | None:
    """`sleep = min(retry_after, remaining_budget - 1s)` (ADR-0006 A3). `None` means
    there is no budget left to honor it at all — caller must fail fast instead."""
    remaining_for_sleep = _remaining_budget(start, deadline_s) - 1.0
    if remaining_for_sleep <= 0:
        return None
    return min(float(retry_after_s), remaining_for_sleep)


async def request_with_retry(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    source: str,
    host: str | None = None,
    max_retries: int | None = None,
    backoff_base: float | None = None,
    deadline_s: float | None = None,
    settings: CommonSettings | None = None,
    headers: Mapping[str, str] | None = None,
    **kwargs: Any,
) -> httpx.Response:
    """Issue one HTTP request with the ADR-0006 retry/backoff/deadline policy.

    Raises `mcp_common.errors.ToolError` (never a raw httpx exception) on failure —
    `upstream_timeout`/`upstream_unavailable`/`upstream_error`/`rate_limited` per the
    mapping table in `mcp_common.errors`.
    """
    resolved_settings = settings or CommonSettings()
    max_retries = resolved_settings.http_max_retries if max_retries is None else max_retries
    backoff_base = resolved_settings.http_backoff_base if backoff_base is None else backoff_base
    deadline_s = resolved_settings.tool_deadline if deadline_s is None else deadline_s
    method_upper = method.upper()
    retryable_request = method_upper in _IDEMPOTENT_METHODS
    attempts = (max_retries + 1) if retryable_request else 1
    resolved_host = host or httpx.URL(url).host

    start = time.monotonic()
    last_network_exc: Exception | None = None

    for attempt in range(1, attempts + 1):
        try:
            response = await client.request(method, url, headers=headers, **kwargs)
        except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError) as exc:
            last_network_exc = exc
            if attempt >= attempts:
                raise map_exception_to_tool_error(exc, source=source, host=resolved_host) from exc
            await _sleep_backoff(backoff_base, start, deadline_s)
            continue

        if response.status_code == 429:
            retry_after = _parse_retry_after(response)
            if attempt >= attempts:
                status_exc = httpx.HTTPStatusError(
                    "429", request=response.request, response=response
                )
                raise map_exception_to_tool_error(status_exc, source=source, host=resolved_host)
            clamped = _clamp_retry_after(retry_after or 0, start, deadline_s)
            if clamped is None:
                raise ToolError(
                    ErrorCode.RATE_LIMITED,
                    "Rate limited và không còn budget để chờ Retry-After.",
                    source,
                    True,
                    retry_after_s=retry_after,
                    details={"host": resolved_host},
                )
            await asyncio.sleep(clamped)
            continue

        if (
            response.status_code in _RETRYABLE_STATUS_CODES
            and retryable_request
            and attempt < attempts
        ):
            await _sleep_backoff(backoff_base, start, deadline_s)
            continue

        if response.status_code >= 400:
            status_exc = httpx.HTTPStatusError(
                f"{response.status_code}", request=response.request, response=response
            )
            raise map_exception_to_tool_error(status_exc, source=source, host=resolved_host)

        return response

    if last_network_exc is not None:  # pragma: no cover - defensive, loop always returns/raises
        raise map_exception_to_tool_error(last_network_exc, source=source, host=resolved_host)
    # pragma: no cover - defensive, loop always returns or raises above
    raise ToolError(ErrorCode.INTERNAL, "retry loop exited unexpectedly", source, False)
