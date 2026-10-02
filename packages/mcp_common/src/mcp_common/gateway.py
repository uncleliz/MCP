"""T-109 (E7, ADR-0021): the in-process gateway-boundary.

Every Knowledge/Live tool-call passes through one in-process boundary that does four
things and **opens no network port** (NFR-005/NFR-012 stays intact — stdio only):

* **routing/discovery** — a unified tool surface over the Knowledge + Live servers; a
  tool name resolves to exactly one `(server, handler)` route, so there is a single
  place a call enters the platform.
* **auth-context** — the caller identity. At v1 (stdio, ADR-0016) the caller is the
  owner of the process; :class:`AuthContext` carries that identity and leaves an
  explicit per-request slot (``request_principal``) that v1.1 will fill when HTTP+SSE
  arrives — **without rewriting policy** (ADR-0021 "cấu trúc để v1.1 đổi sang
  per-request"). The per-request transport itself is **not** implemented here.
* **rate-limit** — an in-process token-bucket per routed key. When a caller exceeds
  the configured rate the gateway rejects the excess with a `rate_limited`
  :class:`~mcp_common.errors.ToolError` carrying ``retry_after_s`` (it never overloads
  the upstream), and the rejection is audited.
* **audit** — an append-only audit record per tool-call written to **stderr** (never
  stdout — stdout is the JSON-RPC transport channel, R2). Records carry tool, a
  hash of the arguments (never the raw arguments at INFO), the caller identity, the
  verdict (``routed`` / ``rate_limited`` / ``error``) and latency. Secrets are scrubbed
  by the shared stderr log formatter (R-003); full argument text is logged only at
  DEBUG, redacted.

**What this module deliberately does not do** (ADR-0021 scope / out-of-scope):

* It does not open a socket, bind a port, or start an HTTP/SSE service. There is no
  `socket`/`http.server`/`asyncio` server import in this module *by construction* —
  that is what keeps NFR-005 true, and :mod:`test_gateway_no_port` asserts it.
* It does not implement the HTTP v1.1 transport. :class:`TransportAdapter` is only a
  **seam** (a documented interface) so v1.1 can add a transport *in front of* the
  gateway without touching routing/auth/rate-limit/audit; constructing the concrete
  HTTP adapter raises :class:`NotImplementedError`.
* It does not own the permission filter (choke point #1 lives in
  ``mcp_knowledge.permission.enforce``) or the grounding gate (choke point #2). The
  gateway passes the :class:`AuthContext` *to* those; it never duplicates them.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from mcp_common.errors import ErrorCode, ToolError
from mcp_common.logging import get_logger, setup_logging

__all__ = [
    "AuthContext",
    "TokenBucket",
    "RateLimiter",
    "Route",
    "Gateway",
    "TransportAdapter",
    "StdioTransportAdapter",
    "HttpTransportAdapter",
    "GATEWAY_SOURCE",
]

#: The `source` string every gateway-originated :class:`ToolError` / audit line carries.
GATEWAY_SOURCE = "mcp-gateway"

# A tool handler is any (sync-shaped signature) async callable taking the already-parsed
# arguments plus the resolved auth-context, returning the tool result payload. The gateway
# does not care what the payload is — it only routes, limits, audits, and returns it.
ToolHandler = Callable[[Mapping[str, Any], "AuthContext"], Awaitable[Any]]

# Injectable monotonic clock (seconds). Defaults to `time.monotonic`; tests pass a
# `VirtualClock.monotonic` so rate-limit arithmetic is asserted without real waiting.
MonotonicClock = Callable[[], float]


def _default_clock() -> float:
    import time

    return time.monotonic()


@dataclass(frozen=True)
class AuthContext:
    """The caller identity a routed tool-call runs under (ADR-0021 auth-context).

    At v1 the only caller is the owner of the stdio process, so :meth:`for_process_owner`
    is the normal constructor. ``request_principal`` is the **per-request slot** reserved
    for v1.1 (HTTP+SSE, BR-004): it is populated by a future transport adapter and never
    *widens* anything on its own — downstream policy (``enforce_permission``) still
    decides access. Keeping the slot here means v1.1 changes the transport, not the policy.
    """

    #: The process owner's identity (v1). Falls back to ``"local-process-owner"``.
    process_principal: str = "local-process-owner"
    #: Reserved for v1.1 per-request identity; ``None`` at v1 (stdio, single caller).
    request_principal: str | None = None

    @classmethod
    def for_process_owner(cls) -> AuthContext:
        """Build the v1 auth-context from the OS process owner (stdio, single caller)."""
        import getpass

        try:
            owner = getpass.getuser()
        except Exception:  # pragma: no cover - exotic env with no resolvable user
            owner = "local-process-owner"
        return cls(process_principal=owner)

    @property
    def effective_principal(self) -> str:
        """The identity to audit/enforce against: the per-request one if a v1.1 transport
        set it, else the process owner. v1 always resolves to the process owner."""
        return self.request_principal or self.process_principal


@dataclass
class TokenBucket:
    """A single in-process token-bucket (ADR-0021 rate-limit).

    ``capacity`` tokens refill at ``refill_per_s`` tokens/second. Each admitted call
    consumes one token; when the bucket is empty the call is refused and
    :meth:`retry_after_s` reports the whole seconds until one token is available. Time
    comes from the injected ``clock`` so the arithmetic is testable without sleeping.
    """

    capacity: float
    refill_per_s: float
    clock: MonotonicClock = field(default=_default_clock)
    _tokens: float = field(init=False, default=0.0)
    _last: float | None = field(init=False, default=None)

    def __post_init__(self) -> None:
        if self.capacity <= 0:
            raise ValueError("TokenBucket.capacity must be > 0")
        if self.refill_per_s <= 0:
            raise ValueError("TokenBucket.refill_per_s must be > 0")
        self._tokens = float(self.capacity)

    def _refill(self) -> None:
        now = self.clock()
        if self._last is None:
            self._last = now
            return
        elapsed = now - self._last
        if elapsed <= 0:
            return
        self._last = now
        self._tokens = min(self.capacity, self._tokens + elapsed * self.refill_per_s)

    def try_consume(self) -> bool:
        """Admit one call if a token is available (consuming it); else refuse."""
        self._refill()
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return True
        return False

    def retry_after_s(self) -> int:
        """Whole seconds until the next token is available (>= 1 when empty)."""
        self._refill()
        if self._tokens >= 1.0:
            return 0
        deficit = 1.0 - self._tokens
        import math

        return max(1, math.ceil(deficit / self.refill_per_s))


class RateLimiter:
    """Per-key token-buckets (ADR-0021). The key is the routed `(server, tool)` so a
    burst against one tool cannot starve another; all buckets share one config at v1."""

    def __init__(self, capacity: float, refill_per_s: float, clock: MonotonicClock | None = None):
        self._capacity = capacity
        self._refill_per_s = refill_per_s
        self._clock = clock or _default_clock
        self._buckets: dict[str, TokenBucket] = {}

    def _bucket(self, key: str) -> TokenBucket:
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = TokenBucket(self._capacity, self._refill_per_s, self._clock)
            self._buckets[key] = bucket
        return bucket

    def check(self, key: str) -> tuple[bool, int]:
        """Return ``(allowed, retry_after_s)`` for one call against ``key``."""
        bucket = self._bucket(key)
        if bucket.try_consume():
            return True, 0
        return False, bucket.retry_after_s()


@dataclass(frozen=True)
class Route:
    """One resolved destination: a tool name maps to exactly one `(server, handler)`."""

    server: str
    tool: str
    handler: ToolHandler


class TransportAdapter(Protocol):
    """The seam a future transport plugs into **in front of** the gateway (ADR-0021).

    v1.1 (HTTP+SSE, BR-004) adds a transport by implementing this protocol and feeding
    the gateway a per-request :class:`AuthContext`; routing/auth/rate-limit/audit do not
    change. v1 ships only :class:`StdioTransportAdapter`.
    """

    @property
    def name(self) -> str: ...

    def opens_network_port(self) -> bool:
        """Must be ``False`` for any v1 transport (NFR-005/NFR-012)."""
        ...


@dataclass(frozen=True)
class StdioTransportAdapter:
    """The only v1 transport: stdio, in-process, **no** network port (NFR-005)."""

    name: str = "stdio"

    def opens_network_port(self) -> bool:
        return False


class HttpTransportAdapter:
    """v1.1 HTTP+SSE transport seam — **not implemented** (out of scope, ADR-0021).

    It exists so the shape of v1.1 is visible and the gateway's interface already
    accounts for a per-request transport, but constructing it fails loudly: v1 must
    never open a network port (NFR-005/NFR-012).
    """

    name = "http+sse"

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        raise NotImplementedError(
            "HTTP+SSE transport is deferred to v1.1 (BR-012/BR-004); v1 keeps stdio only "
            "and opens no network port (NFR-005/NFR-012, ADR-0021)."
        )


class Gateway:
    """The in-process gateway-boundary (ADR-0021). One instance fronts the registered
    Knowledge/Live tools; every call goes through :meth:`dispatch`.

    Construction wires nothing network-facing: it holds a route table, a
    :class:`RateLimiter`, and a stderr-only audit logger. The audit logger reuses
    :func:`mcp_common.logging.setup_logging`, which never attaches a stdout handler, so
    audit output is structurally stderr-only (R2/NFR-012).
    """

    def __init__(
        self,
        *,
        rate_limit_capacity: float = 60.0,
        rate_limit_refill_per_s: float = 1.0,
        clock: MonotonicClock | None = None,
        transport: TransportAdapter | None = None,
        configure_logging: bool = True,
    ) -> None:
        self._routes: dict[str, Route] = {}
        self._rate_limiter = RateLimiter(rate_limit_capacity, rate_limit_refill_per_s, clock)
        resolved_transport: TransportAdapter = (
            StdioTransportAdapter() if transport is None else transport
        )
        self._transport = resolved_transport
        if self._transport.opens_network_port():
            # Defensive: a transport that opens a port would break NFR-005; refuse it.
            raise ValueError(
                f"transport {self._transport.name!r} opens a network port — the gateway is "
                "in-process only (NFR-005/NFR-012, ADR-0021)"
            )
        if configure_logging:
            setup_logging(GATEWAY_SOURCE)
        self._log = get_logger(GATEWAY_SOURCE)

    @property
    def transport(self) -> TransportAdapter:
        return self._transport

    def register(self, server: str, tool: str, handler: ToolHandler) -> None:
        """Register one tool route. A duplicate tool name is a wiring bug — refuse it so
        routing stays unambiguous (one tool resolves to exactly one server)."""
        if tool in self._routes:
            existing = self._routes[tool].server
            raise ValueError(
                f"tool {tool!r} already routed to server {existing!r}; tool names must be "
                "unique across the unified Knowledge/Live surface"
            )
        self._routes[tool] = Route(server=server, tool=tool, handler=handler)

    def tool_surface(self) -> dict[str, str]:
        """The unified, discoverable surface: ``{tool_name: server}`` (routing/discovery)."""
        return {tool: route.server for tool, route in self._routes.items()}

    def _audit(
        self,
        *,
        tool: str,
        server: str | None,
        auth: AuthContext,
        verdict: str,
        duration_ms: int,
        error_code: str | None = None,
        arguments: Mapping[str, Any] | None = None,
    ) -> None:
        """Write one append-only audit record to stderr (never stdout).

        At INFO the arguments appear only as a stable hash (`args_hash`) — never the raw
        values, so a query/argument string cannot leak into the audit stream. The full
        (redacted) arguments are emitted only at DEBUG.
        """
        args_hash = self._hash_arguments(arguments)
        # STANDARD_LOG_FIELDS the stderr formatter emits: use `tool`, `status`, `error_code`,
        # `duration_ms`, `request_id` (carries the caller identity for the audit trail).
        self._log.info(
            f"gateway audit: {verdict} {tool} args_hash={args_hash}",
            extra={
                "tool": tool,
                "status": verdict,
                "error_code": error_code,
                "duration_ms": duration_ms,
                "request_id": auth.effective_principal,
                "upstream_host": server,
            },
        )
        if arguments:
            # DEBUG-only, redacted by the formatter; dropped entirely at INFO.
            self._log.debug(
                f"gateway audit args for {tool}: {json.dumps(arguments, default=str)}",
                extra={"tool": tool, "status": verdict},
            )

    @staticmethod
    def _hash_arguments(arguments: Mapping[str, Any] | None) -> str:
        """A stable, non-reversible fingerprint of the arguments for the audit trail."""
        if not arguments:
            return "none"
        canonical = json.dumps(arguments, sort_keys=True, default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]

    async def dispatch(
        self,
        tool: str,
        arguments: Mapping[str, Any] | None = None,
        auth: AuthContext | None = None,
    ) -> Any:
        """Route one tool-call: resolve → rate-limit → run → audit → return.

        Raises :class:`ToolError` with ``code=invalid_input`` for an unknown tool,
        ``code=rate_limited`` (with ``retry_after_s``) when the bucket is empty, or the
        tool's own :class:`ToolError`; every outcome (routed / rate_limited / error) is
        audited to stderr.
        """
        import time

        arguments = arguments or {}
        auth = auth or AuthContext.for_process_owner()
        start = time.monotonic()

        route = self._routes.get(tool)
        if route is None:
            duration_ms = int((time.monotonic() - start) * 1000)
            self._audit(
                tool=tool,
                server=None,
                auth=auth,
                verdict="error",
                duration_ms=duration_ms,
                error_code=ErrorCode.INVALID_INPUT.value,
                arguments=arguments,
            )
            raise ToolError(
                ErrorCode.INVALID_INPUT,
                f"Unknown tool {tool!r}: not in the gateway's routed surface.",
                GATEWAY_SOURCE,
                retryable=False,
                details={"tool": tool},
                request_id=auth.effective_principal,
            )

        key = f"{route.server}:{route.tool}"
        allowed, retry_after = self._rate_limiter.check(key)
        if not allowed:
            duration_ms = int((time.monotonic() - start) * 1000)
            self._audit(
                tool=tool,
                server=route.server,
                auth=auth,
                verdict="rate_limited",
                duration_ms=duration_ms,
                error_code=ErrorCode.RATE_LIMITED.value,
                arguments=arguments,
            )
            raise ToolError(
                ErrorCode.RATE_LIMITED,
                "Gateway rate-limit exceeded; retry after the indicated interval.",
                GATEWAY_SOURCE,
                retryable=True,
                retry_after_s=retry_after,
                details={"tool": tool, "server": route.server},
                request_id=auth.effective_principal,
            )

        try:
            result = await route.handler(arguments, auth)
        except ToolError as exc:
            duration_ms = int((time.monotonic() - start) * 1000)
            self._audit(
                tool=tool,
                server=route.server,
                auth=auth,
                verdict="error",
                duration_ms=duration_ms,
                error_code=exc.code.value,
                arguments=arguments,
            )
            raise

        duration_ms = int((time.monotonic() - start) * 1000)
        self._audit(
            tool=tool,
            server=route.server,
            auth=auth,
            verdict="routed",
            duration_ms=duration_ms,
            arguments=arguments,
        )
        return result
