"""Constants and response helpers shared by the mcp-gitlab tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import respx

FIXTURES = Path(__file__).parent / "fixtures" / "gitlab"
BASE = "https://gitlab.example.test"
API = f"{BASE}/api/v4"
PROJ = "team%2Fpayment-service"


def load_fixture(name: str) -> Any:
    path = FIXTURES / name
    if name.endswith(".json"):
        return json.loads(path.read_text(encoding="utf-8"))
    return path.read_text(encoding="utf-8")


def json_response(name: str, status: int = 200, headers: dict[str, str] | None = None):
    return httpx.Response(status, json=load_fixture(name), headers=headers)


P = "team/payment-service"
PR = f"{API}/projects/{PROJ}"


def mock_ok(router: respx.MockRouter) -> None:
    router.get(f"{API}/projects").mock(return_value=json_response("projects.json"))
    router.get(f"{API}/search").mock(return_value=json_response("search_blobs.json"))
    router.get(url__regex=r".*/repository/files/.*").mock(
        return_value=json_response("file_retry.json")
    )
    router.get(f"{PR}/repository/tree").mock(return_value=json_response("tree.json"))
    router.get(f"{PR}/repository/commits").mock(return_value=json_response("commits.json"))
    router.get(f"{PR}/merge_requests").mock(return_value=json_response("mrs.json"))
    router.get(f"{PR}/merge_requests/12").mock(return_value=json_response("mr_12.json"))
    router.get(f"{PR}/merge_requests/12/changes").mock(
        return_value=json_response("mr_12_changes.json")
    )
    router.get(f"{PR}/merge_requests/12/notes").mock(return_value=json_response("mr_12_notes.json"))
    router.get(f"{PR}/issues").mock(return_value=json_response("issues.json"))
    router.get(f"{PR}/issues/7").mock(return_value=json_response("issue_7.json"))
    router.get(f"{PR}/issues/7/notes").mock(return_value=json_response("issue_7_notes.json"))
    router.get(f"{PR}/pipelines").mock(return_value=json_response("pipelines.json"))
    router.get(f"{PR}/pipelines/900").mock(return_value=json_response("pipeline_900.json"))
    router.get(f"{PR}/pipelines/900/jobs").mock(
        return_value=json_response("pipeline_900_jobs.json")
    )
    router.get(f"{PR}/jobs/5001/trace").mock(
        return_value=httpx.Response(200, text=load_fixture("job_5001_trace.txt"))
    )


OK_CALLS: list[tuple[str, dict[str, Any]]] = [
    ("gitlab_search_projects", {"query": "pay"}),
    ("gitlab_search_code", {"query": "retry"}),
    ("gitlab_get_file", {"project": P, "path": "src/retry.py"}),
    ("gitlab_list_repository_tree", {"project": P}),
    ("gitlab_list_commits", {"project": P}),
    ("gitlab_list_merge_requests", {"project": P}),
    ("gitlab_get_merge_request", {"project": P, "iid": "12", "include_notes": True}),
    ("gitlab_list_issues", {"project": P}),
    ("gitlab_get_issue", {"project": P, "iid": "7"}),
    ("gitlab_list_pipelines", {"project": P}),
    ("gitlab_get_pipeline", {"project": P, "pipeline_id": "900", "include_failed_job_trace": True}),
]
