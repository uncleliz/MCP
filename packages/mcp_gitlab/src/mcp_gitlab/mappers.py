"""GitLab payloads -> contract item schemas + web `Citation` (FR-002/AC-001, FR-015).

Pure functions, no I/O. Free-text fields arrive already redacted/wrapped/truncated from
`read_api.py`. Every `web_url` here is a *web* link a human can open — never an API URL.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote, urlsplit

from mcp_common.envelope import Citation, SourceType

__all__ = [
    "blob_url",
    "map_changed_file",
    "map_code_hit",
    "map_commit",
    "map_file",
    "map_issue",
    "map_job",
    "map_merge_request",
    "map_note",
    "map_pipeline",
    "map_project",
    "map_tree_entry",
    "project_path_from_web_url",
]


def _now() -> datetime:
    return datetime.now(UTC)


def _cite(label: str, uri: str, locator: dict[str, Any]) -> Citation:
    return Citation(
        source_type=SourceType.GITLAB,
        label=label[:512] or uri,
        uri=uri,
        locator=locator,
        retrieved_at=_now(),
    )


def _username(person: Any) -> str | None:
    if isinstance(person, dict):
        value = person.get("username") or person.get("name")
        return str(value) if value else None
    return None


def blob_url(project_web_url: str, ref: str, path: str, line: int | None = None) -> str:
    """`<project>/-/blob/<ref>/<path>[#L<line>]` (ref and path keep their `/`)."""
    url = f"{project_web_url}/-/blob/{quote(ref, safe='/')}/{quote(path, safe='/')}"
    return f"{url}#L{line}" if line else url


def project_path_from_web_url(web_url: str, base_url: str) -> str:
    """`https://host/group/sub/project/-/merge_requests/3` -> `group/sub/project`."""
    path = urlsplit(web_url).path
    base_path = urlsplit(base_url).path.rstrip("/")
    if base_path and path.startswith(base_path):
        path = path[len(base_path) :]
    return path.split("/-/", 1)[0].strip("/")


def map_project(raw: dict[str, Any], *, citation_ref: int) -> tuple[dict[str, Any], Citation]:
    path = str(raw["path_with_namespace"])
    item = {
        "id": str(raw["id"]),
        "path_with_namespace": path,
        "name": raw.get("name", path.rsplit("/", 1)[-1]),
        "description": raw.get("description"),
        "web_url": raw["web_url"],
        "default_branch": raw.get("default_branch"),
        "visibility": raw.get("visibility"),
        "last_activity_at": raw.get("last_activity_at"),
        "archived": raw.get("archived"),
        "citation_ref": citation_ref,
    }
    return item, _cite(path, item["web_url"], {"project": path})


def map_code_hit(
    hit: dict[str, Any],
    *,
    project_path: str,
    project_web_url: str,
    excerpt: str | None,
    citation_ref: int,
) -> tuple[dict[str, Any], Citation]:
    path = str(hit["path"])
    ref = str(hit.get("ref") or "HEAD")
    line = hit.get("startline")
    url = blob_url(project_web_url, ref, path, line)
    item = {
        "project_path": project_path,
        "path": path,
        "ref": ref,
        "start_line": line,
        "excerpt": excerpt,
        "web_url": url,
        "citation_ref": citation_ref,
    }
    label = f"{project_path}: {path}@{ref}" + (f":{line}" if line else "")
    return item, _cite(
        label, url, {"project": project_path, "path": path, "ref": ref, "line": line}
    )


def map_file(
    raw: dict[str, Any],
    *,
    project_path: str,
    project_web_url: str,
    ref: str,
    content: str,
    truncated: bool,
    citation_ref: int,
) -> tuple[dict[str, Any], Citation]:
    path = str(raw.get("file_path") or raw.get("file_name"))
    url = blob_url(project_web_url, ref, path)
    item = {
        "project_path": project_path,
        "path": path,
        "ref": ref,
        "size_bytes": raw.get("size"),
        "content": content,
        "truncated": truncated,
        "last_commit_id": raw.get("last_commit_id"),
        "web_url": url,
        "citation_ref": citation_ref,
    }
    return item, _cite(
        f"{project_path}: {path}@{ref}", url, {"project": project_path, "path": path, "ref": ref}
    )


def map_tree_entry(
    raw: dict[str, Any],
    *,
    project_path: str,
    project_web_url: str,
    ref: str,
    citation_ref: int,
) -> tuple[dict[str, Any], Citation]:
    path = str(raw["path"])
    kind = str(raw["type"])
    if kind == "tree":
        url: str | None = f"{project_web_url}/-/tree/{quote(ref, safe='/')}/{quote(path, safe='/')}"
    elif kind == "blob":
        url = blob_url(project_web_url, ref, path)
    else:  # submodule ("commit"): no openable URL inside this project
        url = None
    item = {
        "path": path,
        "name": raw.get("name", path.rsplit("/", 1)[-1]),
        "type": kind,
        "mode": raw.get("mode"),
        "web_url": url,
        "citation_ref": citation_ref,
    }
    citation = _cite(
        f"{project_path}: {path or '/'}@{ref}",
        url or project_web_url,
        {"project": project_path, "path": path, "ref": ref},
    )
    return item, citation


def map_commit(
    raw: dict[str, Any], *, project: str, message: str | None, citation_ref: int
) -> tuple[dict[str, Any], Citation]:
    sha = str(raw["id"])
    item = {
        "id": sha,
        "short_id": raw.get("short_id", sha[:8]),
        "title": raw.get("title", ""),
        "message": message,
        "author_name": raw.get("author_name"),
        "authored_date": raw.get("authored_date"),
        "committed_date": raw.get("committed_date"),
        "web_url": raw["web_url"],
        "citation_ref": citation_ref,
    }
    return item, _cite(
        f"{project}@{item['short_id']} {item['title']}",
        item["web_url"],
        {"project": project, "sha": sha},
    )


def map_note(raw: dict[str, Any], *, body: str) -> dict[str, Any]:
    return {
        "id": str(raw["id"]),
        "author": _username(raw.get("author")),
        "created_at": raw.get("created_at"),
        "system": bool(raw.get("system", False)),
        "body": body,
    }


def map_changed_file(
    raw: dict[str, Any], *, diff_excerpt: str | None, truncated: bool
) -> dict[str, Any]:
    return {
        "old_path": raw.get("old_path"),
        "new_path": raw["new_path"],
        "new_file": bool(raw.get("new_file", False)),
        "deleted_file": bool(raw.get("deleted_file", False)),
        "renamed_file": bool(raw.get("renamed_file", False)),
        "diff_excerpt": diff_excerpt,
        "truncated": truncated,
    }


def map_merge_request(
    raw: dict[str, Any],
    *,
    project_path: str,
    description: str | None,
    changed_files: list[dict[str, Any]] | None,
    notes: list[dict[str, Any]] | None,
    citation_ref: int,
) -> tuple[dict[str, Any], Citation]:
    iid = str(raw["iid"])
    item = {
        "iid": iid,
        "project_path": project_path,
        "title": raw.get("title", ""),
        "state": raw["state"],
        "draft": bool(raw.get("draft", raw.get("work_in_progress", False))),
        "source_branch": raw.get("source_branch"),
        "target_branch": raw.get("target_branch"),
        "author": _username(raw.get("author")),
        "labels": [str(label) for label in raw.get("labels") or []],
        "created_at": raw.get("created_at"),
        "updated_at": raw.get("updated_at"),
        "merged_at": raw.get("merged_at"),
        "description": description,
        "changed_files": changed_files,
        "notes": notes,
        "web_url": raw["web_url"],
        "citation_ref": citation_ref,
    }
    return item, _cite(
        f"{project_path}!{iid} {item['title']}",
        item["web_url"],
        {"project": project_path, "iid": iid},
    )


def map_issue(
    raw: dict[str, Any],
    *,
    project_path: str,
    description: str | None,
    notes: list[dict[str, Any]] | None,
    citation_ref: int,
) -> tuple[dict[str, Any], Citation]:
    iid = str(raw["iid"])
    item = {
        "iid": iid,
        "project_path": project_path,
        "title": raw.get("title", ""),
        "state": raw["state"],
        "author": _username(raw.get("author")),
        "assignees": [u for u in (_username(a) for a in raw.get("assignees") or []) if u],
        "labels": [str(label) for label in raw.get("labels") or []],
        "created_at": raw.get("created_at"),
        "updated_at": raw.get("updated_at"),
        "closed_at": raw.get("closed_at"),
        "description": description,
        "notes": notes,
        "web_url": raw["web_url"],
        "citation_ref": citation_ref,
    }
    return item, _cite(
        f"{project_path}#{iid} {item['title']}",
        item["web_url"],
        {"project": project_path, "iid": iid},
    )


def map_job(
    raw: dict[str, Any], *, trace_excerpt: str | None, trace_truncated: bool
) -> dict[str, Any]:
    return {
        "id": str(raw["id"]),
        "name": raw.get("name", ""),
        "stage": raw.get("stage"),
        "status": raw.get("status", ""),
        "duration_s": raw.get("duration"),
        "failure_reason": raw.get("failure_reason"),
        "web_url": raw.get("web_url"),
        "trace_excerpt": trace_excerpt,
        "trace_truncated": trace_truncated,
    }


def map_pipeline(
    raw: dict[str, Any],
    *,
    project_path: str,
    jobs: list[dict[str, Any]] | None,
    duration_s: float | None,
    citation_ref: int,
) -> tuple[dict[str, Any], Citation]:
    pipeline_id = str(raw["id"])
    item = {
        "id": pipeline_id,
        "project_path": project_path,
        "ref": str(raw.get("ref", "")),
        "sha": raw.get("sha"),
        "status": raw.get("status", ""),
        "source": raw.get("source"),
        "created_at": raw.get("created_at"),
        "updated_at": raw.get("updated_at"),
        "duration_s": duration_s,
        "jobs": jobs,
        "web_url": raw["web_url"],
        "citation_ref": citation_ref,
    }
    return item, _cite(
        f"Pipeline #{pipeline_id} ({project_path}@{item['ref']}) {item['status']}",
        item["web_url"],
        {"project": project_path, "pipeline_id": pipeline_id},
    )
