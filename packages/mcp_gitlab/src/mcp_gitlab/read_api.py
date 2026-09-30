"""GitLab tool layer: input bounds, project/ref resolution, redaction, byte budget, paging.

Tool bounds (`limit <= 100`, `max_bytes`, tz-aware timestamps) live here, not in
`client.py`, so `mcp-ingest`'s connector can crawl without them (ADR-0007 A3 /
ADR-0012 A4) — the connector must never import this module.
"""

from __future__ import annotations

import base64
import fnmatch
import re
import time
from datetime import datetime
from typing import Any

from mcp_common.config import CommonSettings
from mcp_common.content import wrap_untrusted
from mcp_common.envelope import Citation, SourceType
from mcp_common.errors import ErrorCode, ToolError
from mcp_common.redact import scrub
from mcp_common.tooling import (
    RedactionCounter,
    TextBudget,
    ToolOutcome,
    build_result,
    decode_cursor,
    effective_max_bytes,
    encode_cursor,
    invalid_input,
    not_found_result,
)

from mcp_gitlab import mappers
from mcp_gitlab.client import SOURCE, GitLabClient

__all__ = ["GitLabReadApi"]

_IID = re.compile(r"^[0-9]{1,12}$")
_PIPELINE_ID = re.compile(r"^[0-9]{1,18}$")
_MR_STATES = ("opened", "closed", "merged", "locked", "all")
_ISSUE_STATES = ("opened", "closed", "all")
_PIPELINE_STATUSES = (
    "created", "waiting_for_resource", "preparing", "pending", "running",
    "success", "failed", "canceled", "skipped", "manual", "scheduled",
)  # fmt: skip
_MAX_CHANGED_FILES = 200
_MAX_FAILED_TRACES = 3
_BINARY_SNIFF_BYTES = 8000


def _is_not_found(exc: ToolError) -> bool:
    return exc.code == ErrorCode.UPSTREAM_ERROR and exc.details.get("upstream_status") == 404


def _tail_bytes(text: str, max_bytes: int) -> tuple[str, bool]:
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text, False
    return encoded[-max_bytes:].decode("utf-8", errors="ignore"), True


class _Call:
    """State of one tool call: timing, redaction counter, shared byte budget, warnings."""

    def __init__(self, budget_bytes: int, warnings: list[str] | None = None) -> None:
        self.started = time.monotonic()
        self.counter = RedactionCounter()
        self.budget = TextBudget(budget_bytes)
        self.warnings: list[str] = list(warnings or [])
        self.truncated_elsewhere = False

    @property
    def truncated(self) -> bool:
        return self.budget.truncated or self.truncated_elsewhere

    def warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)


class GitLabReadApi:
    def __init__(self, client: GitLabClient, common: CommonSettings) -> None:
        self._client = client
        self._common = common

    # -- validation helpers ------------------------------------------------------------

    @staticmethod
    def _limit(limit: int) -> None:
        if not 1 <= limit <= 100:
            raise invalid_input("limit", "phải nằm trong khoảng 1..100", SOURCE)

    @staticmethod
    def _project(project: str | None, *, required: bool) -> None:
        if project is None:
            if required:
                raise invalid_input("project", "bắt buộc", SOURCE)
            return
        if not 1 <= len(project) <= 512:
            raise invalid_input("project", "độ dài phải từ 1 đến 512 ký tự", SOURCE)

    @staticmethod
    def _max_bytes(max_bytes: int) -> None:
        if not 1024 <= max_bytes <= 131072:
            raise invalid_input("max_bytes", "phải nằm trong khoảng 1024..131072", SOURCE)

    @staticmethod
    def _aware(value: datetime | None, field: str) -> str | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise invalid_input(field, "thiếu timezone (ví dụ hậu tố Z)", SOURCE)
        return value.isoformat()

    @staticmethod
    def _page(cursor: str | None) -> int:
        page = decode_cursor(cursor, source=SOURCE).get("page", 1)
        if not isinstance(page, int) or page < 1:
            raise invalid_input("cursor", "cursor không hợp lệ; dùng đúng meta.next_cursor", SOURCE)
        return page

    @staticmethod
    def _next_cursor(next_page: int | None) -> str | None:
        return encode_cursor({"page": next_page}) if next_page else None

    # -- shared building blocks --------------------------------------------------------

    def _text(self, call: _Call, text: str | None, content_id: str) -> str | None:
        """Redact -> spend budget -> wrap as untrusted (ADR-0015; result boundary only)."""
        if not text:
            return None
        scrubbed, count = scrub(text, disabled=self._common.redact_disabled)
        call.counter.count += count
        cut, _ = call.budget.take(scrubbed)
        return wrap_untrusted(cut, source=SOURCE, content_id=content_id)

    def _not_found(self, call: _Call, echo: dict[str, Any], identifier: str) -> ToolOutcome:
        result = not_found_result(SourceType.GITLAB, started=call.started, query_echo=echo)
        return ToolOutcome(result, identifier=identifier)

    def _finish(
        self,
        call: _Call,
        items: list[dict[str, Any]],
        citations: list[Citation],
        echo: dict[str, Any],
        *,
        description: str,
        next_page: int | None = None,
    ) -> ToolOutcome:
        result = build_result(
            SourceType.GITLAB,
            items,
            citations,
            started=call.started,
            query_echo=echo,
            next_cursor=self._next_cursor(next_page),
            truncated=call.truncated,
            warnings=call.warnings,
            redactions=call.counter.count,
        )
        return ToolOutcome(result, query_description=description)

    async def _project_info(self, ref: str) -> dict[str, Any] | None:
        """The project payload, or None if it does not exist."""
        try:
            return await self._client.get_project(ref)
        except ToolError as exc:
            if _is_not_found(exc):
                return None
            raise

    def _default_budget(self) -> int:
        return self._common.max_output_bytes

    # -- gitlab_search_projects --------------------------------------------------------

    async def search_projects(
        self,
        *,
        query: str,
        membership_only: bool = False,
        limit: int = 20,
        cursor: str | None = None,
    ) -> ToolOutcome:
        if not 1 <= len(query) <= 256:
            raise invalid_input("query", "độ dài phải từ 1 đến 256 ký tự", SOURCE)
        self._limit(limit)
        page = self._page(cursor)
        call = _Call(self._default_budget())
        response = await self._client.search_projects(
            query, membership=membership_only, per_page=limit, page=page
        )
        items: list[dict[str, Any]] = []
        citations: list[Citation] = []
        for index, raw in enumerate(response.data):
            item, citation = mappers.map_project(raw, citation_ref=index)
            items.append(item)
            citations.append(citation)
        echo = {"query": query, "membership_only": membership_only, "limit": limit}
        return self._finish(
            call, items, citations, echo,
            description=f'project GitLab khớp "{query}"', next_page=response.next_page,
        )  # fmt: skip

    # -- gitlab_search_code ------------------------------------------------------------

    async def search_code(
        self,
        *,
        query: str,
        project: str | None = None,
        ref: str | None = None,
        filename_filter: str | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> ToolOutcome:
        if not 2 <= len(query) <= 512:
            raise invalid_input("query", "độ dài phải từ 2 đến 512 ký tự", SOURCE)
        self._project(project, required=False)
        self._limit(limit)
        page = self._page(cursor)
        call = _Call(self._default_budget())
        echo = {
            "query": query, "project": project, "ref": ref,
            "filename_filter": filename_filter, "limit": limit,
        }  # fmt: skip

        cache: dict[str, dict[str, Any] | None] = {}
        if project is not None:
            scoped = await self._project_info(project)
            if scoped is None:
                return self._not_found(call, echo, f"Project {project}")
            cache[str(scoped["id"])] = scoped

        try:
            response = await self._client.search_blobs(
                query, project=project, ref=ref, per_page=limit, page=page
            )
        except ToolError as exc:
            if project is not None and _is_not_found(exc):
                return self._not_found(call, echo, f"Project {project}")
            raise

        items: list[dict[str, Any]] = []
        citations: list[Citation] = []
        denied = 0
        for hit in response.data:
            path = str(hit.get("path", ""))
            if self._client.is_path_denied(path):
                denied += 1
                continue
            basename = path.rsplit("/", 1)[-1]
            if filename_filter and not (
                fnmatch.fnmatch(path, filename_filter) or fnmatch.fnmatch(basename, filename_filter)
            ):
                continue
            project_id = str(hit.get("project_id"))
            if project_id not in cache:
                try:
                    cache[project_id] = await self._project_info(project_id)
                except ToolError as exc:
                    if exc.code in (ErrorCode.FORBIDDEN, ErrorCode.UNAUTHORIZED):
                        cache[project_id] = None
                    else:
                        raise
            info = cache[project_id]
            if info is None:
                call.warn(f"bỏ qua kết quả của project {project_id} (không truy cập được)")
                continue
            item, citation = mappers.map_code_hit(
                hit,
                project_path=info["path_with_namespace"],
                project_web_url=info["web_url"],
                excerpt=self._text(call, hit.get("data"), f"{project_id}:{path}"),
                citation_ref=len(items),
            )
            items.append(item)
            citations.append(citation)
        if denied:
            call.warn(f"{denied} kết quả bị loại bởi deny-glob (MCP_GITLAB_PATH_DENY)")
        if response.next_page and not items:
            call.warn("trang này không còn kết quả hợp lệ sau khi lọc; dùng cursor để xem tiếp")
        return self._finish(
            call, items, citations, echo,
            description=f'code GitLab cho "{query}"', next_page=response.next_page,
        )  # fmt: skip

    # -- gitlab_get_file ---------------------------------------------------------------

    async def get_file(
        self, *, project: str, path: str, ref: str = "HEAD", max_bytes: int = 65536
    ) -> ToolOutcome:
        self._project(project, required=True)
        if not 1 <= len(path) <= 1024:
            raise invalid_input("path", "độ dài phải từ 1 đến 1024 ký tự", SOURCE)
        self._max_bytes(max_bytes)
        self._client.assert_path_allowed(path)  # deny-glob: before any network I/O
        effective, clamp_warnings = effective_max_bytes(max_bytes, self._common)
        call = _Call(effective, clamp_warnings)
        echo = {"project": project, "path": path, "ref": ref, "max_bytes": effective}

        info = await self._project_info(project)
        if info is None:
            return self._not_found(call, echo, f"Project {project}")
        resolved_ref = (info.get("default_branch") or "HEAD") if ref == "HEAD" else ref
        identifier = f"{project}:{path}@{resolved_ref}"
        try:
            raw = await self._client.get_file(project, path, resolved_ref)
        except ToolError as exc:
            if _is_not_found(exc):
                return self._not_found(call, echo, identifier)
            raise

        payload = str(raw.get("content", ""))
        data = base64.b64decode(payload) if raw.get("encoding") == "base64" else payload.encode()
        if b"\x00" in data[:_BINARY_SNIFF_BYTES]:
            text = f"[binary file omitted: {len(data)} bytes]"
            call.warn("file nhị phân: nội dung không được trả về")
        else:
            text = data.decode("utf-8", errors="replace")
        content = self._text(call, text, f"{info['id']}:{path}") or ""
        item, citation = mappers.map_file(
            raw,
            project_path=info["path_with_namespace"],
            project_web_url=info["web_url"],
            ref=resolved_ref,
            content=content,
            truncated=call.budget.truncated,
            citation_ref=0,
        )
        if call.budget.truncated:
            call.warn("nội dung file đã bị cắt theo max_bytes")
        outcome = self._finish(call, [item], [citation], echo, description=identifier)
        outcome.identifier = identifier
        return outcome

    # -- gitlab_list_repository_tree ---------------------------------------------------

    async def list_repository_tree(
        self,
        *,
        project: str,
        path: str = "",
        ref: str = "HEAD",
        recursive: bool = False,
        limit: int = 20,
        cursor: str | None = None,
    ) -> ToolOutcome:
        self._project(project, required=True)
        self._limit(limit)
        page = self._page(cursor)
        call = _Call(self._default_budget())
        echo = {
            "project": project,
            "path": path,
            "ref": ref,
            "recursive": recursive,
            "limit": limit,
        }
        info = await self._project_info(project)
        if info is None:
            return self._not_found(call, echo, f"Project {project}")
        resolved_ref = (info.get("default_branch") or "HEAD") if ref == "HEAD" else ref
        identifier = f"{project}:{path or '/'}@{resolved_ref}"
        try:
            response = await self._client.list_tree(
                project, path=path, ref=resolved_ref, recursive=recursive, per_page=limit, page=page
            )
        except ToolError as exc:
            if _is_not_found(exc):
                return self._not_found(call, echo, identifier)
            raise
        items: list[dict[str, Any]] = []
        citations: list[Citation] = []
        for index, entry in enumerate(response.data):
            item, citation = mappers.map_tree_entry(
                entry,
                project_path=info["path_with_namespace"],
                project_web_url=info["web_url"],
                ref=resolved_ref,
                citation_ref=index,
            )
            items.append(item)
            citations.append(citation)
        return self._finish(
            call, items, citations, echo,
            description=f"mục trong {identifier}", next_page=response.next_page,
        )  # fmt: skip

    # -- gitlab_list_commits -----------------------------------------------------------

    async def list_commits(
        self,
        *,
        project: str,
        ref: str = "HEAD",
        path: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> ToolOutcome:
        self._project(project, required=True)
        self._limit(limit)
        since_iso = self._aware(since, "since")
        until_iso = self._aware(until, "until")
        page = self._page(cursor)
        call = _Call(self._default_budget())
        echo = {
            "project": project, "ref": ref, "path": path,
            "since": since_iso, "until": until_iso, "limit": limit,
        }  # fmt: skip
        try:
            response = await self._client.list_commits(
                project,
                ref=None if ref == "HEAD" else ref,
                path=path,
                since=since_iso,
                until=until_iso,
                per_page=limit,
                page=page,
            )
        except ToolError as exc:
            if _is_not_found(exc):
                return self._not_found(call, echo, f"Project {project}")
            raise
        items: list[dict[str, Any]] = []
        citations: list[Citation] = []
        for index, raw in enumerate(response.data):
            message = raw.get("message")
            if message is not None and message.strip() == str(raw.get("title", "")).strip():
                message = None
            item, citation = mappers.map_commit(
                raw,
                project=project,
                message=self._text(call, message, str(raw["id"])),
                citation_ref=index,
            )
            items.append(item)
            citations.append(citation)
        return self._finish(
            call, items, citations, echo,
            description=f"commit trong {project}", next_page=response.next_page,
        )  # fmt: skip

    # -- gitlab_list_merge_requests ----------------------------------------------------

    async def list_merge_requests(
        self,
        *,
        project: str | None = None,
        search: str | None = None,
        state: str = "all",
        author_username: str | None = None,
        target_branch: str | None = None,
        updated_after: datetime | None = None,
        labels: list[str] | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> ToolOutcome:
        self._project(project, required=False)
        if search is not None and len(search) > 256:
            raise invalid_input("search", "tối đa 256 ký tự", SOURCE)
        if state not in _MR_STATES:
            raise invalid_input("state", f"phải thuộc {list(_MR_STATES)}", SOURCE)
        labels = labels or []
        if len(labels) > 10:
            raise invalid_input("labels", "tối đa 10 label", SOURCE)
        self._limit(limit)
        updated_iso = self._aware(updated_after, "updated_after")
        page = self._page(cursor)
        call = _Call(self._default_budget())
        echo = {
            "project": project, "search": search, "state": state,
            "author_username": author_username, "target_branch": target_branch,
            "updated_after": updated_iso, "labels": labels, "limit": limit,
        }  # fmt: skip
        filters = {
            "search": search, "state": state, "author_username": author_username,
            "target_branch": target_branch, "updated_after": updated_iso,
            "labels": ",".join(labels) or None,
        }  # fmt: skip
        try:
            response = await self._client.list_merge_requests(
                project, filters=filters, per_page=limit, page=page
            )
        except ToolError as exc:
            if project is not None and _is_not_found(exc):
                return self._not_found(call, echo, f"Project {project}")
            raise
        items: list[dict[str, Any]] = []
        citations: list[Citation] = []
        for index, raw in enumerate(response.data):
            item, citation = mappers.map_merge_request(
                raw,
                project_path=mappers.project_path_from_web_url(
                    raw["web_url"], self._client.base_url
                ),
                description=None,
                changed_files=None,
                notes=None,
                citation_ref=index,
            )
            items.append(item)
            citations.append(citation)
        return self._finish(
            call, items, citations, echo,
            description="merge request GitLab", next_page=response.next_page,
        )  # fmt: skip

    # -- gitlab_get_merge_request ------------------------------------------------------

    def _notes(self, call: _Call, notes: list[dict[str, Any]], prefix: str) -> list[dict[str, Any]]:
        mapped = []
        for raw in notes:
            body = self._text(call, raw.get("body", ""), f"{prefix}:note-{raw['id']}") or ""
            mapped.append(mappers.map_note(raw, body=body))
        return mapped

    async def get_merge_request(
        self,
        *,
        project: str,
        iid: str,
        include_changes: bool = True,
        include_notes: bool = False,
        max_bytes: int = 65536,
    ) -> ToolOutcome:
        self._project(project, required=True)
        if not _IID.match(iid):
            raise invalid_input("iid", "phải là số nguyên dạng string (tối đa 12 chữ số)", SOURCE)
        self._max_bytes(max_bytes)
        effective, clamp_warnings = effective_max_bytes(max_bytes, self._common)
        call = _Call(effective, clamp_warnings)
        echo = {
            "project": project, "iid": iid, "include_changes": include_changes,
            "include_notes": include_notes, "max_bytes": effective,
        }  # fmt: skip
        identifier = f"{project}!{iid}"
        try:
            raw = await self._client.get_merge_request(project, iid)
        except ToolError as exc:
            if _is_not_found(exc):
                return self._not_found(call, echo, identifier)
            raise
        project_path = mappers.project_path_from_web_url(raw["web_url"], self._client.base_url)
        description = self._text(call, raw.get("description"), f"{identifier}:description")

        changed_files: list[dict[str, Any]] | None = None
        if include_changes:
            try:
                changes = await self._client.get_merge_request_changes(project, iid)
            except ToolError as exc:
                if not _is_not_found(exc):
                    raise
                changes = []
                call.warn("không lấy được danh sách file thay đổi (endpoint /changes)")
            changed_files = []
            for change in changes[:_MAX_CHANGED_FILES]:
                changed_files.append(self._changed_file(call, change, identifier))
            if len(changes) > _MAX_CHANGED_FILES:
                call.warn(f"chỉ hiển thị {_MAX_CHANGED_FILES}/{len(changes)} file thay đổi")
                call.truncated_elsewhere = True

        notes: list[dict[str, Any]] | None = None
        if include_notes:
            notes = self._notes(
                call, await self._client.get_merge_request_notes(project, iid), identifier
            )

        item, citation = mappers.map_merge_request(
            raw,
            project_path=project_path,
            description=description,
            changed_files=changed_files,
            notes=notes,
            citation_ref=0,
        )
        if call.truncated:
            call.warn("nội dung đã bị cắt theo max_bytes")
        outcome = self._finish(call, [item], [citation], echo, description=identifier)
        outcome.identifier = identifier
        return outcome

    def _changed_file(self, call: _Call, change: dict[str, Any], identifier: str) -> dict[str, Any]:
        new_path = str(change["new_path"])
        old_path = change.get("old_path")
        denied = self._client.is_path_denied(new_path) or (
            old_path is not None and self._client.is_path_denied(str(old_path))
        )
        if denied:
            call.warn("diff của file khớp deny-glob (MCP_GITLAB_PATH_DENY) không được trả về")
            return mappers.map_changed_file(change, diff_excerpt=None, truncated=False)
        diff = str(change.get("diff") or "")
        if not diff:
            return mappers.map_changed_file(change, diff_excerpt=None, truncated=False)
        scrubbed, count = scrub(diff, disabled=self._common.redact_disabled)
        call.counter.count += count
        cut, was_truncated = call.budget.take(scrubbed)
        if not cut:
            return mappers.map_changed_file(change, diff_excerpt=None, truncated=True)
        excerpt = wrap_untrusted(cut, source=SOURCE, content_id=f"{identifier}:{new_path}")
        return mappers.map_changed_file(change, diff_excerpt=excerpt, truncated=was_truncated)

    # -- gitlab_list_issues ------------------------------------------------------------

    async def list_issues(
        self,
        *,
        project: str | None = None,
        search: str | None = None,
        state: str = "all",
        labels: list[str] | None = None,
        assignee_username: str | None = None,
        updated_after: datetime | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> ToolOutcome:
        self._project(project, required=False)
        if search is not None and len(search) > 256:
            raise invalid_input("search", "tối đa 256 ký tự", SOURCE)
        if state not in _ISSUE_STATES:
            raise invalid_input("state", f"phải thuộc {list(_ISSUE_STATES)}", SOURCE)
        labels = labels or []
        if len(labels) > 10:
            raise invalid_input("labels", "tối đa 10 label", SOURCE)
        self._limit(limit)
        updated_iso = self._aware(updated_after, "updated_after")
        page = self._page(cursor)
        call = _Call(self._default_budget())
        echo = {
            "project": project, "search": search, "state": state, "labels": labels,
            "assignee_username": assignee_username, "updated_after": updated_iso, "limit": limit,
        }  # fmt: skip
        filters = {
            "search": search, "state": state, "labels": ",".join(labels) or None,
            "assignee_username": assignee_username, "updated_after": updated_iso,
        }  # fmt: skip
        try:
            response = await self._client.list_issues(
                project, filters=filters, per_page=limit, page=page
            )
        except ToolError as exc:
            if project is not None and _is_not_found(exc):
                return self._not_found(call, echo, f"Project {project}")
            raise
        items: list[dict[str, Any]] = []
        citations: list[Citation] = []
        for index, raw in enumerate(response.data):
            item, citation = mappers.map_issue(
                raw,
                project_path=mappers.project_path_from_web_url(
                    raw["web_url"], self._client.base_url
                ),
                description=None,
                notes=None,
                citation_ref=index,
            )
            items.append(item)
            citations.append(citation)
        return self._finish(
            call, items, citations, echo,
            description="issue GitLab", next_page=response.next_page,
        )  # fmt: skip

    # -- gitlab_get_issue --------------------------------------------------------------

    async def get_issue(
        self,
        *,
        project: str,
        iid: str,
        include_notes: bool = True,
        max_bytes: int = 65536,
    ) -> ToolOutcome:
        self._project(project, required=True)
        if not _IID.match(iid):
            raise invalid_input("iid", "phải là số nguyên dạng string (tối đa 12 chữ số)", SOURCE)
        self._max_bytes(max_bytes)
        effective, clamp_warnings = effective_max_bytes(max_bytes, self._common)
        call = _Call(effective, clamp_warnings)
        echo = {
            "project": project, "iid": iid, "include_notes": include_notes, "max_bytes": effective,
        }  # fmt: skip
        identifier = f"{project}#{iid}"
        try:
            raw = await self._client.get_issue(project, iid)
        except ToolError as exc:
            if _is_not_found(exc):
                return self._not_found(call, echo, identifier)
            raise
        project_path = mappers.project_path_from_web_url(raw["web_url"], self._client.base_url)
        description = self._text(call, raw.get("description"), f"{identifier}:description")
        notes: list[dict[str, Any]] | None = None
        if include_notes:
            notes = self._notes(call, await self._client.get_issue_notes(project, iid), identifier)
        item, citation = mappers.map_issue(
            raw, project_path=project_path, description=description, notes=notes, citation_ref=0
        )
        if call.truncated:
            call.warn("nội dung đã bị cắt theo max_bytes")
        outcome = self._finish(call, [item], [citation], echo, description=identifier)
        outcome.identifier = identifier
        return outcome

    # -- gitlab_list_pipelines ---------------------------------------------------------

    async def list_pipelines(
        self,
        *,
        project: str,
        ref: str | None = None,
        status: str | None = None,
        updated_after: datetime | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> ToolOutcome:
        self._project(project, required=True)
        if status is not None and status not in _PIPELINE_STATUSES:
            raise invalid_input("status", f"phải thuộc {list(_PIPELINE_STATUSES)}", SOURCE)
        self._limit(limit)
        updated_iso = self._aware(updated_after, "updated_after")
        page = self._page(cursor)
        call = _Call(self._default_budget())
        echo = {
            "project": project, "ref": ref, "status": status,
            "updated_after": updated_iso, "limit": limit,
        }  # fmt: skip
        info = await self._project_info(project)
        if info is None:
            return self._not_found(call, echo, f"Project {project}")
        try:
            response = await self._client.list_pipelines(
                project,
                filters={"ref": ref, "status": status, "updated_after": updated_iso},
                per_page=limit,
                page=page,
            )
        except ToolError as exc:
            if _is_not_found(exc):
                return self._not_found(call, echo, f"Project {project}")
            raise
        items: list[dict[str, Any]] = []
        citations: list[Citation] = []
        for index, raw in enumerate(response.data):
            item, citation = mappers.map_pipeline(
                raw,
                project_path=info["path_with_namespace"],
                jobs=None,
                duration_s=None,
                citation_ref=index,
            )
            items.append(item)
            citations.append(citation)
        return self._finish(
            call, items, citations, echo,
            description=f"pipeline trong {project}", next_page=response.next_page,
        )  # fmt: skip

    # -- gitlab_get_pipeline -----------------------------------------------------------

    async def get_pipeline(
        self,
        *,
        project: str,
        pipeline_id: str,
        include_failed_job_trace: bool = False,
        max_bytes: int = 65536,
    ) -> ToolOutcome:
        self._project(project, required=True)
        if not _PIPELINE_ID.match(pipeline_id):
            raise invalid_input("pipeline_id", "phải là số nguyên dạng string", SOURCE)
        self._max_bytes(max_bytes)
        effective, clamp_warnings = effective_max_bytes(max_bytes, self._common)
        call = _Call(effective, clamp_warnings)
        echo = {
            "project": project, "pipeline_id": pipeline_id,
            "include_failed_job_trace": include_failed_job_trace, "max_bytes": effective,
        }  # fmt: skip
        identifier = f"{project}: pipeline {pipeline_id}"
        info = await self._project_info(project)
        if info is None:
            return self._not_found(call, echo, f"Project {project}")
        try:
            raw = await self._client.get_pipeline(project, pipeline_id)
            raw_jobs = await self._client.get_pipeline_jobs(project, pipeline_id)
        except ToolError as exc:
            if _is_not_found(exc):
                return self._not_found(call, echo, identifier)
            raise

        per_trace = max(effective // _MAX_FAILED_TRACES, 512)
        traces_fetched = 0
        jobs: list[dict[str, Any]] = []
        for raw_job in raw_jobs:
            excerpt: str | None = None
            trace_truncated = False
            if (
                include_failed_job_trace
                and raw_job.get("status") == "failed"
                and traces_fetched < _MAX_FAILED_TRACES
            ):
                traces_fetched += 1
                excerpt, trace_truncated = await self._trace(
                    call, project, str(raw_job["id"]), per_trace
                )
            jobs.append(
                mappers.map_job(raw_job, trace_excerpt=excerpt, trace_truncated=trace_truncated)
            )
        item, citation = mappers.map_pipeline(
            raw,
            project_path=info["path_with_namespace"],
            jobs=jobs,
            duration_s=raw.get("duration"),
            citation_ref=0,
        )
        if call.truncated:
            call.warn("trace đã bị cắt: chỉ giữ phần cuối theo max_bytes")
        outcome = self._finish(call, [item], [citation], echo, description=identifier)
        outcome.identifier = identifier
        return outcome

    async def _trace(
        self, call: _Call, project: str, job_id: str, max_bytes: int
    ) -> tuple[str | None, bool]:
        try:
            trace = await self._client.get_job_trace(project, job_id)
        except ToolError as exc:
            if _is_not_found(exc):
                return None, False
            raise
        scrubbed, count = scrub(trace, disabled=self._common.redact_disabled)
        call.counter.count += count
        tail, truncated = _tail_bytes(scrubbed, max_bytes)
        if truncated:
            call.truncated_elsewhere = True
        return wrap_untrusted(tail, source=SOURCE, content_id=f"job-{job_id}"), truncated
