"""OpenSearch connector (T-072) — **off by default** (ADR-0012 A5).

Enabled only by an allowlist of index aliases in `MCP_INGEST_OPENSEARCH_INDICES` (default empty =>
`mcp-ingest sources` reports it disabled and `run --source opensearch` crawls nothing). Goes
through `mcp_opensearch.client` (read-only transport + body guard). Paginates with
`search_after`; **never** `scroll` or point-in-time (both create server-side state and are refused
by the client, ADR-0008 A2; the checkpoint is a timestamp watermark, not a PIT, ADR-0012 A4).

`source_id` is `<alias>:<_id>` (identity.opensearch_source_id): stable across ILM rollover only if
the producer re-uses `_id`; index shapes that cannot guarantee this should not be allowlisted.
Hits whose backing index resolves to an alias outside the allowlist are ignored (a pattern such as
`postmortems-*` also matches the unrelated `postmortems-v2`).
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any

from mcp_opensearch.client import OP_SEARCH, OpenSearchClient
from mcp_opensearch.settings import Settings as OpenSearchSettings

from mcp_ingest.connectors._common import parse_ts, source_settings_status
from mcp_ingest.connectors.base import (
    AsyncBridge,
    ConnectorStatus,
    CrawlMode,
    Cursor,
    SourceDocument,
)
from mcp_ingest.identity import opensearch_alias, opensearch_source_id, opensearch_visibility
from mcp_ingest.settings import Settings, split_csv

__all__ = ["OpenSearchConnector", "build", "status"]

PAGE_SIZE = 500


def status(settings: Settings) -> ConnectorStatus:
    if not split_csv(settings.opensearch_indices):
        return ConnectorStatus(
            enabled=False, configured=False, missing_env=["MCP_INGEST_OPENSEARCH_INDICES"]
        )
    result, _ = source_settings_status(OpenSearchSettings, "opensearch", extra_missing=[])
    return result


def build(settings: Settings) -> OpenSearchConnector:
    from mcp_common.config import load_settings

    os_settings = load_settings(OpenSearchSettings, source="opensearch")
    return OpenSearchConnector(
        OpenSearchClient(os_settings, enforce_egress=True),
        indices=split_csv(settings.opensearch_indices),
        text_field=settings.opensearch_text_field,
        title_field=settings.opensearch_title_field,
        timestamp_field=settings.opensearch_timestamp_field,
        base_url=os_settings.host_list[0],
    )


class OpenSearchConnector:
    source_type = "opensearch"
    name = "OpenSearchConnector"
    operations_used: tuple[str, ...] = (OP_SEARCH,)

    def __init__(
        self,
        client: OpenSearchClient,
        *,
        indices: list[str],
        text_field: str = "content",
        title_field: str = "title",
        timestamp_field: str = "@timestamp",
        base_url: str = "",
        bridge: AsyncBridge | None = None,
    ) -> None:
        self._client = client
        self._indices = indices
        self._allow = {opensearch_alias(i).lower() for i in indices}
        self._text = text_field
        self._title = title_field
        self._ts = timestamp_field
        self._base = base_url.rstrip("/")
        self._bridge = bridge or AsyncBridge()

    def status(self) -> ConnectorStatus:
        return ConnectorStatus(enabled=bool(self._indices), configured=bool(self._indices))

    def close(self) -> None:
        self._bridge.close(self._client.aclose())

    def _target(self) -> str:
        return ",".join(f"{alias},{alias}-*" for alias in self._indices)

    def _hits(
        self, body: dict[str, Any], target: str, limit: int | None
    ) -> Iterator[dict[str, Any]]:
        count = 0
        search_after: list[Any] | None = None
        while True:
            page_body = dict(body, size=PAGE_SIZE, track_total_hits=False)
            if search_after:
                page_body["search_after"] = search_after
            payload = self._bridge.run(self._client.search(target, page_body))
            hits = (payload.get("hits") or {}).get("hits") or []
            for hit in hits:
                yield hit
                count += 1
                if limit is not None and count >= limit:
                    return
            if len(hits) < PAGE_SIZE:
                return
            search_after = hits[-1].get("sort")
            if not search_after:
                return

    def iter_documents(
        self, cursor: Cursor | None, mode: CrawlMode, *, limit: int | None = None
    ) -> Iterator[SourceDocument]:
        if not self._indices:
            return  # disabled: nothing is crawled
        query: dict[str, Any] = {"match_all": {}}
        if mode == "incremental" and cursor is not None and cursor.watermark is not None:
            query = {
                "range": {self._ts: {"gte": cursor.watermark.isoformat().replace("+00:00", "Z")}}
            }  # inclusive
        body = {"query": query, "sort": [{self._ts: "asc"}, {"_id": "asc"}]}
        yielded = 0
        for hit in self._hits(body, self._target(), None):
            document = self._to_document(hit)
            if document is None:
                continue
            yield document
            yielded += 1
            if limit is not None and yielded >= limit:
                return

    def fetch_documents(self, source_ids: Iterable[str]) -> Iterator[SourceDocument]:
        for source_id in source_ids:
            alias, _, doc_id = source_id.partition(":")
            if alias.lower() not in self._allow or not doc_id:
                continue
            body = {"query": {"ids": {"values": [doc_id]}}}
            for hit in self._hits(body, f"{alias},{alias}-*", 1):
                document = self._to_document(hit)
                if document is not None:
                    yield document

    def _to_document(self, hit: dict[str, Any]) -> SourceDocument | None:
        index = str(hit.get("_index", ""))
        if opensearch_alias(index).lower() not in self._allow:
            return None
        source = hit.get("_source") or {}
        text = source.get(self._text)
        if not isinstance(text, str) or not text.strip():
            return None
        doc_id = str(hit["_id"])
        alias = opensearch_alias(index)
        return SourceDocument(
            source_type="opensearch",
            source_id=opensearch_source_id(index, doc_id),
            source_uri=f"{self._base}/{index}/_doc/{doc_id}",
            raw_content=text,
            source_updated_at=parse_ts(source.get(self._ts)),
            title=str(source[self._title]) if source.get(self._title) else None,
            container=alias,
            visibility=opensearch_visibility(index, allowlist=self._indices),
            metadata={"index": index},
        )
