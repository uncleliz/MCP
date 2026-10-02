"""Jira payloads -> contract item schemas (`JiraIssue`, `JiraProject`, `JiraSprint`) + `Citation`
(FR-017/AC-001..002, FR-015, ADR-0004).

Pure functions, no I/O. Both API flavors (Cloud `/rest/api/3`, Server/DC `/rest/api/2`) return
compatible shapes for the fields the contract exposes; where they differ (e.g. `assignee` is
`displayName` under `fields.assignee`), the readers below cope with a missing branch.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from mcp_common.envelope import Citation, SourceType

__all__ = ["issue_url", "map_issue", "map_project", "map_sprint", "project_url", "sprint_url"]

_SPRINT_STATES = {"active", "future", "closed"}


def _now() -> datetime:
    return datetime.now(UTC)


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None


def issue_url(base_url: str, key: str) -> str:
    return f"{base_url.rstrip('/')}/browse/{key}"


def project_url(base_url: str, key: str) -> str:
    return f"{base_url.rstrip('/')}/browse/{key}"


def sprint_url(base_url: str, board_id: int | None, sprint_id: int) -> str | None:
    if board_id is None:
        return None
    return (
        f"{base_url.rstrip('/')}/secure/RapidBoard.jspa?rapidView={board_id}&sprint={sprint_id}"
    )


def map_issue(
    raw: dict[str, Any], *, base_url: str, citation_ref: int
) -> tuple[dict[str, Any], Citation]:
    key = str(raw.get("key") or "")
    fields = raw.get("fields") or {}
    status = ((fields.get("status") or {}).get("name")) or ""
    issue_type = (fields.get("issuetype") or {}).get("name")
    assignee = (fields.get("assignee") or {}).get("displayName")
    priority = (fields.get("priority") or {}).get("name")
    project = (fields.get("project") or {}).get("key")
    summary = str(fields.get("summary") or "")
    url = issue_url(base_url, key)
    item = {
        "key": key,
        "summary": summary,
        "status": str(status),
        "issue_type": _str_or_none(issue_type),
        "assignee": _str_or_none(assignee),
        "priority": _str_or_none(priority),
        "updated": _str_or_none(fields.get("updated")),
        "project": _str_or_none(project),
        "url": url,
        "citation_ref": citation_ref,
    }
    label = f"{key} — {summary}" if summary else key
    citation = Citation(
        source_type=SourceType.JIRA,
        label=label[:512] or key,
        uri=url,
        locator={"key": key},
        retrieved_at=_now(),
    )
    return item, citation


def map_project(
    raw: dict[str, Any], *, base_url: str, citation_ref: int
) -> tuple[dict[str, Any], Citation]:
    key = str(raw.get("key") or "")
    name = str(raw.get("name") or key)
    url = project_url(base_url, key)
    item = {
        "key": key,
        "name": name,
        "project_type": _str_or_none(raw.get("projectTypeKey")),
        "url": url,
        "citation_ref": citation_ref,
    }
    citation = Citation(
        source_type=SourceType.JIRA,
        label=f"{name} ({key})"[:512],
        uri=url,
        locator={"key": key},
        retrieved_at=_now(),
    )
    return item, citation


def map_sprint(
    raw: dict[str, Any], *, base_url: str, citation_ref: int
) -> tuple[dict[str, Any], Citation]:
    sprint_id = int(raw.get("id") or 0)
    name = str(raw.get("name") or f"Sprint {sprint_id}")
    raw_state = str(raw.get("state") or "").lower()
    state = raw_state if raw_state in _SPRINT_STATES else "closed"
    board_id = raw.get("originBoardId")
    board_id = int(board_id) if isinstance(board_id, int) else None
    url = sprint_url(base_url, board_id, sprint_id)
    item = {
        "sprint_id": sprint_id,
        "name": name,
        "state": state,
        "board_id": board_id,
        "start_date": _str_or_none(raw.get("startDate")),
        "end_date": _str_or_none(raw.get("endDate")),
        "goal": _str_or_none(raw.get("goal")),
        "url": url,
        "citation_ref": citation_ref,
    }
    citation = Citation(
        source_type=SourceType.JIRA,
        label=f"{name} ({state})"[:512],
        uri=url,
        locator={"sprint_id": sprint_id},
        retrieved_at=_now(),
    )
    return item, citation
