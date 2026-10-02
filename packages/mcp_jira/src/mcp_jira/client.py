"""Jira transport layer (ADR-0007 / ADR-0019): thin httpx client over the Jira REST API.

`client.py` is the layer `mcp-ingest`'s Jira connector reuses (ADR-0007 A3 / ADR-0012 A4): it
enforces the read-only allowlist, the timeout budget and the Cloud/Server flavor split, but
deliberately applies **no tool bounds** (limit caps, JQL bounding) — those live in `read_api.py`.

Read-only guarantees in this file:
* every call goes through :meth:`JiraClient.get`, which checks the operation against
  :data:`ALLOWED_OPERATIONS` (GET only; the flavored `/rest/api/{2|3}` prefix is normalised to a
  version-independent key so one allowlist covers both flavors);
* the underlying `httpx.AsyncClient` is built by `mcp_common.http.build_client`, whose transport
  hook refuses any non-GET/HEAD request (ADR-0003 A2) — there is no create/transition/comment
  path anywhere in this module;
* the startup check (`GET /myself`) proves the token authenticates and, by probing the issue the
  caller can see, that it cannot create/transition/comment (ADR-0003 A1 / ADR-0007 A2).

Pagination (ADR-0019): Cloud returns a `nextPageToken`; Server/DC uses `startAt`/`maxResults`.
Both are hidden behind the opaque `CursorField` (ADR-0004) by :meth:`JiraClient.search_issues`
and the typed list helpers, which return a :class:`JiraPage` carrying a flavor-independent
`next_cursor` state.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

import httpx
from mcp_common.config import CommonSettings
from mcp_common.errors import ErrorCode, ToolError
from mcp_common.http import build_client, request_with_retry
from mcp_common.readonly import enforce
from mcp_common.redact import register_secret

from mcp_jira.settings import ResolvedFlavor, Settings

__all__ = [
    "ALLOWED_OPERATIONS",
    "CredentialReport",
    "JiraClient",
    "JiraPage",
    "SOURCE",
]

SOURCE = "jira"

# Version-independent operation keys. `GET /rest/api/{v}/...` is normalised to `GET /rest/api/...`
# before the allowlist check, so the SAME allowlist covers Cloud (`/rest/api/3`) and
# Server/DC (`/rest/api/2`). Agile endpoints (`/rest/agile/1.0`) are identical across flavors.
OP_SEARCH = "GET /rest/api/search"
OP_ISSUE = "GET /rest/api/issue/{key}"
OP_PROJECT_SEARCH = "GET /rest/api/project/search"
OP_PROJECT = "GET /rest/api/project"
OP_SPRINT = "GET /rest/agile/1.0/sprint/{id}"
OP_BOARD_SPRINTS = "GET /rest/agile/1.0/board/{id}/sprint"
# Startup credential check only — never exposed as a tool (ADR-0007 A2 / ADR-0019).
OP_MYSELF = "GET /rest/api/myself"

ALLOWED_OPERATIONS: tuple[str, ...] = (
    OP_SEARCH,
    OP_ISSUE,
    OP_PROJECT_SEARCH,
    OP_PROJECT,
    OP_SPRINT,
    OP_BOARD_SPRINTS,
    OP_MYSELF,
)

# Fields that, if the caller is permitted to perform them on an issue, prove the account is NOT
# read-only (ADR-0003 A1). Read via the `expand=editmeta`/transitions probe in the startup check.
_WRITE_PROBES = frozenset({"transitions", "editmeta"})


@dataclass
class JiraPage:
    """One page of a flavored list call plus the flavor-independent cursor to resume it.

    `next_cursor_state` is the opaque dict `read_api.py` round-trips through `encode_cursor`;
    `None` means there is no next page. The caller never sees `nextPageToken` vs `startAt`.
    """

    values: list[dict[str, Any]]
    next_cursor_state: dict[str, Any] | None = None
    total: int | None = None


@dataclass
class CredentialReport:
    ok: bool
    reasons: list[str] = field(default_factory=list)


class JiraClient:
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
        # E-mcp-data-platform-009 (FR-025/NFR-014): register the configured token for
        # value-based scrubbing at the single client-construction seam (the same token is
        # used for Cloud Basic auth and the Server/DC bearer header), so an opaque PAT the
        # shape/label/entropy passes cannot recognise is still redacted from any outbound
        # error/result/log. Covers the live server and the mcp_ingest Jira connector. Additive.
        register_secret(settings.token.get_secret_value())
        self.http = http or build_client(
            settings=self._common,
            enforce_egress=enforce_egress,
            auth=self._auth(settings),
            headers=self._headers(settings),
        )

    @staticmethod
    def _auth(settings: Settings) -> httpx.Auth | None:
        if settings.resolved_flavor == "cloud":
            # Cloud: Basic auth with email + API token.
            return httpx.BasicAuth(settings.email or "", settings.token.get_secret_value())
        return None  # Server/DC: bearer PAT, set as a header below.

    @staticmethod
    def _headers(settings: Settings) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if settings.resolved_flavor == "server":
            headers["Authorization"] = f"Bearer {settings.token.get_secret_value()}"
        return headers

    @property
    def base_url(self) -> str:
        return self._settings.base_url

    @property
    def flavor(self) -> ResolvedFlavor:
        return self._settings.resolved_flavor

    async def aclose(self) -> None:
        await self.http.aclose()

    # -- the single choke point --------------------------------------------------------

    def _api_path(self, operation: str, quoted: Mapping[str, str]) -> str:
        """Turn a version-independent operation key into the concrete flavored path.

        `GET /rest/api/issue/{key}` -> `/rest/api/3/issue/PAY-1` (Cloud) or
        `/rest/api/2/issue/PAY-1` (Server/DC). `/rest/agile/1.0/...` is left untouched.
        """
        path = operation.removeprefix("GET ")
        if path.startswith("/rest/api/"):
            path = "/rest/api/" + self._settings.api_version + path[len("/rest/api") :]
        return path.format(**quoted)

    async def get(
        self,
        operation: str,
        path_params: Mapping[str, str] | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        enforce(ALLOWED_OPERATIONS, operation, source=SOURCE)
        quoted = {k: quote(str(v), safe="") for k, v in (path_params or {}).items()}
        path = self._api_path(operation, quoted)
        response = await request_with_retry(
            self.http,
            "GET",
            f"{self._settings.base_url}{path}",
            source=SOURCE,
            host=self._host,
            settings=self._common,
            params={k: v for k, v in (params or {}).items() if v is not None},
        )
        try:
            body = response.json()
        except ValueError as exc:
            raise ToolError(
                ErrorCode.UPSTREAM_ERROR,
                "Jira trả nội dung không phải JSON (kiểm tra MCP_JIRA_BASE_URL).",
                SOURCE,
                True,
                details={"host": self._host},
            ) from exc
        if not isinstance(body, dict):
            raise ToolError(
                ErrorCode.UPSTREAM_ERROR, "Jira trả JSON không mong đợi.", SOURCE, True
            )
        return body

    # -- typed operations (no tool bounds here; see module docstring) ------------------

    async def search_issues(
        self, jql: str, *, max_results: int, cursor_state: Mapping[str, Any] | None = None
    ) -> JiraPage:
        """Flavor-aware JQL search. Cloud: `nextPageToken`; Server/DC: `startAt`/`maxResults`."""
        if self.flavor == "cloud":
            params: dict[str, Any] = {"jql": jql, "maxResults": max_results}
            token = (cursor_state or {}).get("nextPageToken")
            if token:
                params["nextPageToken"] = token
            payload = await self.get(OP_SEARCH, params=params)
            issues = list(payload.get("issues") or [])
            next_token = payload.get("nextPageToken")
            next_state = {"nextPageToken": next_token} if next_token else None
            return JiraPage(issues, next_state, payload.get("total"))

        start_at = int((cursor_state or {}).get("startAt", 0))
        payload = await self.get(
            OP_SEARCH, params={"jql": jql, "startAt": start_at, "maxResults": max_results}
        )
        issues = list(payload.get("issues") or [])
        total = payload.get("total")
        next_start = start_at + len(issues)
        has_more = isinstance(total, int) and next_start < total
        next_state = {"startAt": next_start} if has_more else None
        return JiraPage(issues, next_state, total if isinstance(total, int) else None)

    async def get_issue(self, key: str) -> dict[str, Any]:
        return await self.get(OP_ISSUE, {"key": key})

    async def list_projects(
        self, *, max_results: int, cursor_state: Mapping[str, Any] | None = None
    ) -> JiraPage:
        """Cloud paginates `/project/search` by `startAt`; Server/DC `/project` is unpaginated."""
        if self.flavor == "cloud":
            start_at = int((cursor_state or {}).get("startAt", 0))
            payload = await self.get(
                OP_PROJECT_SEARCH, params={"startAt": start_at, "maxResults": max_results}
            )
            values = list(payload.get("values") or [])
            is_last = bool(payload.get("isLast", True))
            next_state = (
                {"startAt": start_at + len(values)} if not is_last and values else None
            )
            return JiraPage(values, next_state, payload.get("total"))

        # Server/DC `/project` returns a bare JSON array (not a dict) — fetch via the raw path.
        enforce(ALLOWED_OPERATIONS, OP_PROJECT, source=SOURCE)
        path = self._api_path(OP_PROJECT, {})
        response = await request_with_retry(
            self.http,
            "GET",
            f"{self._settings.base_url}{path}",
            source=SOURCE,
            host=self._host,
            settings=self._common,
        )
        try:
            body = response.json()
        except ValueError as exc:
            raise ToolError(
                ErrorCode.UPSTREAM_ERROR,
                "Jira trả nội dung không phải JSON (kiểm tra MCP_JIRA_BASE_URL).",
                SOURCE,
                True,
                details={"host": self._host},
            ) from exc
        values = list(body) if isinstance(body, list) else []
        return JiraPage(values, None, len(values))

    async def get_sprint(self, sprint_id: int) -> dict[str, Any]:
        return await self.get(OP_SPRINT, {"id": str(sprint_id)})

    async def list_board_sprints(
        self,
        board_id: int,
        *,
        state: str,
        max_results: int,
        cursor_state: Mapping[str, Any] | None = None,
    ) -> JiraPage:
        """Agile `/board/{id}/sprint` paginates by `startAt`/`maxResults` on both flavors."""
        start_at = int((cursor_state or {}).get("startAt", 0))
        params: dict[str, Any] = {"startAt": start_at, "maxResults": max_results}
        if state and state != "all":
            params["state"] = state
        payload = await self.get(OP_BOARD_SPRINTS, {"id": str(board_id)}, params)
        values = list(payload.get("values") or [])
        is_last = bool(payload.get("isLast", True))
        next_state = {"startAt": start_at + len(values)} if not is_last and values else None
        return JiraPage(values, next_state, payload.get("total"))

    async def current_user(self) -> dict[str, Any]:
        return await self.get(OP_MYSELF)

    # -- startup credential check ------------------------------------------------------

    async def verify_credentials(self) -> CredentialReport:
        """Prove the token authenticates AND cannot write (ADR-0003 A1, ADR-0007 A2 / ADR-0019).

        Jira API tokens / PATs carry the full permissions of their user, so "read-only" is a
        property of the account, not the token. We therefore (1) check identity via `/myself`,
        then (2) sample one visible issue (`jira_search_issues` with an empty-order JQL) with
        `expand=transitions,editmeta` and require that the account may NOT transition or edit it.
        Everything is GET; nothing is created. Fails closed when the answer cannot be determined.
        """
        try:
            me = await self.current_user()
        except ToolError as exc:
            return CredentialReport(False, [self._reason(exc)])
        if not me.get("accountId") and not me.get("name") and not me.get("key"):
            return CredentialReport(False, ["/myself did not identify an authenticated user"])

        try:
            page = await self.search_issues("order by created DESC", max_results=1)
        except ToolError as exc:
            return CredentialReport(False, [self._reason(exc)])
        if not page.values:
            return CredentialReport(
                False, ["cannot verify read-only: no issue visible to sample permissions from"]
            )

        key = str(page.values[0].get("key") or "")
        if not key:
            return CredentialReport(
                False, ["cannot verify read-only: sampled issue has no key"]
            )
        try:
            probed = await self.get(
                OP_ISSUE, {"key": key}, {"expand": "transitions,editmeta"}
            )
        except ToolError as exc:
            return CredentialReport(False, [self._reason(exc)])

        writes = sorted(
            probe
            for probe in _WRITE_PROBES
            if self._probe_is_writable(probed.get(probe))
        )
        if writes:
            return CredentialReport(
                False,
                [
                    "account is not read-only (use a viewer-only service account); "
                    f"permitted write surface: {', '.join(writes)}"
                ],
            )
        return CredentialReport(True, [])

    @staticmethod
    def _probe_is_writable(node: Any) -> bool:
        """`transitions` is a non-empty list when the account may move the issue; `editmeta`
        is `{"fields": {...non-empty...}}` when the account may edit fields."""
        if isinstance(node, list):
            return bool(node)
        if isinstance(node, dict):
            return bool(node.get("fields"))
        return False

    @staticmethod
    def _reason(exc: ToolError) -> str:
        hint = exc.details.get("hint")
        reason = f"{exc.code.value}: {exc.message}"
        return f"{reason} ({hint})" if hint else reason

    async def credential_check(self) -> bool:
        """`mcp_common.runtime.serve(credential_check=...)` hook."""
        return (await self.verify_credentials()).ok
