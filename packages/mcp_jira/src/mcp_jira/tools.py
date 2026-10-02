"""The five Jira tools (FR-017). Names, input schemas and outputs are dictated by
`api-contract.yaml`; `tools.snapshot.json` + `tests/test_contract.py` keep them in sync.

Every tool is read-only (`x-readonly: true`, `x-side-effects: none`, ADR-0019): there is NO
`jira_create_issue`/`jira_transition_issue`/`jira_add_comment`. Each function only validates and
forwards to `JiraReadApi`; deadline, error envelope and text rendering are applied by
`register_tool`.
"""

from collections.abc import Callable
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from mcp_common.config import CommonSettings
from mcp_common.readonly import readonly_tool
from mcp_common.tooling import CursorParam, LimitParam, ToolOutcome, register_tool
from pydantic import Field

from mcp_jira.client import SOURCE
from mcp_jira.read_api import JiraReadApi

__all__ = ["TOOL_DESCRIPTIONS", "make_tools", "register_tools"]

TOOL_DESCRIPTIONS: dict[str, str] = {
    "jira_search_issues": (
        "Tìm issue Jira bằng JQL bounded (chỉ đọc). Trả về issue kèm key, summary, status và URL "
        "dùng làm citation. status=empty khi không có issue nào khớp."
    ),
    "jira_get_issue": (
        "Lấy một issue Jira theo key (ví dụ PAY-1234). status=not_found nếu key không tồn tại "
        "hoặc không có quyền đọc."
    ),
    "jira_list_projects": "Liệt kê các project Jira mà caller đọc được. status=empty khi không có.",
    "jira_get_sprint": (
        "Lấy một sprint theo id (Agile API). status=not_found nếu id không tồn tại."
    ),
    "jira_list_board_sprints": (
        "Liệt kê sprint của một board, lọc theo state. status=empty khi board không có sprint; "
        "status=not_found khi board không tồn tại."
    ),
}

IssueKey = Annotated[
    str,
    Field(
        pattern=r"^[A-Z][A-Z0-9]+-[0-9]+$",
        max_length=64,
        description="Key của issue, ví dụ PAY-1234.",
    ),
]

ApiFactory = Callable[[], JiraReadApi]


def make_tools(api: ApiFactory) -> dict[str, Callable[..., Any]]:
    @readonly_tool
    async def jira_search_issues(
        jql: Annotated[
            str,
            Field(min_length=1, max_length=2048, description="JQL bounded (chỉ đọc)."),
        ],
        limit: LimitParam = 20,
        cursor: CursorParam = None,
    ) -> ToolOutcome:
        return await api().search_issues(jql=jql, limit=limit, cursor=cursor)

    @readonly_tool
    async def jira_get_issue(key: IssueKey) -> ToolOutcome:
        return await api().get_issue(key=key)

    @readonly_tool
    async def jira_list_projects(
        limit: LimitParam = 20,
        cursor: CursorParam = None,
    ) -> ToolOutcome:
        return await api().list_projects(limit=limit, cursor=cursor)

    @readonly_tool
    async def jira_get_sprint(
        sprint_id: Annotated[int, Field(ge=1, description="Id của sprint.")],
    ) -> ToolOutcome:
        return await api().get_sprint(sprint_id=sprint_id)

    @readonly_tool
    async def jira_list_board_sprints(
        board_id: Annotated[int, Field(ge=1, description="Id của board.")],
        state: Annotated[
            str, Field(json_schema_extra={"enum": ["active", "future", "closed", "all"]})
        ] = "active",
        limit: LimitParam = 20,
        cursor: CursorParam = None,
    ) -> ToolOutcome:
        return await api().list_board_sprints(
            board_id=board_id, state=state, limit=limit, cursor=cursor
        )

    return {
        "jira_search_issues": jira_search_issues,
        "jira_get_issue": jira_get_issue,
        "jira_list_projects": jira_list_projects,
        "jira_get_sprint": jira_get_sprint,
        "jira_list_board_sprints": jira_list_board_sprints,
    }


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
