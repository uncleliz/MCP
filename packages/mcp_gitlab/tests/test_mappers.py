"""T-024: mappers — GitLab payloads -> every GitLab schema of the contract + web Citation."""

from __future__ import annotations

from gitlab_helpers import BASE, load_fixture
from mcp_common.envelope import SourceType

from mcp_gitlab import mappers

P = "team/payment-service"
WEB = f"{BASE}/{P}"


def test_FR_002_AC_001_project() -> None:
    item, citation = mappers.map_project(load_fixture("projects.json")[0], citation_ref=0)
    assert item["id"] == "42" and item["path_with_namespace"] == P
    assert item["web_url"] == WEB and item["default_branch"] == "main"
    assert item["visibility"] == "private" and item["archived"] is False
    assert citation.source_type == SourceType.GITLAB
    assert citation.uri == WEB and citation.locator == {"project": P}


def test_code_hit_links_to_web_blob_line_not_api() -> None:
    hit = load_fixture("search_blobs.json")[0]
    item, citation = mappers.map_code_hit(
        hit, project_path=P, project_web_url=WEB, excerpt="x", citation_ref=2
    )
    assert item["web_url"] == f"{WEB}/-/blob/main/src/retry.py#L12"
    assert "/api/v4" not in item["web_url"]
    assert item["start_line"] == 12 and item["project_path"] == P and item["citation_ref"] == 2
    assert citation.locator == {"project": P, "path": "src/retry.py", "ref": "main", "line": 12}
    assert citation.uri == item["web_url"]


def test_blob_url_quotes_unsafe_characters() -> None:
    assert mappers.blob_url(WEB, "feat/x", "dir/my file#1.py") == (
        f"{WEB}/-/blob/feat/x/dir/my%20file%231.py"
    )


def test_file_mapping() -> None:
    raw = load_fixture("file_retry.json")
    item, citation = mappers.map_file(
        raw,
        project_path=P,
        project_web_url=WEB,
        ref="main",
        content="wrapped",
        truncated=False,
        citation_ref=0,
    )
    assert item == {
        "project_path": P,
        "path": "src/retry.py",
        "ref": "main",
        "size_bytes": raw["size"],
        "content": "wrapped",
        "truncated": False,
        "last_commit_id": "abc1234def5678",
        "web_url": f"{WEB}/-/blob/main/src/retry.py",
        "citation_ref": 0,
    }
    assert citation.locator == {"project": P, "path": "src/retry.py", "ref": "main"}


def test_tree_entries_blob_tree_and_submodule() -> None:
    entries = load_fixture("tree.json")
    by_name = {
        e["name"]: mappers.map_tree_entry(
            e, project_path=P, project_web_url=WEB, ref="main", citation_ref=i
        )[0]
        for i, e in enumerate(entries)
    }
    assert by_name["src"]["web_url"] == f"{WEB}/-/tree/main/src"
    assert by_name["README.md"]["web_url"] == f"{WEB}/-/blob/main/README.md"
    assert by_name["vendor"]["web_url"] is None  # submodule: no openable URL
    assert by_name["src"]["type"] == "tree" and by_name["src"]["mode"] == "040000"


def test_commit() -> None:
    raw = load_fixture("commits.json")[0]
    item, citation = mappers.map_commit(raw, project=P, message="m", citation_ref=0)
    assert item["short_id"] == "abc1234" and item["title"] == "Fix retry backoff"
    assert item["web_url"].endswith("/-/commit/" + raw["id"]) and item["message"] == "m"
    assert citation.locator == {"project": P, "sha": raw["id"]}


def test_merge_request_list_and_detail_shapes() -> None:
    raw = load_fixture("mrs.json")[0]
    item, citation = mappers.map_merge_request(
        raw, project_path=P, description=None, changed_files=None, notes=None, citation_ref=0
    )
    assert item["iid"] == "12" and item["project_path"] == P and item["state"] == "opened"
    assert item["author"] == "dana" and item["labels"] == ["payments"]
    assert item["changed_files"] is None and item["notes"] is None
    assert item["web_url"] == f"{WEB}/-/merge_requests/12"
    assert citation.locator == {"project": P, "iid": "12"} and "!12" in citation.label


def test_merge_request_old_work_in_progress_field_maps_to_draft() -> None:
    raw = {**load_fixture("mrs.json")[0], "work_in_progress": True}
    del raw["draft"]
    item, _ = mappers.map_merge_request(
        raw, project_path=P, description=None, changed_files=None, notes=None, citation_ref=0
    )
    assert item["draft"] is True


def test_issue() -> None:
    raw = load_fixture("issues.json")[0]
    item, citation = mappers.map_issue(
        raw, project_path=P, description=None, notes=None, citation_ref=0
    )
    assert item["iid"] == "7" and item["assignees"] == ["dana"] and item["labels"] == ["bug"]
    assert item["closed_at"] is None and item["notes"] is None
    assert citation.locator == {"project": P, "iid": "7"} and "#7" in citation.label


def test_note_and_changed_file() -> None:
    note = mappers.map_note(load_fixture("mr_12_notes.json")[1], body="b")
    assert note == {
        "id": "502",
        "author": "dana",
        "created_at": "2026-09-12T08:00:00.000Z",
        "system": True,
        "body": "b",
    }
    change = load_fixture("mr_12_changes.json")["changes"][2]
    assert mappers.map_changed_file(change, diff_excerpt="d", truncated=True) == {
        "old_path": None,
        "new_path": "docs/new.md",
        "new_file": True,
        "deleted_file": False,
        "renamed_file": False,
        "diff_excerpt": "d",
        "truncated": True,
    }


def test_pipeline_and_job() -> None:
    raw = load_fixture("pipeline_900.json")
    job = mappers.map_job(
        load_fixture("pipeline_900_jobs.json")[0], trace_excerpt="t", trace_truncated=True
    )
    assert job["id"] == "5001" and job["duration_s"] == 120.5
    assert job["trace_excerpt"] == "t" and job["trace_truncated"] is True
    item, citation = mappers.map_pipeline(
        raw, project_path=P, jobs=[job], duration_s=600.0, citation_ref=0
    )
    assert item["id"] == "900" and item["jobs"] == [job] and item["duration_s"] == 600.0
    assert item["web_url"] == f"{WEB}/-/pipelines/900"
    assert citation.locator == {"project": P, "pipeline_id": "900"}


def test_project_path_from_web_url() -> None:
    assert mappers.project_path_from_web_url(f"{WEB}/-/merge_requests/3", BASE) == P
    assert mappers.project_path_from_web_url(f"{BASE}/a/b/c/-/issues/9", BASE) == "a/b/c"
    assert mappers.project_path_from_web_url("https://elsewhere/x/y/-/issues/1", BASE) == "x/y"
