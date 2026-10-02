"""T-092 / T-094: Jira `read_api.py` — tool bounds, empty vs not_found, opaque cursor
round-trip, and mapper output shape (both flavors).

AC: FR-017/AC-001, FR-017/AC-002 (ADR-0019 / ADR-0004).
"""

from __future__ import annotations

import httpx
import pytest
import respx
from mcp_common.envelope import ResultStatus, SourceType
from mcp_common.errors import ToolError
from mcp_common.tooling import decode_cursor, encode_cursor
from mcp_jira.read_api import JiraReadApi

from .jira_helpers import (
    board_sprints_url,
    issue_url,
    project_search_url,
    project_url,
    search_url,
    sprint_url,
)


@pytest.mark.asyncio
async def test_FR_017_AC_001_search_ok_maps_issue_and_citation(
    read_api: JiraReadApi, readonly_respx_router: respx.MockRouter, fixture, base, flavor
) -> None:
    name = "search_cloud.json" if flavor == "cloud" else "search_server.json"
    readonly_respx_router.get(search_url(base, flavor)).mock(
        return_value=httpx.Response(200, json=fixture(name))
    )
    outcome = await read_api.search_issues(jql="project = PAY", limit=1)
    result = outcome.result
    assert result.status == ResultStatus.OK
    item = result.items[0]
    assert item["key"] == "PAY-1234"
    assert item["status"] == "In Progress"
    assert item["url"] == f"{base}/browse/PAY-1234"
    assert result.citations[0].source_type == SourceType.JIRA
    assert result.citations[0].uri == f"{base}/browse/PAY-1234"
    assert result.meta.source == SourceType.JIRA
    assert result.meta.query_echo == {"jql": "project = PAY", "limit": 1}


@pytest.mark.asyncio
async def test_search_cursor_round_trip_is_opaque(
    read_api: JiraReadApi, readonly_respx_router: respx.MockRouter, fixture, base, flavor
) -> None:
    name = "search_cloud.json" if flavor == "cloud" else "search_server.json"
    readonly_respx_router.get(search_url(base, flavor)).mock(
        return_value=httpx.Response(200, json=fixture(name))
    )
    outcome = await read_api.search_issues(jql="project = PAY", limit=1)
    cursor = outcome.result.meta.next_cursor
    assert cursor is not None and outcome.result.meta.has_more is True
    # The cursor is opaque base64 the client never parses — but it decodes to the flavor state.
    state = decode_cursor(cursor, source="jira")
    assert ("nextPageToken" in state) or ("startAt" in state)


@pytest.mark.asyncio
async def test_FR_017_AC_002_search_empty_has_no_citations(
    read_api: JiraReadApi, readonly_respx_router: respx.MockRouter, fixture, base, flavor
) -> None:
    readonly_respx_router.get(search_url(base, flavor)).mock(
        return_value=httpx.Response(200, json=fixture("search_empty.json"))
    )
    result = (await read_api.search_issues(jql="project = NONE")).result
    assert result.status == ResultStatus.EMPTY
    assert result.items == [] and result.citations == []


@pytest.mark.asyncio
async def test_get_issue_ok(
    read_api: JiraReadApi, readonly_respx_router: respx.MockRouter, fixture, base, flavor
) -> None:
    readonly_respx_router.get(issue_url(base, flavor, "PAY-1234")).mock(
        return_value=httpx.Response(200, json=fixture("issue.json"))
    )
    result = (await read_api.get_issue(key="PAY-1234")).result
    assert result.status == ResultStatus.OK
    assert result.items[0]["key"] == "PAY-1234"


@pytest.mark.asyncio
async def test_FR_017_AC_002_get_issue_404_is_not_found(
    read_api: JiraReadApi, readonly_respx_router: respx.MockRouter, base, flavor
) -> None:
    readonly_respx_router.get(issue_url(base, flavor, "ZZZ-9999")).mock(
        return_value=httpx.Response(404, json={"errorMessages": ["gone"]})
    )
    result = (await read_api.get_issue(key="ZZZ-9999")).result
    assert result.status == ResultStatus.NOT_FOUND
    assert result.items == [] and result.citations == []


@pytest.mark.parametrize("bad_key", ["pay-1", "PAY", "1234", "PAY_1", "P-1 OR 1=1"])
@pytest.mark.asyncio
async def test_get_issue_rejects_bad_key(read_api: JiraReadApi, bad_key: str) -> None:
    with pytest.raises(ToolError) as exc:
        await read_api.get_issue(key=bad_key)
    assert exc.value.details["field"] == "key"


@pytest.mark.parametrize("bad_limit", [0, 101, -1])
@pytest.mark.asyncio
async def test_search_rejects_bad_limit(read_api: JiraReadApi, bad_limit: int) -> None:
    with pytest.raises(ToolError) as exc:
        await read_api.search_issues(jql="x", limit=bad_limit)
    assert exc.value.details["field"] == "limit"


@pytest.mark.asyncio
async def test_search_rejects_empty_and_overlong_jql(read_api: JiraReadApi) -> None:
    with pytest.raises(ToolError):
        await read_api.search_issues(jql="")
    with pytest.raises(ToolError):
        await read_api.search_issues(jql="x" * 2049)


@pytest.mark.asyncio
async def test_list_projects_ok(
    read_api: JiraReadApi, readonly_respx_router: respx.MockRouter, fixture, base, flavor
) -> None:
    if flavor == "cloud":
        readonly_respx_router.get(project_search_url(base, flavor)).mock(
            return_value=httpx.Response(200, json=fixture("projects_cloud.json"))
        )
    else:
        readonly_respx_router.get(project_url(base, flavor)).mock(
            return_value=httpx.Response(200, json=fixture("projects_server.json"))
        )
    result = (await read_api.list_projects()).result
    assert result.status == ResultStatus.OK
    assert result.items[0]["key"] == "PAY"
    assert result.items[0]["url"] == f"{base}/browse/PAY"


@pytest.mark.asyncio
async def test_list_projects_empty_cloud(
    read_api: JiraReadApi, readonly_respx_router: respx.MockRouter, fixture, base, flavor
) -> None:
    if flavor == "cloud":
        readonly_respx_router.get(project_search_url(base, flavor)).mock(
            return_value=httpx.Response(200, json=fixture("projects_empty.json"))
        )
    else:
        readonly_respx_router.get(project_url(base, flavor)).mock(
            return_value=httpx.Response(200, json=[])
        )
    result = (await read_api.list_projects()).result
    assert result.status == ResultStatus.EMPTY


@pytest.mark.asyncio
async def test_get_sprint_ok_and_not_found(
    read_api: JiraReadApi, readonly_respx_router: respx.MockRouter, fixture, base
) -> None:
    readonly_respx_router.get(sprint_url(base, 42)).mock(
        return_value=httpx.Response(200, json=fixture("sprint.json"))
    )
    ok = (await read_api.get_sprint(sprint_id=42)).result
    assert ok.status == ResultStatus.OK
    assert ok.items[0]["sprint_id"] == 42
    assert ok.items[0]["state"] == "active"
    assert ok.items[0]["url"].endswith("rapidView=7&sprint=42")

    readonly_respx_router.get(sprint_url(base, 999)).mock(
        return_value=httpx.Response(404, json={"errorMessages": ["gone"]})
    )
    nf = (await read_api.get_sprint(sprint_id=999)).result
    assert nf.status == ResultStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_list_board_sprints_ok_empty_not_found(
    read_api: JiraReadApi, readonly_respx_router: respx.MockRouter, fixture, base
) -> None:
    readonly_respx_router.get(board_sprints_url(base, 7)).mock(
        return_value=httpx.Response(200, json=fixture("board_sprints.json"))
    )
    ok = (await read_api.list_board_sprints(board_id=7)).result
    assert ok.status == ResultStatus.OK
    assert ok.items[0]["sprint_id"] == 42

    readonly_respx_router.get(board_sprints_url(base, 8)).mock(
        return_value=httpx.Response(200, json=fixture("board_sprints_empty.json"))
    )
    empty = (await read_api.list_board_sprints(board_id=8)).result
    assert empty.status == ResultStatus.EMPTY

    readonly_respx_router.get(board_sprints_url(base, 999)).mock(
        return_value=httpx.Response(404, json={"errorMessages": ["gone"]})
    )
    nf = (await read_api.list_board_sprints(board_id=999)).result
    assert nf.status == ResultStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_board_sprints_rejects_bad_state(read_api: JiraReadApi) -> None:
    with pytest.raises(ToolError) as exc:
        await read_api.list_board_sprints(board_id=7, state="deleted")
    assert exc.value.details["field"] == "state"


@pytest.mark.asyncio
async def test_bad_cursor_rejected(read_api: JiraReadApi) -> None:
    with pytest.raises(ToolError) as exc:
        await read_api.search_issues(jql="x", cursor="not-base64!!")
    assert exc.value.details["field"] == "cursor"


def test_encode_decode_cursor_symmetry() -> None:
    assert decode_cursor(encode_cursor({"startAt": 20}), source="jira") == {"startAt": 20}
