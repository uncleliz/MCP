"""Jira tool layer: input bounds, cursor round-trip, normalisation, empty/not_found branches.

This is where the *tool* bounds live (`limit <= 100`, JQL length cap, key pattern); `client.py`
stays bound-free so `mcp-ingest`'s Jira connector can crawl (ADR-0007 A3 / ADR-0012 A4) — the
connector must never import this module.

JQL is **bounded** (ADR-0019 Alternative 3): the caller supplies a JQL string that is read-only
by construction (the transport is GET-only and no write endpoint is reachable), length-capped,
and the result count is capped at `limit <= 100`. The opaque `CursorField` (ADR-0004) wraps the
flavor-specific pagination token/offset returned by `client.py`.
"""

from __future__ import annotations

import re
import time
from typing import Any

from mcp_common.config import CommonSettings
from mcp_common.envelope import Citation, SourceType
from mcp_common.errors import ErrorCode, ToolError
from mcp_common.tooling import (
    ToolOutcome,
    build_result,
    decode_cursor,
    encode_cursor,
    invalid_input,
    not_found_result,
)

from mcp_jira.client import SOURCE, JiraClient, JiraPage
from mcp_jira.mappers import map_issue, map_project, map_sprint
from mcp_jira.settings import Settings

__all__ = ["JiraReadApi"]

_ISSUE_KEY = re.compile(r"^[A-Z][A-Z0-9]+-[0-9]+$")
_JQL_MAX = 2048
_SPRINT_STATES = ("active", "future", "closed", "all")


def _check_limit(limit: int) -> None:
    if not 1 <= limit <= 100:
        raise invalid_input("limit", "phải nằm trong khoảng 1..100", SOURCE)


def _is_not_found(exc: ToolError) -> bool:
    return exc.code == ErrorCode.UPSTREAM_ERROR and exc.details.get("upstream_status") == 404


class JiraReadApi:
    def __init__(self, client: JiraClient, settings: Settings, common: CommonSettings) -> None:
        self._client = client
        self._settings = settings
        self._common = common

    # -- jira_search_issues -----------------------------------------------------------

    async def search_issues(
        self, *, jql: str, limit: int = 20, cursor: str | None = None
    ) -> ToolOutcome:
        started = time.monotonic()
        if not 1 <= len(jql) <= _JQL_MAX:
            raise invalid_input("jql", f"độ dài phải từ 1 đến {_JQL_MAX} ký tự", SOURCE)
        _check_limit(limit)
        cursor_state = decode_cursor(cursor, source=SOURCE)
        page = await self._client.search_issues(jql, max_results=limit, cursor_state=cursor_state)
        items, citations = self._map(page, map_issue)
        next_cursor = encode_cursor(page.next_cursor_state) if page.next_cursor_state else None
        result = build_result(
            SourceType.JIRA,
            items,
            citations,
            started=started,
            query_echo={"jql": jql, "limit": limit},
            next_cursor=next_cursor,
        )
        return ToolOutcome(result, query_description=f'issue Jira cho JQL "{jql}"')

    # -- jira_get_issue ---------------------------------------------------------------

    async def get_issue(self, *, key: str) -> ToolOutcome:
        started = time.monotonic()
        if not _ISSUE_KEY.match(key):
            raise invalid_input("key", "phải khớp ^[A-Z][A-Z0-9]+-[0-9]+$ (ví dụ PAY-1234)", SOURCE)
        echo = {"key": key}
        try:
            raw = await self._client.get_issue(key)
        except ToolError as exc:
            if _is_not_found(exc):
                return ToolOutcome(
                    not_found_result(SourceType.JIRA, started=started, query_echo=echo),
                    identifier=f"Issue {key}",
                )
            raise
        item, citation = map_issue(raw, base_url=self._settings.base_url, citation_ref=0)
        result = build_result(
            SourceType.JIRA, [item], [citation], started=started, query_echo=echo
        )
        return ToolOutcome(result, identifier=f"Issue {key}")

    # -- jira_list_projects -----------------------------------------------------------

    async def list_projects(self, *, limit: int = 20, cursor: str | None = None) -> ToolOutcome:
        started = time.monotonic()
        _check_limit(limit)
        cursor_state = decode_cursor(cursor, source=SOURCE)
        page = await self._client.list_projects(max_results=limit, cursor_state=cursor_state)
        items, citations = self._map(page, map_project)
        next_cursor = encode_cursor(page.next_cursor_state) if page.next_cursor_state else None
        result = build_result(
            SourceType.JIRA,
            items,
            citations,
            started=started,
            query_echo={"limit": limit},
            next_cursor=next_cursor,
        )
        return ToolOutcome(result, query_description="project Jira")

    # -- jira_get_sprint --------------------------------------------------------------

    async def get_sprint(self, *, sprint_id: int) -> ToolOutcome:
        started = time.monotonic()
        if sprint_id < 1:
            raise invalid_input("sprint_id", "phải là số nguyên dương", SOURCE)
        echo = {"sprint_id": sprint_id}
        try:
            raw = await self._client.get_sprint(sprint_id)
        except ToolError as exc:
            if _is_not_found(exc):
                return ToolOutcome(
                    not_found_result(SourceType.JIRA, started=started, query_echo=echo),
                    identifier=f"Sprint {sprint_id}",
                )
            raise
        item, citation = map_sprint(raw, base_url=self._settings.base_url, citation_ref=0)
        result = build_result(
            SourceType.JIRA, [item], [citation], started=started, query_echo=echo
        )
        return ToolOutcome(result, identifier=f"Sprint {sprint_id}")

    # -- jira_list_board_sprints ------------------------------------------------------

    async def list_board_sprints(
        self,
        *,
        board_id: int,
        state: str = "active",
        limit: int = 20,
        cursor: str | None = None,
    ) -> ToolOutcome:
        started = time.monotonic()
        if board_id < 1:
            raise invalid_input("board_id", "phải là số nguyên dương", SOURCE)
        if state not in _SPRINT_STATES:
            raise invalid_input("state", f"chỉ nhận một trong {_SPRINT_STATES}", SOURCE)
        _check_limit(limit)
        echo = {"board_id": board_id, "state": state, "limit": limit}
        cursor_state = decode_cursor(cursor, source=SOURCE)
        try:
            page = await self._client.list_board_sprints(
                board_id, state=state, max_results=limit, cursor_state=cursor_state
            )
        except ToolError as exc:
            if _is_not_found(exc):
                return ToolOutcome(
                    not_found_result(SourceType.JIRA, started=started, query_echo=echo),
                    identifier=f"Board {board_id}",
                )
            raise
        items, citations = self._map(page, map_sprint)
        next_cursor = encode_cursor(page.next_cursor_state) if page.next_cursor_state else None
        result = build_result(
            SourceType.JIRA,
            items,
            citations,
            started=started,
            query_echo=echo,
            next_cursor=next_cursor,
        )
        return ToolOutcome(result, query_description=f"sprint của board {board_id}")

    # -- shared ------------------------------------------------------------------------

    def _map(
        self, page: JiraPage, mapper: Any
    ) -> tuple[list[dict[str, Any]], list[Citation]]:
        items: list[dict[str, Any]] = []
        citations: list[Citation] = []
        for index, raw in enumerate(page.values):
            item, citation = mapper(raw, base_url=self._settings.base_url, citation_ref=index)
            items.append(item)
            citations.append(citation)
        return items, citations
