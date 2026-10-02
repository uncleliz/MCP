"""OpenSearch transport layer (ADR-0008): `opensearch-py` (async) behind read-only guards.

`client.py` is the layer `mcp-ingest`'s OpenSearch connector reuses (ADR-0012 A4), so every
guard lives here and not in `read_api.py`:

* layer 2a — :class:`ReadOnlyTransport` refuses, before any I/O, every request that is not
  one of the allowlisted endpoints (`GET /`, `_cat/indices`, `_mapping`, `_search`, `_count`
  and the security `authinfo` used by the startup check). `scroll` and point-in-time create
  server-side state, so they are refused as well (ADR-0008 A2);
* layer 2b — :meth:`OpenSearchClient.search` walks the *whole* request body and refuses the
  constructs in :data:`FORBIDDEN_KEYS` at any depth (`script`, `scripted_metric`,
  `runtime_mappings`, stored-script `id`, terms-lookup, ...), and any top-level key outside
  :data:`ALLOWED_TOP_LEVEL_KEYS`;
* layer 3 — the startup check (:meth:`OpenSearchClient.verify_credentials`) reads the roles of
  the credential from the security plugin and refuses write-capable roles (ADR-0003 A1).

Timeouts (ADR-0006 A2): cheap calls use a 7s total per-request timeout; `search`/`count`
use `timeout_s + 1` (default 21s < the 25s tool deadline). One retry on connection errors,
none on timeouts (retrying a slow query would blow the deadline).
"""

from __future__ import annotations

import fnmatch
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from mcp_common.config import CommonSettings
from mcp_common.egress import check_egress
from mcp_common.errors import (
    ErrorCode,
    NotPermittedError,
    ToolError,
    map_exception_to_tool_error,
)
from mcp_common.readonly import enforce
from mcp_common.redact import register_secret
from opensearchpy import AsyncOpenSearch
from opensearchpy import exceptions as osx
from opensearchpy._async.transport import AsyncTransport

from mcp_opensearch.settings import Settings

__all__ = [
    "ALLOWED_OPERATIONS",
    "ALLOWED_TOP_LEVEL_KEYS",
    "CredentialReport",
    "FORBIDDEN_KEYS",
    "OpenSearchClient",
    "ReadOnlyTransport",
    "SOURCE",
    "assert_body_allowed",
    "assert_request_allowed",
    "not_permitted",
    "to_tool_error",
]

SOURCE = "opensearch"
CHEAP_REQUEST_TIMEOUT_S = 7.0

OP_INFO = "GET /"
OP_CAT_INDICES = "GET /_cat/indices/{pattern}"
OP_MAPPING = "GET /{index}/_mapping"
OP_SEARCH = "POST /{index}/_search"
OP_COUNT = "POST /{index}/_count"
# Startup credential check only — never exposed as a tool.
OP_AUTHINFO = "GET /_plugins/_security/authinfo"

ALLOWED_OPERATIONS: tuple[str, ...] = (
    OP_INFO,
    OP_CAT_INDICES,
    OP_MAPPING,
    OP_SEARCH,
    OP_COUNT,
    OP_AUTHINFO,
)

# (method(s), path glob) the transport lets through. Everything else is `not_permitted`.
_TRANSPORT_ALLOWLIST: tuple[tuple[frozenset[str], str], ...] = (
    (frozenset({"GET", "HEAD"}), "/"),
    (frozenset({"GET"}), "/_cat/indices"),
    (frozenset({"GET"}), "/_cat/indices/*"),
    (frozenset({"GET"}), "/*/_mapping"),
    (frozenset({"POST", "GET"}), "/*/_search"),
    (frozenset({"POST", "GET"}), "/*/_count"),
    (frozenset({"GET"}), "/_plugins/_security/authinfo"),
)
_STATEFUL_PATH_MARKERS = ("_search/scroll", "/_pit", "point_in_time")

# Body guard (contract `opensearch_search_dsl` x-guardrails + ADR-0008 A2).
ALLOWED_TOP_LEVEL_KEYS: tuple[str, ...] = (
    "query",
    "sort",
    "_source",
    "aggs",
    "highlight",
    "size",
    "from",
    "search_after",
    "track_total_hits",
    "timeout",
)
FORBIDDEN_KEYS = frozenset(
    {
        "script", "script_score", "script_fields", "scripted_metric", "bucket_script",
        "bucket_selector", "_script", "runtime_mappings", "id",
        "scroll", "pit", "point_in_time", "profile", "search_template", "indices_boost",
    }
)  # fmt: skip


def not_permitted(operation: str, allowlist: Sequence[str]) -> NotPermittedError:
    return NotPermittedError(
        f"'{operation}' không nằm trong allowlist read-only của OpenSearch.",
        source=SOURCE,
        operation=operation,
        allowlist=list(allowlist),
    )


def assert_request_allowed(method: str, url: str, params: Mapping[str, Any] | None) -> None:
    """Endpoint-level guard used by :class:`ReadOnlyTransport` (and unit-tested directly)."""
    method = method.upper()
    path = url.split("?", 1)[0] or "/"
    if any(marker in path for marker in _STATEFUL_PATH_MARKERS) or (params or {}).get("scroll"):
        raise not_permitted(f"{method} {path}", ALLOWED_OPERATIONS)
    for methods, pattern in _TRANSPORT_ALLOWLIST:
        if method in methods and fnmatch.fnmatchcase(path, pattern):
            return
    raise not_permitted(f"{method} {path}", ALLOWED_OPERATIONS)


class ReadOnlyTransport(AsyncTransport):
    """`AsyncTransport` that refuses non-allowlisted requests before any I/O.

    When ``MCP_EGRESS_ALLOWLIST`` enforcement is turned on for this transport (the
    ingest-pull path sets ``enforce_egress=True`` on the client — CHG-003, ADR-0023 §6a),
    every request is also routed through the one ``mcp_common.egress.check_egress``
    default-deny choke point (L-001) before I/O: an OpenSearch host not on the allow-list is
    refused fail-closed. The MCP server leaves it off and keeps reaching its own upstream.
    """

    _enforce_egress: bool = False

    async def perform_request(  # type: ignore[override]
        self,
        method: str,
        url: str,
        params: Mapping[str, Any] | None = None,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        assert_request_allowed(method, url, params)
        if self._enforce_egress:
            check_egress(self._egress_target())
        return await super().perform_request(method, url, params, *args, **kwargs)

    def _egress_target(self) -> str:
        """The upstream host this transport dials (from the configured connections)."""
        for connection in self.connection_pool.connections:
            host = getattr(connection, "host", None)
            if host:
                return str(host)
        return ""


def assert_body_allowed(body: Mapping[str, Any]) -> None:
    """Refuse forbidden constructs at *any* depth and unknown top-level keys."""
    allowlist = list(ALLOWED_TOP_LEVEL_KEYS)
    for key in body:
        if key not in ALLOWED_TOP_LEVEL_KEYS:
            raise not_permitted(f"body.{key}", allowlist)

    def walk(node: Any, path: str) -> None:
        if isinstance(node, Mapping):
            for key, value in node.items():
                here = f"{path}.{key}"
                if key in FORBIDDEN_KEYS:
                    raise not_permitted(here, allowlist)
                if key == "terms" and isinstance(value, Mapping):
                    for field_name, spec in value.items():
                        if isinstance(spec, Mapping) and "index" in spec:  # terms lookup
                            raise not_permitted(f"{here}.{field_name}.index", allowlist)
                walk(value, here)
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{path}[{index}]")

    walk(body, "body")


def to_tool_error(exc: Exception, host: str | None) -> ToolError:
    """Classify an `opensearch-py` exception into the contract's error taxonomy."""
    hint = f"kiểm tra VPN/kết nối nội bộ tới {host or 'nguồn'}"
    if isinstance(exc, osx.ConnectionTimeout):
        return ToolError(
            ErrorCode.UPSTREAM_TIMEOUT, "Timeout khi gọi OpenSearch.", SOURCE, True,
            details={"host": host, "hint": hint},
        )  # fmt: skip
    if isinstance(exc, osx.ConnectionError):  # includes SSLError
        return ToolError(
            ErrorCode.UPSTREAM_UNAVAILABLE, "Không kết nối được tới OpenSearch.", SOURCE, True,
            details={"host": host, "hint": hint},
        )  # fmt: skip
    if isinstance(exc, osx.AuthenticationException):
        return ToolError(ErrorCode.UNAUTHORIZED, "OpenSearch trả 401.", SOURCE, False)
    if isinstance(exc, osx.AuthorizationException):
        return ToolError(ErrorCode.FORBIDDEN, "OpenSearch trả 403.", SOURCE, False)
    if isinstance(exc, osx.NotFoundError):
        return ToolError(
            ErrorCode.UPSTREAM_ERROR, "OpenSearch trả 404.", SOURCE, False,
            details={"upstream_status": 404},
        )  # fmt: skip
    if isinstance(exc, osx.RequestError):
        return ToolError(
            ErrorCode.INVALID_INPUT,
            f"query: OpenSearch từ chối truy vấn ({str(exc.error)[:200]}).",
            SOURCE,
            False,
            details={"field": "query", "upstream_status": 400},
        )
    if isinstance(exc, osx.TransportError):
        status = exc.status_code if isinstance(exc.status_code, int) else None
        if status == 429:
            return ToolError(
                ErrorCode.RATE_LIMITED, "OpenSearch trả 429.", SOURCE, True,
                details={"upstream_status": 429},
            )  # fmt: skip
        if status in (502, 503, 504):
            return ToolError(
                ErrorCode.UPSTREAM_UNAVAILABLE, f"OpenSearch trả {status}.", SOURCE, True,
                details={"upstream_status": status, "host": host, "hint": hint},
            )  # fmt: skip
        return ToolError(
            ErrorCode.UPSTREAM_ERROR, f"OpenSearch trả {exc.status_code}.", SOURCE, True,
            details={"upstream_status": status},
        )  # fmt: skip
    return map_exception_to_tool_error(exc, source=SOURCE, host=host)


@dataclass
class CredentialReport:
    ok: bool
    reasons: list[str] = field(default_factory=list)
    user: str | None = None


class OpenSearchClient:
    def __init__(
        self,
        settings: Settings,
        *,
        common: CommonSettings | None = None,
        os_client: Any | None = None,
        enforce_egress: bool = False,
    ) -> None:
        self._settings = settings
        self._common = common or CommonSettings()
        self._host = settings.host_list[0].split("://", 1)[-1].split(":", 1)[0]
        # E-mcp-data-platform-009 (FR-025/NFR-014): register the configured password for
        # value-based scrubbing at the single client-construction seam, so an opaque
        # credential the shape/label/entropy passes cannot recognise is still redacted
        # from any outbound error/result/log. Covers the live server and the mcp_ingest
        # OpenSearch connector. Additive; a no-op when no password is configured.
        if settings.password is not None:
            register_secret(settings.password.get_secret_value())
        self._os = os_client or self._build_sdk_client(settings, enforce_egress=enforce_egress)

    @staticmethod
    def _build_sdk_client(settings: Settings, *, enforce_egress: bool = False) -> AsyncOpenSearch:
        auth = (
            (settings.username, settings.password.get_secret_value())
            if settings.username and settings.password
            else None
        )
        if enforce_egress:
            # One choke point (L-001): a transport subclass that also runs check_egress.
            transport_class: type[AsyncTransport] = type(
                "EgressGuardedReadOnlyTransport",
                (ReadOnlyTransport,),
                {"_enforce_egress": True},
            )
        else:
            transport_class = ReadOnlyTransport
        return AsyncOpenSearch(
            hosts=settings.host_list,
            http_auth=auth,
            use_ssl=settings.host_list[0].startswith("https://"),
            verify_certs=settings.verify_certs,
            transport_class=transport_class,
            timeout=CHEAP_REQUEST_TIMEOUT_S,
            max_retries=1,
            retry_on_timeout=False,
        )

    async def aclose(self) -> None:
        await self._os.close()

    async def _guard(self, coro: Any) -> Any:
        try:
            return await coro
        except ToolError:
            raise
        except Exception as exc:  # noqa: BLE001 - mapped to the contract taxonomy
            raise to_tool_error(exc, self._host) from exc

    # -- typed operations (no tool bounds here; see module docstring) -------------------------

    async def info(self) -> Any:
        enforce(ALLOWED_OPERATIONS, OP_INFO, source=SOURCE)
        return await self._guard(self._os.info())

    async def authinfo(self) -> Any:
        enforce(ALLOWED_OPERATIONS, OP_AUTHINFO, source=SOURCE)
        return await self._guard(
            self._os.transport.perform_request("GET", "/_plugins/_security/authinfo")
        )

    async def cat_indices(self, pattern: str) -> Any:
        enforce(ALLOWED_OPERATIONS, OP_CAT_INDICES, source=SOURCE)
        params = {
            "format": "json",
            "h": "index,health,status,docs.count,store.size,creation.date.string",
            "s": "index",
        }
        return await self._guard(self._os.cat.indices(index=pattern, params=params))

    async def get_mapping(self, index: str) -> Any:
        enforce(ALLOWED_OPERATIONS, OP_MAPPING, source=SOURCE)
        return await self._guard(self._os.indices.get_mapping(index=index))

    async def search(
        self, index: str, body: Mapping[str, Any], *, request_timeout: float | None = None
    ) -> Any:
        enforce(ALLOWED_OPERATIONS, OP_SEARCH, source=SOURCE)
        assert_body_allowed(body)
        params = {"request_timeout": request_timeout} if request_timeout else {}
        return await self._guard(self._os.search(index=index, body=dict(body), params=params))

    async def count(
        self, index: str, body: Mapping[str, Any], *, request_timeout: float | None = None
    ) -> Any:
        enforce(ALLOWED_OPERATIONS, OP_COUNT, source=SOURCE)
        assert_body_allowed(body)
        params = {"request_timeout": request_timeout} if request_timeout else {}
        return await self._guard(self._os.count(index=index, body=dict(body), params=params))

    # -- startup credential check (ADR-0003 A1, ADR-0008 A3) --------------------------------------

    async def verify_credentials(self) -> CredentialReport:
        """Connectivity + auth (`GET /`), then the credential's security-plugin roles."""
        try:
            await self.info()
        except ToolError as exc:
            hint = exc.details.get("hint")
            reason = f"{exc.code.value}: {exc.message}"
            return CredentialReport(False, [f"{reason} ({hint})" if hint else reason])
        try:
            authinfo = await self.authinfo()
        except ToolError as exc:
            return CredentialReport(False, [_cannot_verify(f"{exc.code.value}")])
        if not isinstance(authinfo, dict) or not authinfo.get("roles"):
            return CredentialReport(False, [_cannot_verify("authinfo reports no roles")])
        user = str(authinfo.get("user_name") or "") or None
        roles = {str(r) for r in authinfo["roles"]} | {
            str(r) for r in authinfo.get("backend_roles") or []
        }
        bad = sorted(roles & self._settings.denied_roles)
        if bad:
            return CredentialReport(
                False,
                [
                    f"user '{user}' maps to write/admin-capable roles: {', '.join(bad)} "
                    "(use a role limited to read/search/count; see MCP_OPENSEARCH_DENY_ROLES)"
                ],
                user,
            )
        return CredentialReport(True, [], user)

    async def credential_check(self) -> bool:
        """`mcp_common.runtime.serve(credential_check=...)` hook."""
        return (await self.verify_credentials()).ok


def _cannot_verify(why: str) -> str:
    return (
        f"cannot verify read-only: could not read this user's roles from the security plugin "
        f"({why}); grant access to /_plugins/_security/authinfo or set "
        "MCP_ALLOW_UNVERIFIED_CREDENTIALS=true"
    )
