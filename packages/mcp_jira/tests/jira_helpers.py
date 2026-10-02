"""URL builders mirroring `client.py`'s flavor split, so tests mock the right endpoint per
flavor without duplicating the `/rest/api/{2|3}` logic."""

from __future__ import annotations

__all__ = [
    "api_version",
    "search_url",
    "issue_url",
    "project_search_url",
    "project_url",
    "sprint_url",
    "board_sprints_url",
    "myself_url",
]


def api_version(flavor: str) -> str:
    return "3" if flavor == "cloud" else "2"


def search_url(base: str, flavor: str) -> str:
    return f"{base}/rest/api/{api_version(flavor)}/search"


def issue_url(base: str, flavor: str, key: str) -> str:
    return f"{base}/rest/api/{api_version(flavor)}/issue/{key}"


def project_search_url(base: str, flavor: str) -> str:
    return f"{base}/rest/api/{api_version(flavor)}/project/search"


def project_url(base: str, flavor: str) -> str:
    return f"{base}/rest/api/{api_version(flavor)}/project"


def sprint_url(base: str, sprint_id: int) -> str:
    return f"{base}/rest/agile/1.0/sprint/{sprint_id}"


def board_sprints_url(base: str, board_id: int) -> str:
    return f"{base}/rest/agile/1.0/board/{board_id}/sprint"


def myself_url(base: str, flavor: str) -> str:
    return f"{base}/rest/api/{api_version(flavor)}/myself"
