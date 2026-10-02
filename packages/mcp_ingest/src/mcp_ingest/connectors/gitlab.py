"""GitLab connector (T-071): repository files + MR/issue descriptions through
`mcp_gitlab.client` (read-only allowlist, deny-glob, retries).

* Which projects: `MCP_INGEST_GITLAB_PROJECTS` (path or numeric id). Which files: the include
  globs `MCP_INGEST_GITLAB_FILE_GLOBS` (documentation by default), size-capped.
* **Deny-glob at the ingest layer (ADR-0015 A1 / ADR-0012 A1):** a path matching
  `MCP_GITLAB_PATH_DENY` is never fetched; the connector yields a blocked document so that the
  pipeline records `ingest_failures{stage: redact, code: blocked_by_policy}`. Denied paths are
  surfaced even when they do not match the include globs: skipping them silently would make
  the policy invisible.
* `visibility` follows spike S5 (`identity.gitlab_visibility`), recomputed from the project on
  every run (one `GET /projects/{id}` per project).
* Incremental: MRs/issues by `updated_after`; repository files have no per-file timestamp in the
  tree API, so a project's files are re-read when its `last_activity_at` reaches the cursor, and
  unchanged ones are absorbed by the content hash.
"""

from __future__ import annotations

import base64
import fnmatch
from collections.abc import Iterable, Iterator
from datetime import datetime
from typing import Any
from urllib.parse import quote

from mcp_common.errors import ToolError
from mcp_gitlab.client import (
    OP_FILE,
    OP_ISSUE,
    OP_ISSUES,
    OP_MR,
    OP_MRS,
    OP_PROJECT,
    OP_TREE,
    GitLabClient,
)
from mcp_gitlab.settings import Settings as GitLabSettings

from mcp_ingest.connectors._common import parse_ts, source_settings_status
from mcp_ingest.connectors.base import (
    AsyncBridge,
    ConnectorStatus,
    CrawlMode,
    Cursor,
    SourceDocument,
)
from mcp_ingest.identity import gitlab_source_id, gitlab_visibility
from mcp_ingest.settings import Settings, split_csv

__all__ = ["GitLabConnector", "build", "status"]

PER_PAGE = 100
_MAX_PAGES = 1000  # hard stop against a misbehaving X-Next-Page


def status(settings: Settings) -> ConnectorStatus:
    extra = [] if split_csv(settings.gitlab_projects) else ["MCP_INGEST_GITLAB_PROJECTS"]
    result, _ = source_settings_status(GitLabSettings, "gitlab", extra_missing=extra)
    return result


def build(settings: Settings) -> GitLabConnector:
    from mcp_common.config import load_settings

    return GitLabConnector(
        GitLabClient(load_settings(GitLabSettings, source="gitlab"), enforce_egress=True),
        projects=split_csv(settings.gitlab_projects),
        team_projects=split_csv(settings.gitlab_team_projects),
        internal_is_team=settings.gitlab_internal_is_team,
        file_globs=split_csv(settings.gitlab_file_globs),
        max_file_bytes=settings.gitlab_max_file_bytes,
        include_mrs_issues=settings.gitlab_include_mrs_issues,
    )


class GitLabConnector:
    source_type = "gitlab"
    name = "GitLabConnector"
    operations_used: tuple[str, ...] = (
        OP_PROJECT,
        OP_TREE,
        OP_FILE,
        OP_MRS,
        OP_ISSUES,
        OP_MR,
        OP_ISSUE,
    )

    def __init__(
        self,
        client: GitLabClient,
        *,
        projects: list[str],
        team_projects: list[str],
        internal_is_team: bool = True,
        file_globs: list[str] | None = None,
        max_file_bytes: int = 262144,
        include_mrs_issues: bool = True,
        bridge: AsyncBridge | None = None,
    ) -> None:
        self._client = client
        self._projects = projects
        self._team_projects = team_projects
        self._internal_is_team = internal_is_team
        self._globs = [g.lower() for g in (file_globs or [])]
        self._max_bytes = max_file_bytes
        self._include_mrs_issues = include_mrs_issues
        self._bridge = bridge or AsyncBridge()
        self._project_cache: dict[str, dict[str, Any]] = {}

    def status(self) -> ConnectorStatus:
        return ConnectorStatus(enabled=True, configured=True)

    def close(self) -> None:
        self._bridge.close(self._client.aclose())

    # -- project facts -------------------------------------------------------------------------

    def _project(self, ref: str) -> dict[str, Any]:
        if ref not in self._project_cache:
            self._project_cache[ref] = self._bridge.run(self._client.get_project(ref))
        return self._project_cache[ref]

    @staticmethod
    def _access_level(project: dict[str, Any]) -> int | None:
        permissions = project.get("permissions") or {}
        levels = [
            (permissions.get(key) or {}).get("access_level")
            for key in ("project_access", "group_access")
        ]
        numbers = [level for level in levels if isinstance(level, int)]
        return max(numbers) if numbers else None

    def _visibility(self, project: dict[str, Any], kind: str, *, confidential: bool = False):
        feature = {
            "blob": "repository_access_level",
            "issue": "issues_access_level",
            "mr": "merge_requests_access_level",
        }[kind]
        return gitlab_visibility(
            kind=kind,  # type: ignore[arg-type]
            project_visibility=project.get("visibility"),
            feature_access=project.get(feature),
            confidential=confidential,
            project_path=str(project.get("path_with_namespace", "")),
            team_projects=self._team_projects,
            crawler_access_level=self._access_level(project),
            internal_is_team=self._internal_is_team,
        )

    # -- crawl ---------------------------------------------------------------------------------

    def iter_documents(
        self, cursor: Cursor | None, mode: CrawlMode, *, limit: int | None = None
    ) -> Iterator[SourceDocument]:
        since = cursor.watermark if (cursor and mode == "incremental") else None
        yielded = 0
        for ref in self._projects:
            project = self._project(ref)
            for document in self._project_documents(project, since):
                yield document
                yielded += 1
                if limit is not None and yielded >= limit:
                    return

    def _project_documents(
        self, project: dict[str, Any], since: datetime | None
    ) -> Iterator[SourceDocument]:
        activity = parse_ts(project.get("last_activity_at"))
        if since is None or activity is None or activity >= since:
            yield from self._files(project, activity)
        if self._include_mrs_issues:
            yield from self._items(project, "mr", since)
            yield from self._items(project, "issue", since)

    def _paged(self, call: Any) -> Iterator[Any]:
        page = 1
        for _ in range(_MAX_PAGES):
            response = self._bridge.run(call(page))
            yield from response.data
            if response.next_page is None:
                return
            page = response.next_page

    def _wanted(self, path: str) -> bool:
        lowered = path.lower()
        base = lowered.rsplit("/", 1)[-1]
        return any(
            fnmatch.fnmatchcase(base, g) or fnmatch.fnmatchcase(lowered, g) for g in self._globs
        )

    def _files(
        self, project: dict[str, Any], activity: datetime | None
    ) -> Iterator[SourceDocument]:
        branch = project.get("default_branch")
        if not branch:
            return  # empty repository
        pid = int(project["id"])
        for entry in self._paged(
            lambda page: self._client.list_tree(
                str(pid), path="", ref=branch, recursive=True, per_page=PER_PAGE, page=page
            )
        ):
            if entry.get("type") != "blob":
                continue
            path = str(entry["path"])
            if self._client.is_path_denied(path):
                yield self._blocked_blob(project, branch, path, activity)
            elif self._wanted(path):
                document = self._blob_document(project, branch, path, activity)
                if document is not None:
                    yield document

    def _blob_url(self, project: dict[str, Any], branch: str, path: str) -> str:
        return f"{project['web_url']}/-/blob/{quote(branch, safe='/')}/{quote(path, safe='/')}"

    def _blocked_blob(
        self, project: dict[str, Any], branch: str, path: str, updated: datetime | None
    ) -> SourceDocument:
        return SourceDocument(
            source_type="gitlab",
            source_id=gitlab_source_id(int(project["id"]), "blob", path),
            source_uri=self._blob_url(project, branch, path),
            raw_content="",
            source_updated_at=updated,
            title=path,
            container=project.get("path_with_namespace"),
            visibility=self._visibility(project, "blob"),
            path=path,
            blocked_reason="path matches MCP_GITLAB_PATH_DENY (deny-glob)",
        )

    def _blob_document(
        self, project: dict[str, Any], branch: str, path: str, updated: datetime | None
    ) -> SourceDocument | None:
        raw = self._bridge.run(self._client.get_file(str(project["id"]), path, branch))
        if int(raw.get("size") or 0) > self._max_bytes:
            return None  # too large to be documentation; a size cap, not a policy decision
        encoded = raw.get("content") or ""
        text = (
            base64.b64decode(encoded).decode("utf-8", "replace")
            if raw.get("encoding") == "base64"
            else str(encoded)
        )
        is_markdown = path.lower().endswith((".md", ".markdown"))
        return SourceDocument(
            source_type="gitlab",
            source_id=gitlab_source_id(int(project["id"]), "blob", path),
            source_uri=self._blob_url(project, branch, path),
            raw_content=text,
            source_format="markdown" if is_markdown else "plain",
            source_updated_at=updated,
            title=path,
            container=project.get("path_with_namespace"),
            visibility=self._visibility(project, "blob"),
            path=path,
            metadata={"ref": branch, "blob_id": raw.get("blob_id")},
        )

    def _items(
        self, project: dict[str, Any], kind: str, since: datetime | None
    ) -> Iterator[SourceDocument]:
        filters: dict[str, Any] = {"state": "all", "order_by": "updated_at", "sort": "asc"}
        if since is not None:
            filters["updated_after"] = since.isoformat()
        pid = str(project["id"])
        fetch = self._client.list_merge_requests if kind == "mr" else self._client.list_issues
        for raw in self._paged(
            lambda page: fetch(pid, filters=filters, per_page=PER_PAGE, page=page)
        ):
            document = self._item_document(project, kind, raw)
            if since is not None and document.source_updated_at is not None:
                if document.source_updated_at < since:
                    continue  # inclusive boundary, exactly
            yield document

    def _item_document(
        self, project: dict[str, Any], kind: str, raw: dict[str, Any]
    ) -> SourceDocument:
        title = str(raw.get("title") or "")
        body = f"# {title}\n\n{raw.get('description') or ''}".strip()
        author = (raw.get("author") or {}).get("username")
        return SourceDocument(
            source_type="gitlab",
            source_id=gitlab_source_id(int(project["id"]), kind, str(raw["iid"])),  # type: ignore[arg-type]
            source_uri=str(raw["web_url"]),
            raw_content=body,
            source_format="markdown",
            source_updated_at=parse_ts(raw.get("updated_at")),
            title=title or None,
            container=project.get("path_with_namespace"),
            author=author,
            visibility=self._visibility(
                project, kind, confidential=bool(raw.get("confidential", False))
            ),
            metadata={"state": raw.get("state")},
        )

    # -- retry-failed --------------------------------------------------------------------------

    def fetch_documents(self, source_ids: Iterable[str]) -> Iterator[SourceDocument]:
        for source_id in source_ids:
            try:
                document = self._fetch_one(source_id)
            except ToolError as exc:
                if exc.details.get("upstream_status") == 404:
                    continue
                raise
            if document is not None:
                yield document

    def _fetch_one(self, source_id: str) -> SourceDocument | None:
        project_id, kind, ref = source_id.split(":", 2)
        project = self._project(project_id)
        activity = parse_ts(project.get("last_activity_at"))
        if kind == "blob":
            branch = project.get("default_branch")
            if not branch:
                return None
            if self._client.is_path_denied(ref):
                return self._blocked_blob(project, branch, ref, activity)
            return self._blob_document(project, branch, ref, activity)
        getter = self._client.get_merge_request if kind == "mr" else self._client.get_issue
        return self._item_document(project, kind, self._bridge.run(getter(project_id, ref)))
