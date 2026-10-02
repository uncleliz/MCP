"""Confluence transport layer (ADR-0007): thin httpx client over the Cloud REST API.

`client.py` is the layer `mcp-ingest`'s connector reuses (ADR-0007 A3): it enforces the
read-only allowlist and timeouts but deliberately applies **no tool bounds** (limit caps,
max chars) — those live in `read_api.py`.

Read-only guarantees in this file:
* every call goes through :meth:`ConfluenceClient.get`, which checks the operation
  against :data:`ALLOWED_OPERATIONS` (GET only);
* `body.export_view` is rejected in code (ADR-0007 A1: it renders macros server-side,
  an observable side effect on the source);
* the underlying `httpx.AsyncClient` is built by `mcp_common.http.build_client`, whose
  transport hook refuses any non-GET/HEAD request (ADR-0003 A2).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

import httpx
from mcp_common.config import CommonSettings
from mcp_common.errors import ErrorCode, NotPermittedError, ToolError
from mcp_common.http import build_client, request_with_retry
from mcp_common.readonly import enforce
from mcp_common.redact import register_secret

from mcp_confluence.settings import Settings

__all__ = ["ALLOWED_OPERATIONS", "ConfluenceClient", "CredentialReport", "SOURCE"]

SOURCE = "confluence"

OP_SEARCH = "GET /rest/api/content/search"
OP_CONTENT = "GET /rest/api/content/{id}"
OP_SPACES = "GET /rest/api/space"
OP_CHILDREN = "GET /rest/api/content/{id}/child/page"
# Startup credential check only — never exposed as a tool (ADR-0007 A2).
OP_CURRENT_USER = "GET /rest/api/user/current"

ALLOWED_OPERATIONS: tuple[str, ...] = (
    OP_SEARCH,
    OP_CONTENT,
    OP_SPACES,
    OP_CHILDREN,
    OP_CURRENT_USER,
)

_FORBIDDEN_EXPAND = ("export_view",)
# Operations that prove an account is NOT read-only (ADR-0003 A1 / ADR-0007 A2).
_WRITE_OPERATIONS = frozenset(
    {"create", "update", "delete", "administer", "restrict_content", "copy", "move", "archive"}
)


@dataclass
class CredentialReport:
    ok: bool
    reasons: list[str] = field(default_factory=list)


class ConfluenceClient:
    def __init__(
        self,
        settings: Settings,
        *,
        common: CommonSettings | None = None,
        http: httpx.AsyncClient | None = None,
        enforce_egress: bool = False,
    ) -> None:
        self._settings = settings
        self._common = common or CommonSettings()
        self._host = httpx.URL(settings.base_url).host
        # E-mcp-data-platform-009 (FR-025/NFR-014): register the configured credential for
        # value-based scrubbing at the single client-construction seam, so an opaque token
        # that the shape/label/entropy passes cannot recognise is still redacted from any
        # outbound error/result/log before any request can fail. Covers both the live MCP
        # server path and the mcp_ingest connector path (both build this client). Additive.
        register_secret(settings.api_token.get_secret_value())
        self.http = http or build_client(
            settings=self._common,
            enforce_egress=enforce_egress,
            auth=httpx.BasicAuth(settings.email, settings.api_token.get_secret_value()),
            headers={"Accept": "application/json"},
        )

    @property
    def base_url(self) -> str:
        return self._settings.base_url

    async def aclose(self) -> None:
        await self.http.aclose()

    # -- the single choke point ------------------------------------------------------

    async def get(
        self,
        operation: str,
        path_params: Mapping[str, str] | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        enforce(ALLOWED_OPERATIONS, operation, source=SOURCE)
        expand = str((params or {}).get("expand", ""))
        for forbidden in _FORBIDDEN_EXPAND:
            if forbidden in expand.lower():
                raise NotPermittedError(
                    f"expand '{forbidden}' is not allowed "
                    "(renders macros server-side, ADR-0007 A1).",
                    source=SOURCE,
                    operation=f"expand={expand}",
                    allowlist=list(ALLOWED_OPERATIONS),
                )
        quoted = {k: quote(v, safe="") for k, v in (path_params or {}).items()}
        path = operation.removeprefix("GET ").format(**quoted)
        response = await request_with_retry(
            self.http,
            "GET",
            f"{self._settings.base_url}{path}",
            source=SOURCE,
            host=self._host,
            settings=self._common,
            params=dict(params or {}),
        )
        try:
            body = response.json()
        except ValueError as exc:
            raise ToolError(
                ErrorCode.UPSTREAM_ERROR,
                "Confluence trả nội dung không phải JSON (kiểm tra MCP_CONFLUENCE_BASE_URL).",
                SOURCE,
                True,
                details={"host": self._host},
            ) from exc
        if not isinstance(body, dict):
            raise ToolError(
                ErrorCode.UPSTREAM_ERROR, "Confluence trả JSON không mong đợi.", SOURCE, True
            )
        return body

    # -- typed operations (no tool bounds here; see module docstring) -----------------

    async def search(self, cql: str, *, limit: int, start: int, expand: str) -> dict[str, Any]:
        return await self.get(
            OP_SEARCH, params={"cql": cql, "limit": limit, "start": start, "expand": expand}
        )

    async def get_content(self, page_id: str, *, expand: str) -> dict[str, Any]:
        return await self.get(OP_CONTENT, {"id": page_id}, {"expand": expand})

    async def list_spaces(self, *, limit: int, start: int) -> dict[str, Any]:
        return await self.get(OP_SPACES, params={"limit": limit, "start": start})

    async def list_children(
        self, page_id: str, *, limit: int, start: int, expand: str = "version"
    ) -> dict[str, Any]:
        return await self.get(
            OP_CHILDREN,
            {"id": page_id},
            {"limit": limit, "start": start, "expand": expand},
        )

    async def current_user(self) -> dict[str, Any]:
        return await self.get(OP_CURRENT_USER)

    # -- startup credential check ------------------------------------------------------

    async def verify_credentials(self) -> CredentialReport:
        """Prove the token authenticates AND cannot write (ADR-0003 A1, ADR-0007 A2).

        Atlassian API tokens carry the full permissions of their user, so "read-only" can
        only be a property of the account. We therefore (1) check the identity via the
        current-user endpoint, then (2) sample one page through `content/search` with
        `expand=operations` and require that none of the operations the account may
        perform on it is a write. Everything is GET; nothing is created to test this.
        Fails closed when the answer cannot be determined.
        """
        reasons: list[str] = []
        try:
            user = await self.current_user()
            if user.get("type") == "anonymous":
                return CredentialReport(False, ["token authenticates as an anonymous user"])
            sample = await self.search("type = page", limit=1, start=0, expand="operations")
        except ToolError as exc:
            hint = exc.details.get("hint")
            reason = f"{exc.code.value}: {exc.message}"
            return CredentialReport(False, [f"{reason} ({hint})" if hint else reason])

        results = sample.get("results") or []
        if not results:
            return CredentialReport(
                False, ["cannot verify read-only: no page visible to sample permissions from"]
            )
        operations = results[0].get("operations")
        if not isinstance(operations, list):
            return CredentialReport(
                False, ["cannot verify read-only: API did not report page operations"]
            )
        writes = sorted(
            f"{op.get('operation')}:{op.get('targetType')}"
            for op in operations
            if isinstance(op, dict) and op.get("operation") in _WRITE_OPERATIONS
        )
        if writes:
            reasons.append(
                "account is not read-only (use a viewer-only service account); "
                f"permitted write operations: {', '.join(writes)}"
            )
        return CredentialReport(not reasons, reasons)

    async def credential_check(self) -> bool:
        """`mcp_common.runtime.serve(credential_check=...)` hook."""
        return (await self.verify_credentials()).ok
