"""Confluence connector (T-070): incremental crawl by `lastModified`, paginated, through
`mcp_confluence.client` (read-only allowlist + `Retry-After` clamped to the budget live in
`mcp_common.http`, shared with the MCP server).

Scope: only the spaces the operator declared team-wide (`MCP_INGEST_CONFLUENCE_TEAM_SPACES`);
`visibility` follows spike S5 (`identity.confluence_visibility`, default-deny). Restrictions are
read through `expand=restrictions.read...` of `GET /content/{id}`, which is already allowlisted —
no new endpoint is needed. The CQL/expand shapes come from Atlassian's public API docs and are
NOT verified against a live tenant (spike S1: unreachable from the squad container); the
`@live` test in mcp_confluence covers the real shape.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from datetime import timedelta
from typing import Any

from mcp_common.errors import ToolError
from mcp_confluence.client import OP_CONTENT, OP_SEARCH, ConfluenceClient
from mcp_confluence.settings import Settings as ConfluenceSettings

from mcp_ingest.connectors._common import parse_ts, source_settings_status
from mcp_ingest.connectors.base import (
    AsyncBridge,
    ConnectorStatus,
    CrawlMode,
    Cursor,
    SourceDocument,
)
from mcp_ingest.identity import confluence_source_id, confluence_visibility
from mcp_ingest.settings import Settings, split_csv

__all__ = ["ConfluenceConnector", "build", "status"]

PAGE_SIZE = 25  # bodies are large; keep responses small
_RESTRICTIONS = "restrictions.read.restrictions.user,restrictions.read.restrictions.group"
_EXPAND = f"body.storage,space,version,ancestors,{_RESTRICTIONS}"
# CQL `lastModified` has minute precision and is interpreted in the *account's* timezone, which
# we cannot read without another endpoint. Query a day earlier and filter exactly client-side:
# over-fetching is absorbed by the content hash, under-fetching would lose pages.
_CQL_SKEW = timedelta(days=1)  # THRESHOLD TBD (verify CQL timezone semantics on a live tenant)


def status(settings: Settings) -> ConnectorStatus:
    extra = (
        [] if split_csv(settings.confluence_team_spaces) else ["MCP_INGEST_CONFLUENCE_TEAM_SPACES"]
    )
    result, _ = source_settings_status(ConfluenceSettings, "confluence", extra_missing=extra)
    return result


def build(settings: Settings) -> ConfluenceConnector:
    from mcp_common.config import load_settings

    return ConfluenceConnector(
        ConfluenceClient(load_settings(ConfluenceSettings, source="confluence")),
        team_spaces=split_csv(settings.confluence_team_spaces),
    )


def _restricted(node: Any) -> bool | None:
    """True/False from an `expand=restrictions.read.restrictions.*` object; None if unknown."""
    try:
        users = node["restrictions"]["read"]["restrictions"]["user"]["results"]
        groups = node["restrictions"]["read"]["restrictions"]["group"]["results"]
    except (KeyError, TypeError):
        return None
    if not isinstance(users, list) or not isinstance(groups, list):
        return None
    return bool(users or groups)


class ConfluenceConnector:
    source_type = "confluence"
    name = "ConfluenceConnector"
    operations_used: tuple[str, ...] = (OP_SEARCH, OP_CONTENT)

    def __init__(
        self,
        client: ConfluenceClient,
        *,
        team_spaces: list[str],
        bridge: AsyncBridge | None = None,
    ) -> None:
        self._client = client
        self._team_spaces = team_spaces
        self._bridge = bridge or AsyncBridge()
        self._ancestor_cache: dict[str, bool | None] = {}

    def status(self) -> ConnectorStatus:
        return ConnectorStatus(enabled=True, configured=True)

    def close(self) -> None:
        self._bridge.close(self._client.aclose())

    # -- crawl ---------------------------------------------------------------------------------

    def _cql(self, cursor: Cursor | None, mode: CrawlMode) -> str:
        spaces = ", ".join('"' + space.replace('"', "") + '"' for space in self._team_spaces)
        cql = f"type = page AND space in ({spaces})"
        if mode == "incremental" and cursor is not None and cursor.watermark is not None:
            since = (cursor.watermark - _CQL_SKEW).strftime("%Y-%m-%d %H:%M")
            cql += f' AND lastModified >= "{since}"'
        return cql + " ORDER BY lastModified ASC"

    def iter_documents(
        self, cursor: Cursor | None, mode: CrawlMode, *, limit: int | None = None
    ) -> Iterator[SourceDocument]:
        cql = self._cql(cursor, mode)
        floor = cursor.watermark if (cursor and mode == "incremental") else None
        start = 0
        yielded = 0
        while True:
            payload = self._bridge.run(
                self._client.search(cql, limit=PAGE_SIZE, start=start, expand=_EXPAND)
            )
            results = payload.get("results") or []
            for raw in results:
                updated = parse_ts((raw.get("version") or {}).get("when"))
                if floor is not None and updated is not None and updated < floor:
                    continue  # exact inclusive boundary (>=); the CQL above is deliberately wider
                yield self._to_document(raw)
                yielded += 1
                if limit is not None and yielded >= limit:
                    return
            if len(results) < PAGE_SIZE or not (payload.get("_links") or {}).get("next"):
                return
            start += len(results)

    def fetch_documents(self, source_ids: Iterable[str]) -> Iterator[SourceDocument]:
        for source_id in source_ids:
            try:
                raw = self._bridge.run(self._client.get_content(source_id, expand=_EXPAND))
            except ToolError as exc:
                if exc.details.get("upstream_status") == 404:
                    continue  # gone at the source: the next full reconcile tombstones it
                raise
            yield self._to_document(raw)

    # -- mapping -------------------------------------------------------------------------------

    def _ancestors_restricted(self, raw: dict[str, Any]) -> bool | None:
        """False only if every ancestor was *read* and has no read restriction."""
        verdict: bool | None = False
        for ancestor in raw.get("ancestors") or []:
            ancestor_id = str(ancestor.get("id", ""))
            if not ancestor_id:
                return None
            if ancestor_id not in self._ancestor_cache:
                try:
                    node = self._bridge.run(
                        self._client.get_content(ancestor_id, expand=_RESTRICTIONS)
                    )
                    self._ancestor_cache[ancestor_id] = _restricted(node)
                except ToolError:
                    self._ancestor_cache[ancestor_id] = None  # cannot prove => default-deny
            value = self._ancestor_cache[ancestor_id]
            if value is None:
                return None
            if value:
                verdict = True
        return verdict

    def _to_document(self, raw: dict[str, Any]) -> SourceDocument:
        page_id = confluence_source_id(raw["id"])
        space = raw.get("space") or {}
        version = raw.get("version") or {}
        links = raw.get("_links") or {}
        base = str(links.get("base") or self._client.base_url).rstrip("/")
        webui = links.get("webui")
        uri = f"{base}{webui}" if webui else f"{base}/pages/viewpage.action?pageId={page_id}"
        visibility = confluence_visibility(
            space_key=str(space.get("key", "")),
            space_type=space.get("type"),
            team_spaces=self._team_spaces,
            page_read_restricted=_restricted(raw),
            ancestors_read_restricted=self._ancestors_restricted(raw),
        )
        return SourceDocument(
            source_type="confluence",
            source_id=page_id,
            source_uri=uri,
            raw_content=str(((raw.get("body") or {}).get("storage") or {}).get("value") or ""),
            source_format="html",
            source_updated_at=parse_ts(version.get("when")),
            title=raw.get("title"),
            container=str(space["key"]) if space.get("key") else None,
            author=(version.get("by") or {}).get("displayName"),
            visibility=visibility,
            metadata={"version": version.get("number")},
        )
