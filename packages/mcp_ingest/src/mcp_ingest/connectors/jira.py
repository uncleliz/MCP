"""Jira connector (T-093, ADR-0019): issues crawled through `mcp_jira.client` (read-only
allowlist, timeout budget, flavor split) — **never** `mcp_jira.read_api` (ADR-0012 A4;
enforced by `tests/test_connector_isolation.py`).

* Scope: the project keys the operator declared (`MCP_INGEST_JIRA_PROJECTS`); `visibility`
  follows `identity.jira_visibility` (default-deny, ADR-0016 A2): a declared team project with no
  per-issue security level is `team`, everything else `restricted` and rejected by the `redact`
  stage.
* Incremental: JQL `updated >= <watermark>` with an inclusive boundary (`>=`, ADR-0012 A2). Jira
  `updated` has minute precision in the *account* timezone (which we cannot read without another
  endpoint), so the JQL is widened by a one-minute skew and the exact `>=` boundary is applied
  client-side; over-fetching is absorbed by the content hash.
* Deletes/moves: Jira has no reliable delete feed (ADR-0019), so disappearance is detected by the
  pipeline's full-reconcile tombstone (safety-valve 0.8, `pipeline/reconcile.py`) — the connector
  only has to yield every live issue in `full` mode.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from datetime import timedelta
from typing import Any

from mcp_common.errors import ToolError
from mcp_jira.client import JiraClient
from mcp_jira.mappers import issue_url
from mcp_jira.settings import Settings as JiraSettings

from mcp_ingest.connectors._common import parse_ts, source_settings_status
from mcp_ingest.connectors.base import (
    AsyncBridge,
    ConnectorStatus,
    CrawlMode,
    Cursor,
    SourceDocument,
)
from mcp_ingest.identity import jira_source_id, jira_visibility
from mcp_ingest.settings import Settings, split_csv

__all__ = ["JiraConnector", "build", "status"]

PAGE_SIZE = 100
_MAX_PAGES = 1000  # hard stop against a misbehaving cursor
# `updated` is minute-precision in the account timezone; widen the JQL and filter exactly below.
_JQL_SKEW = timedelta(minutes=1)
_FORMAT = "%Y-%m-%d %H:%M"


def status(settings: Settings) -> ConnectorStatus:
    extra = [] if split_csv(settings.jira_projects) else ["MCP_INGEST_JIRA_PROJECTS"]
    result, _ = source_settings_status(JiraSettings, "jira", extra_missing=extra)
    return result


def build(settings: Settings) -> JiraConnector:
    from mcp_common.config import load_settings

    return JiraConnector(
        JiraClient(load_settings(JiraSettings, source="jira"), enforce_egress=True),
        projects=split_csv(settings.jira_projects),
        team_projects=split_csv(settings.jira_team_projects),
        page_size=settings.jira_page_size,
    )


def _jql_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


class JiraConnector:
    source_type = "jira"
    name = "JiraConnector"
    # Client operations this connector calls (readonly surface test). Version-independent keys.
    operations_used: tuple[str, ...] = ("GET /rest/api/search",)

    def __init__(
        self,
        client: JiraClient,
        *,
        projects: list[str],
        team_projects: list[str],
        page_size: int = PAGE_SIZE,
        bridge: AsyncBridge | None = None,
    ) -> None:
        self._client = client
        self._projects = projects
        self._team_projects = team_projects
        self._page_size = min(max(page_size, 1), 100)
        self._bridge = bridge or AsyncBridge()

    def status(self) -> ConnectorStatus:
        return ConnectorStatus(enabled=True, configured=True)

    def close(self) -> None:
        self._bridge.close(self._client.aclose())

    # -- crawl ---------------------------------------------------------------------------------

    def _jql(self, cursor: Cursor | None, mode: CrawlMode) -> str:
        keys = ", ".join(_jql_quote(key) for key in self._projects)
        jql = f"project in ({keys})"
        if mode == "incremental" and cursor is not None and cursor.watermark is not None:
            since = (cursor.watermark - _JQL_SKEW).strftime(_FORMAT)
            jql += f' AND updated >= "{since}"'
        return jql + " ORDER BY updated ASC"

    def iter_documents(
        self, cursor: Cursor | None, mode: CrawlMode, *, limit: int | None = None
    ) -> Iterator[SourceDocument]:
        jql = self._jql(cursor, mode)
        floor = cursor.watermark if (cursor and mode == "incremental") else None
        cursor_state: dict[str, Any] | None = None
        yielded = 0
        for _ in range(_MAX_PAGES):
            page = self._bridge.run(
                self._client.search_issues(
                    jql, max_results=self._page_size, cursor_state=cursor_state
                )
            )
            for raw in page.values:
                updated = parse_ts((raw.get("fields") or {}).get("updated"))
                if floor is not None and updated is not None and updated < floor:
                    continue  # exact inclusive boundary (>=); the JQL above is deliberately wider
                yield self._to_document(raw)
                yielded += 1
                if limit is not None and yielded >= limit:
                    return
            if page.next_cursor_state is None:
                return
            cursor_state = page.next_cursor_state

    def fetch_documents(self, source_ids: Iterable[str]) -> Iterator[SourceDocument]:
        for source_id in source_ids:
            try:
                raw = self._bridge.run(self._client.get_issue(source_id))
            except ToolError as exc:
                if exc.details.get("upstream_status") == 404:
                    continue  # gone at the source: the next full reconcile tombstones it
                raise
            yield self._to_document(raw)

    # -- mapping -------------------------------------------------------------------------------

    def _to_document(self, raw: dict[str, Any]) -> SourceDocument:
        key = jira_source_id(str(raw.get("key") or ""))
        fields = raw.get("fields") or {}
        project_key = str((fields.get("project") or {}).get("key") or "")
        summary = str(fields.get("summary") or "")
        description = fields.get("description")
        # Server/DC returns a plain-string description; Cloud ADF is a dict we flatten to text.
        body = self._description_text(description)
        content = f"# {summary}\n\n{body}".strip() if summary or body else summary
        security = (fields.get("security") or {}).get("name") if fields.get("security") else None
        visibility = jira_visibility(
            project_key=project_key,
            team_projects=self._team_projects,
            issue_security_level=security,
            # Declaring a project team-wide is the operator's attestation it is not restricted
            # (ADR-0016 A2); a per-issue security level still demotes it above.
            project_restricted=False if self._is_declared(project_key) else None,
        )
        return SourceDocument(
            source_type="jira",
            source_id=key,
            source_uri=issue_url(self._client.base_url, key),
            raw_content=content,
            source_format="markdown",
            source_updated_at=parse_ts(fields.get("updated")),
            title=summary or key,
            container=project_key or None,
            author=(fields.get("assignee") or {}).get("displayName"),
            visibility=visibility,
            metadata={
                "status": (fields.get("status") or {}).get("name"),
                "issue_type": (fields.get("issuetype") or {}).get("name"),
            },
        )

    def _is_declared(self, project_key: str) -> bool:
        return project_key.lower() in {k.lower() for k in self._team_projects}

    @staticmethod
    def _description_text(description: Any) -> str:
        """Jira Cloud descriptions are Atlassian Document Format (ADF, nested dict); Server/DC
        returns a wiki/plain string. Extract the text leaves of ADF; pass strings through."""
        if isinstance(description, str):
            return description
        if not isinstance(description, dict):
            return ""
        parts: list[str] = []

        def walk(node: Any) -> None:
            if isinstance(node, dict):
                if node.get("type") == "text" and isinstance(node.get("text"), str):
                    parts.append(node["text"])
                for child in node.get("content") or []:
                    walk(child)
            elif isinstance(node, list):
                for child in node:
                    walk(child)

        walk(description)
        return " ".join(parts).strip()
