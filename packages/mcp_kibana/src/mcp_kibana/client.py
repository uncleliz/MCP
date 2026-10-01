"""Kibana transport layer (ADR-0008): a thin httpx client over the saved-objects API.

Read-only guarantees in this file:

* every call goes through :meth:`KibanaClient.get`, which checks the operation against
  :data:`ALLOWED_OPERATIONS` — exactly three GET endpoints;
* the `httpx.AsyncClient` comes from `mcp_common.http.build_client`, whose transport hook
  refuses any non-GET/HEAD request (ADR-0003 A2);
* the xsrf header that Kibana demands for write methods is never sent: nothing here writes.

The startup check (`GET /api/status`) proves the credential works and Kibana is available;
Kibana exposes no allowlisted endpoint that proves the *privileges* are read-only, so the
operator must still give `mcp-kibana` a role with read-only saved-object access.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

import httpx
from mcp_common.config import CommonSettings
from mcp_common.errors import ErrorCode, ToolError
from mcp_common.http import build_client, request_with_retry
from mcp_common.readonly import enforce
from mcp_common.tooling import invalid_input

from mcp_kibana.settings import Settings

__all__ = [
    "ALLOWED_OPERATIONS",
    "CredentialReport",
    "KibanaClient",
    "OP_FIND",
    "OP_GET",
    "OP_STATUS",
    "SOURCE",
]

SOURCE = "kibana"

OP_FIND = "GET /api/saved_objects/_find"
OP_GET = "GET /api/saved_objects/{type}/{id}"
# Startup credential check only — never exposed as a tool.
OP_STATUS = "GET /api/status"

ALLOWED_OPERATIONS: tuple[str, ...] = (OP_FIND, OP_GET, OP_STATUS)

_SPACE_ID = re.compile(r"^[a-z0-9_-]{1,64}$")
_UNHEALTHY_LEVELS = frozenset({"unavailable", "critical"})


@dataclass
class CredentialReport:
    ok: bool
    reasons: list[str] = field(default_factory=list)


def _q(value: str) -> str:
    return quote(value, safe="")


def check_space(space: str | None) -> None:
    if space is not None and not _SPACE_ID.match(space):
        raise invalid_input("space", "phải khớp ^[a-z0-9_-]{1,64}$", SOURCE)


class KibanaClient:
    def __init__(
        self,
        settings: Settings,
        *,
        common: CommonSettings | None = None,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self._common = common or CommonSettings()
        self._host = httpx.URL(settings.base_url).host
        auth = (
            (settings.username, settings.password.get_secret_value())
            if settings.username and settings.password
            else None
        )
        self.http = http or build_client(
            settings=self._common,
            headers={"Accept": "application/json"},
            auth=auth,
            verify=settings.verify_certs,
        )

    @property
    def base_url(self) -> str:
        return self._settings.base_url

    async def aclose(self) -> None:
        await self.http.aclose()

    async def get(
        self,
        operation: str,
        path_params: Mapping[str, str] | None = None,
        params: Mapping[str, Any] | None = None,
        *,
        space: str | None = None,
    ) -> Any:
        """The single choke point: allowlist -> (space-prefixed) URL -> GET with retry."""
        enforce(ALLOWED_OPERATIONS, operation, source=SOURCE)
        check_space(space)
        path = operation.removeprefix("GET ").format(
            **{k: _q(v) for k, v in (path_params or {}).items()}
        )
        prefix = f"/s/{space}" if space else ""
        response = await request_with_retry(
            self.http,
            "GET",
            f"{self._settings.base_url}{prefix}{path}",
            source=SOURCE,
            host=self._host,
            settings=self._common,
            params={k: v for k, v in (params or {}).items() if v is not None},
        )
        try:
            return response.json()
        except ValueError as exc:
            raise ToolError(
                ErrorCode.UPSTREAM_ERROR,
                "Kibana trả nội dung không phải JSON (kiểm tra MCP_KIBANA_BASE_URL).",
                SOURCE,
                True,
                details={"host": self._host},
            ) from exc

    # -- startup credential check ----------------------------------------------------------

    async def verify_credentials(self) -> CredentialReport:
        """`GET /api/status`: credentials accepted and Kibana overall status available."""
        try:
            status = await self.get(OP_STATUS)
        except ToolError as exc:
            hint = exc.details.get("hint")
            reason = f"{exc.code.value}: {exc.message}"
            return CredentialReport(False, [f"{reason} ({hint})" if hint else reason])
        overall = (
            ((status or {}).get("status") or {}).get("overall")
            if isinstance(status, dict)
            else None
        )
        if not isinstance(overall, dict):
            return CredentialReport(
                False, ["unexpected response from /api/status (no overall status)"]
            )
        level = str(overall.get("level") or "").lower()
        state = str(overall.get("state") or "").lower()
        if level in _UNHEALTHY_LEVELS or state == "red":
            return CredentialReport(False, [f"Kibana reports status {level or state}"])
        if not level and not state:
            return CredentialReport(False, ["Kibana /api/status has no level/state"])
        return CredentialReport(True)

    async def credential_check(self) -> bool:
        """`mcp_common.runtime.serve(credential_check=...)` hook."""
        return (await self.verify_credentials()).ok
