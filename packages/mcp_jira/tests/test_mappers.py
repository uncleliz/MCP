"""T-092: Jira mappers — payload -> contract item + Citation, including edge cases
(missing optional fields, sprint without a board => null URL, unknown sprint state)."""

from __future__ import annotations

from mcp_common.envelope import SourceType
from mcp_jira.mappers import map_issue, map_project, map_sprint

BASE = "https://jira.acme.example"


def test_map_issue_minimal_fields_become_null() -> None:
    item, citation = map_issue(
        {"key": "PAY-1", "fields": {"summary": "", "status": {"name": "Open"}}},
        base_url=BASE,
        citation_ref=0,
    )
    assert item["key"] == "PAY-1" and item["status"] == "Open"
    assert item["issue_type"] is None and item["assignee"] is None and item["priority"] is None
    assert item["url"] == f"{BASE}/browse/PAY-1"
    assert citation.source_type == SourceType.JIRA and citation.label == "PAY-1"


def test_map_project_defaults_name_to_key() -> None:
    item, citation = map_project({"key": "PAY"}, base_url=BASE, citation_ref=2)
    assert item["name"] == "PAY" and item["citation_ref"] == 2
    assert citation.uri == f"{BASE}/browse/PAY"


def test_map_sprint_without_board_has_null_url() -> None:
    item, citation = map_sprint(
        {"id": 9, "name": "Sprint 9", "state": "future"}, base_url=BASE, citation_ref=0
    )
    assert item["board_id"] is None and item["url"] is None
    assert item["state"] == "future"
    assert citation.uri is None and citation.locator == {"sprint_id": 9}


def test_map_sprint_unknown_state_falls_back_to_closed() -> None:
    item, _ = map_sprint(
        {"id": 1, "name": "s", "state": "weird", "originBoardId": 3}, base_url=BASE, citation_ref=0
    )
    assert item["state"] == "closed"
    assert item["url"].endswith("rapidView=3&sprint=1")
