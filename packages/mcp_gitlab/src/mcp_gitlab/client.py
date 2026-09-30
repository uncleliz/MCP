"""GitLab transport layer (ADR-0007): thin httpx client over REST API v4.

`client.py` is the layer `mcp-ingest`'s connector reuses: read-only allowlist, timeouts,
PAT scope check and the path deny-glob live here (ADR-0015 A1 — the deny-glob must also
protect the ingest path, not just the tools); tool bounds live in `read_api.py`.

Read-only guarantees in this file:
* every call goes through :meth:`GitLabClient.get` / :meth:`GitLabClient.get_text`, which
  check the operation against :data:`ALLOWED_OPERATIONS` (GET only);
* the `httpx.AsyncClient` comes from `mcp_common.http.build_client`, whose transport hook
  refuses any non-GET/HEAD request (ADR-0003 A2);
* the startup check refuses tokens with any scope beyond `read_api` / `read_repository`.
"""

from __future__ import annotations

import fnmatch
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

import httpx
from mcp_common.config import CommonSettings
from mcp_common.errors import ErrorCode, NotPermittedError, ToolError
from mcp_common.http import build_client, request_with_retry
from mcp_common.readonly import enforce

from mcp_gitlab.settings import Settings

__all__ = [
    "ALLOWED_OPERATIONS",
    "ALLOWED_TOKEN_SCOPES",
    "CredentialReport",
    "GitLabClient",
    "GitLabResponse",
    "SOURCE",
]

SOURCE = "gitlab"

OP_PROJECTS = "GET /api/v4/projects"
OP_PROJECT = "GET /api/v4/projects/{id}"
OP_SEARCH = "GET /api/v4/search"
OP_PROJECT_SEARCH = "GET /api/v4/projects/{id}/search"
OP_FILE = "GET /api/v4/projects/{id}/repository/files/{path}"
OP_TREE = "GET /api/v4/projects/{id}/repository/tree"
OP_COMMITS = "GET /api/v4/projects/{id}/repository/commits"
OP_MRS = "GET /api/v4/projects/{id}/merge_requests"
OP_MRS_GLOBAL = "GET /api/v4/merge_requests"
OP_MR = "GET /api/v4/projects/{id}/merge_requests/{iid}"
OP_MR_CHANGES = "GET /api/v4/projects/{id}/merge_requests/{iid}/changes"
OP_MR_NOTES = "GET /api/v4/projects/{id}/merge_requests/{iid}/notes"
OP_ISSUES = "GET /api/v4/projects/{id}/issues"
OP_ISSUES_GLOBAL = "GET /api/v4/issues"
OP_ISSUE = "GET /api/v4/projects/{id}/issues/{iid}"
OP_ISSUE_NOTES = "GET /api/v4/projects/{id}/issues/{iid}/notes"
OP_PIPELINES = "GET /api/v4/projects/{id}/pipelines"
OP_PIPELINE = "GET /api/v4/projects/{id}/pipelines/{pid}"
OP_PIPELINE_JOBS = "GET /api/v4/projects/{id}/pipelines/{pid}/jobs"
OP_JOB_TRACE = "GET /api/v4/projects/{id}/jobs/{jid}/trace"
# Startup credential check only — never exposed as a tool (ADR-0007 A2).
OP_TOKEN_SELF = "GET /api/v4/personal_access_tokens/self"

ALLOWED_OPERATIONS: tuple[str, ...] = (
    OP_PROJECTS,
    OP_PROJECT,
    OP_SEARCH,
    OP_PROJECT_SEARCH,
    OP_FILE,
    OP_TREE,
    OP_COMMITS,
    OP_MRS,
    OP_MRS_GLOBAL,
    OP_MR,
    OP_MR_CHANGES,
    OP_MR_NOTES,
    OP_ISSUES,
    OP_ISSUES_GLOBAL,
    OP_ISSUE,
    OP_ISSUE_NOTES,
    OP_PIPELINES,
    OP_PIPELINE,
    OP_PIPELINE_JOBS,
    OP_JOB_TRACE,
    OP_TOKEN_SELF,
)

ALLOWED_TOKEN_SCOPES = frozenset({"read_api", "read_repository"})


@dataclass
class GitLabResponse:
    data: Any
    next_page: int | None = None


@dataclass
class CredentialReport:
    ok: bool
    reasons: list[str] = field(default_factory=list)


def _q(value: str) -> str:
    return quote(value, safe="")


def _clean(params: Mapping[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for key, value in params.items():
        if value is None:
            continue
        cleaned[key] = str(value).lower() if isinstance(value, bool) else value
    return cleaned


class GitLabClient:
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
        self._deny = [glob.lower() for glob in settings.deny_globs]
        self.http = http or build_client(
            settings=self._common,
            headers={
                "PRIVATE-TOKEN": settings.private_token.get_secret_value(),
                "Accept": "application/json",
            },
        )

    @property
    def base_url(self) -> str:
        return self._settings.base_url

    async def aclose(self) -> None:
        await self.http.aclose()

    # -- deny-glob (connector-facing, ADR-0015 A1) ------------------------------------

    def is_path_denied(self, path: str) -> bool:
        lowered = path.lower().lstrip("/")
        basename = lowered.rsplit("/", 1)[-1]
        return any(
            fnmatch.fnmatchcase(lowered, glob) or fnmatch.fnmatchcase(basename, glob)
            for glob in self._deny
        )

    def assert_path_allowed(self, path: str) -> None:
        if self.is_path_denied(path):
            raise NotPermittedError(
                f"Path '{path}' bị chặn bởi deny-glob (MCP_GITLAB_PATH_DENY).",
                source=SOURCE,
                operation=f"read {path}",
                allowlist=[],
            )

    # -- the single choke points -------------------------------------------------------

    async def _request(
        self,
        operation: str,
        path_params: Mapping[str, str] | None,
        params: Mapping[str, Any] | None,
    ) -> httpx.Response:
        enforce(ALLOWED_OPERATIONS, operation, source=SOURCE)
        path = operation.removeprefix("GET ").format(
            **{k: _q(v) for k, v in (path_params or {}).items()}
        )
        return await request_with_retry(
            self.http,
            "GET",
            f"{self._settings.base_url}{path}",
            source=SOURCE,
            host=self._host,
            settings=self._common,
            params=_clean(params or {}),
        )

    async def get(
        self,
        operation: str,
        path_params: Mapping[str, str] | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> GitLabResponse:
        response = await self._request(operation, path_params, params)
        try:
            data = response.json()
        except ValueError as exc:
            raise ToolError(
                ErrorCode.UPSTREAM_ERROR,
                "GitLab trả nội dung không phải JSON (kiểm tra MCP_GITLAB_BASE_URL).",
                SOURCE,
                True,
                details={"host": self._host},
            ) from exc
        next_page = response.headers.get("X-Next-Page", "").strip()
        return GitLabResponse(data, int(next_page) if next_page.isdigit() else None)

    async def get_text(
        self,
        operation: str,
        path_params: Mapping[str, str] | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> str:
        return (await self._request(operation, path_params, params)).text

    # -- typed operations (no tool bounds here; see module docstring) ------------------

    async def search_projects(
        self, query: str, *, membership: bool, per_page: int, page: int
    ) -> GitLabResponse:
        return await self.get(
            OP_PROJECTS,
            params={
                "search": query,
                "membership": membership,
                "order_by": "last_activity_at",
                "per_page": per_page,
                "page": page,
            },
        )

    async def get_project(self, project: str) -> dict[str, Any]:
        return dict((await self.get(OP_PROJECT, {"id": project})).data)

    async def search_blobs(
        self, query: str, *, project: str | None, ref: str | None, per_page: int, page: int
    ) -> GitLabResponse:
        params = {"scope": "blobs", "search": query, "per_page": per_page, "page": page}
        if project is None:
            return await self.get(OP_SEARCH, params=params)
        return await self.get(OP_PROJECT_SEARCH, {"id": project}, {**params, "ref": ref})

    async def get_file(self, project: str, path: str, ref: str) -> dict[str, Any]:
        self.assert_path_allowed(path)
        response = await self.get(OP_FILE, {"id": project, "path": path}, {"ref": ref})
        return dict(response.data)

    async def list_tree(
        self,
        project: str,
        *,
        path: str,
        ref: str | None,
        recursive: bool,
        per_page: int,
        page: int,
    ) -> GitLabResponse:
        return await self.get(
            OP_TREE,
            {"id": project},
            {
                "path": path or None,
                "ref": ref,
                "recursive": recursive,
                "per_page": per_page,
                "page": page,
            },
        )

    async def list_commits(
        self,
        project: str,
        *,
        ref: str | None,
        path: str | None,
        since: str | None,
        until: str | None,
        per_page: int,
        page: int,
    ) -> GitLabResponse:
        return await self.get(
            OP_COMMITS,
            {"id": project},
            {
                "ref_name": ref,
                "path": path,
                "since": since,
                "until": until,
                "per_page": per_page,
                "page": page,
            },
        )

    async def list_merge_requests(
        self, project: str | None, *, filters: Mapping[str, Any], per_page: int, page: int
    ) -> GitLabResponse:
        params = {**filters, "per_page": per_page, "page": page}
        if project is None:
            return await self.get(OP_MRS_GLOBAL, params={**params, "scope": "all"})
        return await self.get(OP_MRS, {"id": project}, params)

    async def get_merge_request(self, project: str, iid: str) -> dict[str, Any]:
        return dict((await self.get(OP_MR, {"id": project, "iid": iid})).data)

    async def get_merge_request_changes(self, project: str, iid: str) -> list[dict[str, Any]]:
        data = (await self.get(OP_MR_CHANGES, {"id": project, "iid": iid})).data
        return list(data.get("changes") or [])

    async def get_merge_request_notes(self, project: str, iid: str) -> list[dict[str, Any]]:
        response = await self.get(
            OP_MR_NOTES,
            {"id": project, "iid": iid},
            {"per_page": 100, "order_by": "created_at", "sort": "asc"},
        )
        return list(response.data)

    async def list_issues(
        self, project: str | None, *, filters: Mapping[str, Any], per_page: int, page: int
    ) -> GitLabResponse:
        params = {**filters, "per_page": per_page, "page": page}
        if project is None:
            return await self.get(OP_ISSUES_GLOBAL, params={**params, "scope": "all"})
        return await self.get(OP_ISSUES, {"id": project}, params)

    async def get_issue(self, project: str, iid: str) -> dict[str, Any]:
        return dict((await self.get(OP_ISSUE, {"id": project, "iid": iid})).data)

    async def get_issue_notes(self, project: str, iid: str) -> list[dict[str, Any]]:
        response = await self.get(
            OP_ISSUE_NOTES,
            {"id": project, "iid": iid},
            {"per_page": 100, "order_by": "created_at", "sort": "asc"},
        )
        return list(response.data)

    async def list_pipelines(
        self, project: str, *, filters: Mapping[str, Any], per_page: int, page: int
    ) -> GitLabResponse:
        return await self.get(
            OP_PIPELINES, {"id": project}, {**filters, "per_page": per_page, "page": page}
        )

    async def get_pipeline(self, project: str, pipeline_id: str) -> dict[str, Any]:
        return dict((await self.get(OP_PIPELINE, {"id": project, "pid": pipeline_id})).data)

    async def get_pipeline_jobs(self, project: str, pipeline_id: str) -> list[dict[str, Any]]:
        response = await self.get(
            OP_PIPELINE_JOBS, {"id": project, "pid": pipeline_id}, {"per_page": 100}
        )
        return list(response.data)

    async def get_job_trace(self, project: str, job_id: str) -> str:
        return await self.get_text(OP_JOB_TRACE, {"id": project, "jid": job_id})

    # -- startup credential check ------------------------------------------------------

    async def verify_credentials(self) -> CredentialReport:
        """`GET /personal_access_tokens/self` -> `scopes ⊆ {read_api, read_repository}`
        (ADR-0007 A2). Fails closed: an unknown/empty scope list is not proof of read-only."""
        try:
            token = (await self.get(OP_TOKEN_SELF)).data
        except ToolError as exc:
            hint = exc.details.get("hint")
            reason = f"{exc.code.value}: {exc.message}"
            return CredentialReport(False, [f"{reason} ({hint})" if hint else reason])
        if not isinstance(token, dict):
            return CredentialReport(False, ["unexpected response from personal_access_tokens/self"])

        reasons: list[str] = []
        scopes = {str(s) for s in token.get("scopes") or []}
        if not scopes:
            reasons.append("token reports no scopes; cannot verify read-only")
        extra = sorted(scopes - ALLOWED_TOKEN_SCOPES)
        if extra:
            reasons.append(
                f"token has scopes beyond read_api/read_repository: {', '.join(extra)} "
                "(create a PAT with only read_api and/or read_repository)"
            )
        if token.get("revoked") or token.get("active") is False:
            reasons.append("token is revoked or inactive")
        return CredentialReport(not reasons, reasons)

    async def credential_check(self) -> bool:
        """`mcp_common.runtime.serve(credential_check=...)` hook."""
        return (await self.verify_credentials()).ok
