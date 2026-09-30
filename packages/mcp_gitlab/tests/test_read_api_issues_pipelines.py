"""T-024/T-027: read_api for issues and pipelines. AC: FR-002/AC-001, FR-002/AC-002."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
import respx
from gitlab_helpers import API, BASE, PROJ, json_response, load_fixture
from mcp_common.errors import ToolError
from mcp_gitlab.read_api import GitLabReadApi

P = "team/payment-service"
WEB = f"{BASE}/team/payment-service"


@pytest.mark.asyncio
async def test_FR_002_AC_001_list_issues(
    read_api: GitLabReadApi, readonly_respx_router: respx.MockRouter
) -> None:
    route = readonly_respx_router.get(f"{API}/projects/{PROJ}/issues").mock(
        return_value=json_response("issues.json")
    )
    outcome = await read_api.list_issues(
        project=P,
        search="retry",
        state="opened",
        labels=["bug"],
        assignee_username="dana",
        updated_after=datetime(2026, 9, 1, tzinfo=UTC),
    )
    result = outcome.result
    assert [i["iid"] for i in result.items] == ["7", "6"]
    assert result.items[0]["assignees"] == ["dana"] and result.items[0]["notes"] is None
    assert result.citations[0].uri == f"{WEB}/-/issues/7"
    params = route.calls.last.request.url.params
    assert params["state"] == "opened" and params["labels"] == "bug"
    assert params["assignee_username"] == "dana" and params["search"] == "retry"


@pytest.mark.asyncio
async def test_list_issues_instance_wide_empty_and_missing_project(
    read_api: GitLabReadApi, readonly_respx_router: respx.MockRouter
) -> None:
    route = readonly_respx_router.get(f"{API}/issues").mock(
        return_value=json_response("issues.json")
    )
    outcome = await read_api.list_issues()
    assert route.calls.last.request.url.params["scope"] == "all"
    assert outcome.result.items[0]["project_path"] == P
    route.mock(return_value=httpx.Response(200, json=[]))
    assert (await read_api.list_issues()).result.status.value == "empty"
    readonly_respx_router.get(f"{API}/projects/ghost/issues").mock(
        return_value=json_response("error_404.json", status=404)
    )
    assert (await read_api.list_issues(project="ghost")).result.status.value == "not_found"


@pytest.mark.parametrize(
    ("kwargs", "field"),
    [
        ({"state": "merged"}, "state"),
        ({"labels": ["x"] * 11}, "labels"),
        ({"updated_after": datetime(2026, 1, 1)}, "updated_after"),
        ({"limit": 0}, "limit"),
    ],
)
@pytest.mark.asyncio
async def test_list_issues_bounds(read_api: GitLabReadApi, kwargs: dict, field: str) -> None:
    with pytest.raises(ToolError) as exc:
        await read_api.list_issues(**kwargs)
    assert exc.value.details["field"] == field


@pytest.mark.asyncio
async def test_get_issue_with_notes_by_default(
    read_api: GitLabReadApi, gitlab: respx.MockRouter
) -> None:
    gitlab.get(f"{API}/projects/{PROJ}/issues/7").mock(return_value=json_response("issue_7.json"))
    gitlab.get(f"{API}/projects/{PROJ}/issues/7/notes").mock(
        return_value=json_response("issue_7_notes.json")
    )
    outcome = await read_api.get_issue(project=P, iid="7")
    item = outcome.result.items[0]
    assert item["description"].startswith("<untrusted-content")
    assert [n["id"] for n in item["notes"]] == ["601"] and "Reproduced" in item["notes"][0]["body"]
    no_notes = await read_api.get_issue(project=P, iid="7", include_notes=False)
    assert no_notes.result.items[0]["notes"] is None


@pytest.mark.asyncio
async def test_FR_002_AC_002_unknown_issue_not_found(
    read_api: GitLabReadApi, gitlab: respx.MockRouter
) -> None:
    gitlab.get(f"{API}/projects/{PROJ}/issues/404").mock(return_value=httpx.Response(404, json={}))
    outcome = await read_api.get_issue(project=P, iid="404")
    assert outcome.result.status.value == "not_found" and outcome.identifier == f"{P}#404"


@pytest.mark.asyncio
async def test_get_issue_bounds(read_api: GitLabReadApi) -> None:
    with pytest.raises(ToolError) as exc:
        await read_api.get_issue(project=P, iid="x")
    assert exc.value.details["field"] == "iid"


# ---- pipelines ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_FR_002_AC_001_list_pipelines(
    read_api: GitLabReadApi, gitlab: respx.MockRouter
) -> None:
    route = gitlab.get(f"{API}/projects/{PROJ}/pipelines").mock(
        return_value=json_response("pipelines.json")
    )
    outcome = await read_api.list_pipelines(
        project=P, ref="main", status="failed", updated_after=datetime(2026, 9, 1, tzinfo=UTC)
    )
    result = outcome.result
    assert [i["id"] for i in result.items] == ["900", "899"]
    assert result.items[0]["project_path"] == P and result.items[0]["jobs"] is None
    assert result.citations[0].uri == f"{WEB}/-/pipelines/900"
    params = route.calls.last.request.url.params
    assert params["ref"] == "main" and params["status"] == "failed" and "updated_after" in params


@pytest.mark.asyncio
async def test_list_pipelines_empty_not_found_and_bounds(
    read_api: GitLabReadApi, gitlab: respx.MockRouter, readonly_respx_router: respx.MockRouter
) -> None:
    route = gitlab.get(f"{API}/projects/{PROJ}/pipelines").mock(
        return_value=httpx.Response(200, json=[])
    )
    assert (await read_api.list_pipelines(project=P)).result.status.value == "empty"
    route.mock(return_value=httpx.Response(404, json={}))
    assert (await read_api.list_pipelines(project=P)).result.status.value == "not_found"
    with pytest.raises(ToolError) as exc:
        await read_api.list_pipelines(project=P, status="exploded")
    assert exc.value.details["field"] == "status"
    with pytest.raises(ToolError) as exc:
        await read_api.list_pipelines(project=P, updated_after=datetime(2026, 1, 1))
    assert exc.value.details["field"] == "updated_after"


def _mock_pipeline(router: respx.MockRouter) -> None:
    router.get(f"{API}/projects/{PROJ}/pipelines/900").mock(
        return_value=json_response("pipeline_900.json")
    )
    router.get(f"{API}/projects/{PROJ}/pipelines/900/jobs").mock(
        return_value=json_response("pipeline_900_jobs.json")
    )


@pytest.mark.asyncio
async def test_get_pipeline_with_jobs_no_trace_by_default(
    read_api: GitLabReadApi, gitlab: respx.MockRouter
) -> None:
    _mock_pipeline(gitlab)
    outcome = await read_api.get_pipeline(project=P, pipeline_id="900")
    item = outcome.result.items[0]
    assert item["duration_s"] == 600 and item["status"] == "failed"
    assert [j["name"] for j in item["jobs"]] == ["unit-tests", "build"]
    assert all(j["trace_excerpt"] is None for j in item["jobs"])
    assert not any("/trace" in str(c.request.url) for c in gitlab.calls)


@pytest.mark.asyncio
async def test_get_pipeline_failed_job_trace_is_tail_and_redacted(
    read_api: GitLabReadApi, gitlab: respx.MockRouter
) -> None:
    _mock_pipeline(gitlab)
    gitlab.get(f"{API}/projects/{PROJ}/jobs/5001/trace").mock(
        return_value=httpx.Response(200, text=load_fixture("job_5001_trace.txt"))
    )
    outcome = await read_api.get_pipeline(
        project=P, pipeline_id="900", include_failed_job_trace=True, max_bytes=1024
    )
    jobs = {j["name"]: j for j in outcome.result.items[0]["jobs"]}
    trace = jobs["unit-tests"]["trace_excerpt"]
    assert "ERROR: test_retry failed" in trace  # the tail (where failures are) is kept
    assert "abcdEFGH1234" not in trace and jobs["unit-tests"]["trace_truncated"] is True
    assert "step 0 ok" not in trace
    assert jobs["build"]["trace_excerpt"] is None  # only failed jobs get a trace
    assert outcome.result.meta.redactions >= 1
    assert outcome.result.status.value == "partial"


@pytest.mark.asyncio
async def test_get_pipeline_trace_missing_is_tolerated(
    read_api: GitLabReadApi, gitlab: respx.MockRouter
) -> None:
    _mock_pipeline(gitlab)
    gitlab.get(f"{API}/projects/{PROJ}/jobs/5001/trace").mock(
        return_value=httpx.Response(404, json={})
    )
    outcome = await read_api.get_pipeline(
        project=P, pipeline_id="900", include_failed_job_trace=True
    )
    assert outcome.result.items[0]["jobs"][0]["trace_excerpt"] is None


@pytest.mark.asyncio
async def test_FR_002_AC_002_unknown_pipeline_not_found_and_bounds(
    read_api: GitLabReadApi, gitlab: respx.MockRouter
) -> None:
    gitlab.get(f"{API}/projects/{PROJ}/pipelines/1").mock(return_value=httpx.Response(404, json={}))
    outcome = await read_api.get_pipeline(project=P, pipeline_id="1")
    assert outcome.result.status.value == "not_found"
    with pytest.raises(ToolError) as exc:
        await read_api.get_pipeline(project=P, pipeline_id="abc")
    assert exc.value.details["field"] == "pipeline_id"
