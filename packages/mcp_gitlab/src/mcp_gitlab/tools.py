"""The eleven GitLab tools (FR-002). Names, input schemas and outputs are dictated by
`api-contract.yaml`; `tools.snapshot.json` + `tests/test_contract.py` keep them in sync.

Each function forwards to `GitLabReadApi` and returns a `ToolOutcome`; deadline, error
envelope and text rendering are applied by `register_tool`. Note that `gitlab_list_merge_requests`
and `gitlab_get_merge_request` legitimately contain "merge": they only read (ADR-0003 A2).
"""

from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp_common.config import CommonSettings
from mcp_common.readonly import readonly_tool
from mcp_common.tooling import (
    CursorParam,
    LimitParam,
    MaxBytesParam,
    ToolOutcome,
    register_tool,
)
from pydantic import Field

from mcp_gitlab.client import SOURCE
from mcp_gitlab.read_api import GitLabReadApi

__all__ = ["TOOL_DESCRIPTIONS", "make_tools", "register_tools"]

TOOL_DESCRIPTIONS: dict[str, str] = {
    "gitlab_search_projects": "Tìm project GitLab theo tên hoặc path, sắp theo hoạt động gần nhất.",
    "gitlab_search_code": (
        "Tìm code (blob) trong một project hoặc toàn instance, lọc tuỳ chọn theo ref và tên file. "
        "Kết quả có link web tới đúng file/dòng. File khớp deny-glob bị loại."
    ),
    "gitlab_get_file": (
        "Đọc nội dung một file tại một ref, đã redaction và cắt theo max_bytes. "
        "Path khớp deny-glob (.env, *.pem, ...) bị từ chối với not_permitted."
    ),
    "gitlab_list_repository_tree": "Liệt kê file và thư mục của một path tại một ref.",
    "gitlab_list_commits": (
        "Liệt kê commit của một project, lọc theo ref, path và khoảng thời gian."
    ),
    "gitlab_list_merge_requests": (
        "Liệt kê merge request theo project hoặc toàn instance, "
        "lọc theo trạng thái, tác giả, nhánh, label. "
        "Chỉ đọc."
    ),
    "gitlab_get_merge_request": (
        "Lấy chi tiết một merge request, kèm diff các file thay đổi và tuỳ chọn các note. "
        "Diff của file khớp deny-glob không được trả về."
    ),
    "gitlab_list_issues": (
        "Liệt kê issue theo project hoặc toàn instance, lọc theo trạng thái, label, assignee."
    ),
    "gitlab_get_issue": "Lấy chi tiết một issue, kèm các note (mặc định bật).",
    "gitlab_list_pipelines": (
        "Liệt kê pipeline của một project, lọc theo ref, trạng thái và thời điểm cập nhật."
    ),
    "gitlab_get_pipeline": (
        "Lấy chi tiết một pipeline kèm các job. "
        "Tuỳ chọn trả phần cuối trace của tối đa 3 job bị fail."
    ),
}

ProjectRequired = Annotated[
    str | None,
    Field(max_length=512, description="path_with_namespace hoặc id (dạng string)."),
]
ProjectOptional = Annotated[
    str | None,
    Field(max_length=512, description="path_with_namespace hoặc id; null = toàn instance."),
]
Iid = Annotated[str, Field(pattern=r"^[0-9]{1,12}$", description="IID dạng string.")]
Labels = Annotated[list[str], Field(max_length=10)]
Ref = Annotated[str, Field(description="Branch, tag hoặc commit; HEAD = default branch.")]

ApiFactory = Callable[[], GitLabReadApi]


def make_tools(api: ApiFactory) -> dict[str, Callable[..., Any]]:
    @readonly_tool
    async def gitlab_search_projects(
        query: Annotated[str, Field(min_length=1, max_length=256)],
        membership_only: bool = False,
        limit: LimitParam = 20,
        cursor: CursorParam = None,
    ) -> ToolOutcome:
        return await api().search_projects(
            query=query, membership_only=membership_only, limit=limit, cursor=cursor
        )

    @readonly_tool
    async def gitlab_search_code(
        query: Annotated[str, Field(min_length=2, max_length=512)],
        project: ProjectOptional = None,
        ref: Annotated[
            str | None, Field(description="Branch/tag; mặc định default branch.")
        ] = None,
        filename_filter: Annotated[str | None, Field(description="Ví dụ `*.py`.")] = None,
        limit: LimitParam = 20,
        cursor: CursorParam = None,
    ) -> ToolOutcome:
        return await api().search_code(
            query=query,
            project=project,
            ref=ref,
            filename_filter=filename_filter,
            limit=limit,
            cursor=cursor,
        )

    @readonly_tool
    async def gitlab_get_file(
        project: ProjectRequired,
        path: Annotated[str, Field(min_length=1, max_length=1024)],
        ref: Ref = "HEAD",
        max_bytes: MaxBytesParam = 65536,
    ) -> ToolOutcome:
        return await api().get_file(project=project or "", path=path, ref=ref, max_bytes=max_bytes)

    @readonly_tool
    async def gitlab_list_repository_tree(
        project: ProjectRequired,
        path: str = "",
        ref: Ref = "HEAD",
        recursive: bool = False,
        limit: LimitParam = 20,
        cursor: CursorParam = None,
    ) -> ToolOutcome:
        return await api().list_repository_tree(
            project=project or "",
            path=path,
            ref=ref,
            recursive=recursive,
            limit=limit,
            cursor=cursor,
        )

    @readonly_tool
    async def gitlab_list_commits(
        project: ProjectRequired,
        ref: Ref = "HEAD",
        path: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: LimitParam = 20,
        cursor: CursorParam = None,
    ) -> ToolOutcome:
        return await api().list_commits(
            project=project or "", ref=ref, path=path, since=since, until=until,
            limit=limit, cursor=cursor,
        )  # fmt: skip

    @readonly_tool
    async def gitlab_list_merge_requests(
        project: ProjectOptional = None,
        search: Annotated[str | None, Field(max_length=256)] = None,
        state: Literal["opened", "closed", "merged", "locked", "all"] = "all",
        author_username: str | None = None,
        target_branch: str | None = None,
        updated_after: datetime | None = None,
        labels: Labels = [],  # noqa: B006 (pydantic copies defaults)
        limit: LimitParam = 20,
        cursor: CursorParam = None,
    ) -> ToolOutcome:
        return await api().list_merge_requests(
            project=project, search=search, state=state, author_username=author_username,
            target_branch=target_branch, updated_after=updated_after, labels=list(labels),
            limit=limit, cursor=cursor,
        )  # fmt: skip

    @readonly_tool
    async def gitlab_get_merge_request(
        project: ProjectRequired,
        iid: Iid,
        include_changes: bool = True,
        include_notes: bool = False,
        max_bytes: MaxBytesParam = 65536,
    ) -> ToolOutcome:
        return await api().get_merge_request(
            project=project or "", iid=iid, include_changes=include_changes,
            include_notes=include_notes, max_bytes=max_bytes,
        )  # fmt: skip

    @readonly_tool
    async def gitlab_list_issues(
        project: ProjectOptional = None,
        search: Annotated[str | None, Field(max_length=256)] = None,
        state: Literal["opened", "closed", "all"] = "all",
        labels: Labels = [],  # noqa: B006 (pydantic copies defaults)
        assignee_username: str | None = None,
        updated_after: datetime | None = None,
        limit: LimitParam = 20,
        cursor: CursorParam = None,
    ) -> ToolOutcome:
        return await api().list_issues(
            project=project, search=search, state=state, labels=list(labels),
            assignee_username=assignee_username, updated_after=updated_after,
            limit=limit, cursor=cursor,
        )  # fmt: skip

    @readonly_tool
    async def gitlab_get_issue(
        project: ProjectRequired,
        iid: Iid,
        include_notes: bool = True,
        max_bytes: MaxBytesParam = 65536,
    ) -> ToolOutcome:
        return await api().get_issue(
            project=project or "", iid=iid, include_notes=include_notes, max_bytes=max_bytes
        )

    @readonly_tool
    async def gitlab_list_pipelines(
        project: ProjectRequired,
        ref: str | None = None,
        status: Literal[
            "created",
            "waiting_for_resource",
            "preparing",
            "pending",
            "running",
            "success",
            "failed",
            "canceled",
            "skipped",
            "manual",
            "scheduled",
        ]
        | None = None,  # fmt: skip
        updated_after: datetime | None = None,
        limit: LimitParam = 20,
        cursor: CursorParam = None,
    ) -> ToolOutcome:
        return await api().list_pipelines(
            project=project or "", ref=ref, status=status, updated_after=updated_after,
            limit=limit, cursor=cursor,
        )  # fmt: skip

    @readonly_tool
    async def gitlab_get_pipeline(
        project: ProjectRequired,
        pipeline_id: Annotated[str, Field(pattern=r"^[0-9]{1,18}$")],
        include_failed_job_trace: bool = False,
        max_bytes: MaxBytesParam = 65536,
    ) -> ToolOutcome:
        return await api().get_pipeline(
            project=project or "", pipeline_id=pipeline_id,
            include_failed_job_trace=include_failed_job_trace, max_bytes=max_bytes,
        )  # fmt: skip

    tools: dict[str, Callable[..., Any]] = {
        fn.__name__: fn
        for fn in (
            gitlab_search_projects,
            gitlab_search_code,
            gitlab_get_file,
            gitlab_list_repository_tree,
            gitlab_list_commits,
            gitlab_list_merge_requests,
            gitlab_get_merge_request,
            gitlab_list_issues,
            gitlab_get_issue,
            gitlab_list_pipelines,
            gitlab_get_pipeline,
        )
    }
    return tools


def register_tools(mcp: FastMCP, api: ApiFactory, common: CommonSettings | None = None) -> None:
    for name, fn in make_tools(api).items():
        register_tool(
            mcp,
            fn,
            name=name,
            description=TOOL_DESCRIPTIONS[name],
            source=SOURCE,
            common_settings=common,
        )
