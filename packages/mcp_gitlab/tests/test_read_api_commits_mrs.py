"""T-024/T-026: read_api for commits and merge requests. AC: FR-002/AC-001, FR-002/AC-002."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
import respx
from gitlab_helpers import API, BASE, PROJ, json_response, load_fixture
from mcp_common.config import CommonSettings
from mcp_common.errors import ErrorCode, ToolError
from mcp_gitlab.read_api import GitLabReadApi

WEB = f"{BASE}/team/payment-service"
P = "team/payment-service"


@pytest.mark.asyncio
async def test_FR_002_AC_001_list_commits(
    read_api: GitLabReadApi, gitlab: respx.MockRouter
) -> None:
    route = gitlab.get(f"{API}/projects/{PROJ}/repository/commits").mock(
        return_value=json_response("commits.json", headers={"X-Next-Page": "2"})
    )
    outcome = await read_api.list_commits(
        project=P,
        ref="main",
        path="src",
        since=datetime(2026, 9, 1, tzinfo=UTC),
        until=datetime(2026, 9, 30, tzinfo=UTC),
        limit=2,
    )
    result = outcome.result
    assert [i["short_id"] for i in result.items] == ["abc1234", "1112223"]
    assert result.citations[0].uri.endswith("/-/commit/" + result.items[0]["id"])
    assert result.meta.has_more
    params = route.calls.last.request.url.params
    assert params["ref_name"] == "main" and params["path"] == "src"
    assert params["since"].startswith("2026-09-01T00:00:00") and "until" in params
    assert "Use exponential delay" in result.items[0]["message"]  # wrapped message


@pytest.mark.asyncio
async def test_commits_head_ref_omits_ref_name_and_empty_and_not_found(
    read_api: GitLabReadApi, readonly_respx_router: respx.MockRouter
) -> None:
    route = readonly_respx_router.get(f"{API}/projects/{PROJ}/repository/commits")
    route.mock(return_value=httpx.Response(200, json=[]))
    outcome = await read_api.list_commits(project=P)
    assert "ref_name" not in route.calls.last.request.url.params
    assert outcome.result.status.value == "empty"
    route.mock(return_value=json_response("error_404.json", status=404))
    assert (await read_api.list_commits(project=P)).result.status.value == "not_found"


@pytest.mark.parametrize(
    ("kwargs", "field"),
    [
        ({"project": P, "since": datetime(2026, 1, 1)}, "since"),
        ({"project": P, "until": datetime(2026, 1, 1)}, "until"),
        ({"project": P, "limit": 101}, "limit"),
        ({"project": ""}, "project"),
    ],
)
@pytest.mark.asyncio
async def test_commits_bounds(read_api: GitLabReadApi, kwargs: dict, field: str) -> None:
    with pytest.raises(ToolError) as exc:
        await read_api.list_commits(**kwargs)
    assert exc.value.details["field"] == field


# ---- merge requests ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_FR_002_AC_001_list_merge_requests_project_scoped(
    read_api: GitLabReadApi, readonly_respx_router: respx.MockRouter
) -> None:
    route = readonly_respx_router.get(f"{API}/projects/{PROJ}/merge_requests").mock(
        return_value=json_response("mrs.json")
    )
    outcome = await read_api.list_merge_requests(
        project=P,
        search="retry",
        state="opened",
        author_username="dana",
        target_branch="main",
        updated_after=datetime(2026, 9, 1, tzinfo=UTC),
        labels=["payments", "bug"],
    )
    result = outcome.result
    assert [i["iid"] for i in result.items] == ["12", "11"]
    assert all(i["project_path"] == P for i in result.items)
    assert result.items[0]["changed_files"] is None and result.items[0]["notes"] is None
    assert result.citations[0].uri == f"{WEB}/-/merge_requests/12"
    params = route.calls.last.request.url.params
    assert params["state"] == "opened" and params["labels"] == "payments,bug"
    assert params["author_username"] == "dana" and params["target_branch"] == "main"
    assert params["search"] == "retry" and "updated_after" in params


@pytest.mark.asyncio
async def test_list_merge_requests_instance_wide_and_state_all(
    read_api: GitLabReadApi, readonly_respx_router: respx.MockRouter
) -> None:
    route = readonly_respx_router.get(f"{API}/merge_requests").mock(
        return_value=json_response("mrs.json")
    )
    outcome = await read_api.list_merge_requests()
    params = route.calls.last.request.url.params
    assert params["scope"] == "all" and params["state"] == "all"
    assert outcome.result.items[0]["project_path"] == P  # derived from web_url


@pytest.mark.asyncio
async def test_list_merge_requests_empty_and_missing_project(
    read_api: GitLabReadApi, readonly_respx_router: respx.MockRouter
) -> None:
    readonly_respx_router.get(f"{API}/merge_requests").mock(
        return_value=httpx.Response(200, json=[])
    )
    assert (await read_api.list_merge_requests()).result.status.value == "empty"
    readonly_respx_router.get(f"{API}/projects/ghost/merge_requests").mock(
        return_value=json_response("error_404.json", status=404)
    )
    assert (await read_api.list_merge_requests(project="ghost")).result.status.value == "not_found"


@pytest.mark.parametrize(
    ("kwargs", "field"),
    [
        ({"state": "bogus"}, "state"),
        ({"labels": ["x"] * 11}, "labels"),
        ({"search": "x" * 257}, "search"),
        ({"updated_after": datetime(2026, 1, 1)}, "updated_after"),
    ],
)
@pytest.mark.asyncio
async def test_list_merge_requests_bounds(
    read_api: GitLabReadApi, kwargs: dict, field: str
) -> None:
    with pytest.raises(ToolError) as exc:
        await read_api.list_merge_requests(**kwargs)
    assert exc.value.details["field"] == field


def _mock_mr_detail(router: respx.MockRouter) -> None:
    router.get(f"{API}/projects/{PROJ}/merge_requests/12").mock(
        return_value=json_response("mr_12.json")
    )
    router.get(f"{API}/projects/{PROJ}/merge_requests/12/changes").mock(
        return_value=json_response("mr_12_changes.json")
    )
    router.get(f"{API}/projects/{PROJ}/merge_requests/12/notes").mock(
        return_value=json_response("mr_12_notes.json")
    )


@pytest.mark.asyncio
async def test_FR_002_AC_001_get_merge_request_with_changes_and_notes(
    read_api: GitLabReadApi, gitlab: respx.MockRouter
) -> None:
    _mock_mr_detail(gitlab)
    outcome = await read_api.get_merge_request(project=P, iid="12", include_notes=True)
    item = outcome.result.items[0]
    assert item["title"] == "Retry with backoff" and len(outcome.result.items) == 1
    assert item["description"].startswith('<untrusted-content source="gitlab"')
    files = {f["new_path"]: f for f in item["changed_files"]}
    assert "retry_payment" in files["src/retry.py"]["diff_excerpt"]
    assert files["config/prod.env"]["diff_excerpt"] is None  # deny-glob: no diff of secret files
    assert files["docs/new.md"]["new_file"] is True
    assert [n["id"] for n in item["notes"]] == ["501", "502"]
    assert "add tests" in item["notes"][0]["body"] and item["notes"][1]["system"] is True
    assert any("deny-glob" in w for w in outcome.result.meta.warnings)
    assert outcome.result.citations[0].uri == f"{WEB}/-/merge_requests/12"


@pytest.mark.asyncio
async def test_get_merge_request_default_skips_notes_and_flags_skip_calls(
    read_api: GitLabReadApi, gitlab: respx.MockRouter
) -> None:
    _mock_mr_detail(gitlab)
    before = len(gitlab.calls)
    item = (await read_api.get_merge_request(project=P, iid="12")).result.items[0]
    assert item["notes"] is None and item["changed_files"] is not None
    new_calls = list(gitlab.calls)[before:]
    assert not any(str(c.request.url.path).endswith("/notes") for c in new_calls)
    before = len(gitlab.calls)
    item = (
        await read_api.get_merge_request(project=P, iid="12", include_changes=False)
    ).result.items[0]
    assert item["changed_files"] is None
    new_calls = list(gitlab.calls)[before:]
    assert new_calls and not any(str(c.request.url.path).endswith("/changes") for c in new_calls)


@pytest.mark.asyncio
async def test_get_merge_request_truncates_diffs_to_budget(
    client, gitlab: respx.MockRouter
) -> None:
    changes = load_fixture("mr_12_changes.json")
    changes["changes"][0]["diff"] = "+x = 1\n" * 5000
    gitlab.get(f"{API}/projects/{PROJ}/merge_requests/12").mock(
        return_value=json_response("mr_12.json")
    )
    gitlab.get(f"{API}/projects/{PROJ}/merge_requests/12/changes").mock(
        return_value=httpx.Response(200, json=changes)
    )
    api = GitLabReadApi(client, CommonSettings())
    outcome = await api.get_merge_request(project=P, iid="12", max_bytes=1024)
    assert outcome.result.status.value == "partial" and outcome.result.meta.truncated
    first = outcome.result.items[0]["changed_files"][0]
    assert first["truncated"] is True


@pytest.mark.asyncio
async def test_get_merge_request_redacts_secrets(
    read_api: GitLabReadApi, gitlab: respx.MockRouter
) -> None:
    mr = {**load_fixture("mr_12.json"), "description": "key AKIAABCDEFGHIJKLMNOP leaked"}
    gitlab.get(f"{API}/projects/{PROJ}/merge_requests/12").mock(
        return_value=httpx.Response(200, json=mr)
    )
    gitlab.get(f"{API}/projects/{PROJ}/merge_requests/12/changes").mock(
        return_value=httpx.Response(200, json={"changes": []})
    )
    outcome = await read_api.get_merge_request(project=P, iid="12")
    assert "AKIA" not in outcome.result.items[0]["description"]
    assert outcome.result.meta.redactions == 1


@pytest.mark.asyncio
async def test_FR_002_AC_002_unknown_merge_request_is_not_found(
    read_api: GitLabReadApi, gitlab: respx.MockRouter
) -> None:
    gitlab.get(f"{API}/projects/{PROJ}/merge_requests/999").mock(
        return_value=httpx.Response(404, json={"message": "404 Not found"})
    )
    outcome = await read_api.get_merge_request(project=P, iid="999")
    assert outcome.result.status.value == "not_found"
    assert outcome.identifier == f"{P}!999"


@pytest.mark.asyncio
async def test_other_errors_propagate(read_api: GitLabReadApi, gitlab: respx.MockRouter) -> None:
    gitlab.get(f"{API}/projects/{PROJ}/merge_requests/5").mock(
        return_value=httpx.Response(403, json={})
    )
    with pytest.raises(ToolError) as exc:
        await read_api.get_merge_request(project=P, iid="5")
    assert exc.value.code == ErrorCode.FORBIDDEN


@pytest.mark.parametrize(
    ("kwargs", "field"),
    [
        ({"project": P, "iid": "abc"}, "iid"),
        ({"project": P, "iid": "1" * 13}, "iid"),
        ({"project": "", "iid": "1"}, "project"),
        ({"project": P, "iid": "1", "max_bytes": 1}, "max_bytes"),
    ],
)
@pytest.mark.asyncio
async def test_get_merge_request_bounds(read_api: GitLabReadApi, kwargs: dict, field: str) -> None:
    with pytest.raises(ToolError) as exc:
        await read_api.get_merge_request(**kwargs)
    assert exc.value.details["field"] == field
