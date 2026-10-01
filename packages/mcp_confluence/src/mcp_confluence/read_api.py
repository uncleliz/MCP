"""Confluence tool layer: input bounds, CQL building, normalisation, redaction, paging.

This is where the *tool* bounds live (`limit <= 100`, `max_chars`, tz-aware
`updated_after`); `client.py` stays bound-free so `mcp-ingest`'s connector can crawl
(ADR-0007 A3 / ADR-0012 A4) — the connector must never import this module.
"""

from __future__ import annotations

import re
import time
from datetime import UTC, datetime
from typing import Any

from mcp_common.config import CommonSettings
from mcp_common.content import truncate_bytes
from mcp_common.envelope import Citation, SourceType
from mcp_common.errors import ErrorCode, ToolError
from mcp_common.tooling import (
    RedactionCounter,
    ToolOutcome,
    build_result,
    decode_cursor,
    effective_max_bytes,
    encode_cursor,
    invalid_input,
    not_found_result,
    safe_text,
)

from mcp_confluence.client import SOURCE, ConfluenceClient
from mcp_confluence.mappers import (
    map_child,
    map_page,
    map_space,
    storage_to_markdown,
    storage_to_text,
)
from mcp_confluence.settings import Settings

__all__ = ["ConfluenceReadApi", "build_cql"]

_PAGE_ID = re.compile(r"^[0-9A-Za-z._-]{1,64}$")
_EXCERPT_CHARS = 300
_SEARCH_EXPAND = "space,version,metadata.labels"
_PAGE_EXPAND = "body.storage,space,version,metadata.labels"


def _cql_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def build_cql(
    query: str,
    *,
    space_key: str | None = None,
    updated_after: datetime | None = None,
    labels: list[str] | None = None,
) -> str:
    clauses = ["type = page", f"text ~ {_cql_quote(query)}"]
    if space_key:
        clauses.append(f"space = {_cql_quote(space_key)}")
    if updated_after is not None:
        stamp = updated_after.astimezone(UTC).strftime("%Y-%m-%d %H:%M")
        clauses.append(f'lastmodified > "{stamp}"')
    for label in labels or []:
        clauses.append(f"label = {_cql_quote(label)}")
    return " AND ".join(clauses) + " ORDER BY lastmodified DESC"


def _check_limit(limit: int) -> None:
    if not 1 <= limit <= 100:
        raise invalid_input("limit", "phải nằm trong khoảng 1..100", SOURCE)


def _check_page_id(page_id: str) -> None:
    if not _PAGE_ID.match(page_id):
        raise invalid_input("page_id", "chỉ gồm [0-9A-Za-z._-], tối đa 64 ký tự", SOURCE)


def _is_not_found(exc: ToolError) -> bool:
    return exc.code == ErrorCode.UPSTREAM_ERROR and exc.details.get("upstream_status") == 404


class ConfluenceReadApi:
    def __init__(
        self, client: ConfluenceClient, settings: Settings, common: CommonSettings
    ) -> None:
        self._client = client
        self._settings = settings
        self._common = common

    def _links_base(self, payload: dict[str, Any]) -> str | None:
        base = (payload.get("_links") or {}).get("base")
        return str(base) if base else None

    # -- confluence_search_pages ------------------------------------------------------

    async def search_pages(
        self,
        *,
        query: str,
        space_key: str | None = None,
        updated_after: datetime | None = None,
        labels: list[str] | None = None,
        include_excerpt: bool = True,
        limit: int = 20,
        cursor: str | None = None,
    ) -> ToolOutcome:
        started = time.monotonic()
        if not 1 <= len(query) <= 512:
            raise invalid_input("query", "độ dài phải từ 1 đến 512 ký tự", SOURCE)
        _check_limit(limit)
        labels = labels or []
        if len(labels) > 10:
            raise invalid_input("labels", "tối đa 10 label", SOURCE)
        if updated_after is not None and updated_after.tzinfo is None:
            raise invalid_input("updated_after", "thiếu timezone (ví dụ hậu tố Z)", SOURCE)
        start = int(decode_cursor(cursor, source=SOURCE).get("start", 0))

        expand = _SEARCH_EXPAND + (",body.storage" if include_excerpt else "")
        payload = await self._client.search(
            build_cql(query, space_key=space_key, updated_after=updated_after, labels=labels),
            limit=limit,
            start=start,
            expand=expand,
        )
        results = payload.get("results") or []
        links_base = self._links_base(payload)
        counter = RedactionCounter()
        items: list[dict[str, Any]] = []
        citations: list[Citation] = []
        for index, raw in enumerate(results):
            excerpt = None
            if include_excerpt:
                body = (((raw.get("body") or {}).get("storage")) or {}).get("value", "")
                snippet = storage_to_text(body)[:_EXCERPT_CHARS]
                if snippet:
                    excerpt = safe_text(
                        snippet,
                        source=SOURCE,
                        content_id=str(raw["id"]),
                        counter=counter,
                        disabled=self._common.redact_disabled,
                    )
            item, citation = map_page(
                raw,
                base_url=self._settings.base_url,
                links_base=links_base,
                citation_ref=index,
                excerpt=excerpt,
            )
            items.append(item)
            citations.append(citation)

        next_cursor = None
        if (payload.get("_links") or {}).get("next") and results:
            next_cursor = encode_cursor({"start": start + len(results)})
        result = build_result(
            SourceType.CONFLUENCE,
            items,
            citations,
            started=started,
            query_echo={
                "query": query,
                "space_key": space_key,
                "updated_after": updated_after.isoformat() if updated_after else None,
                "labels": labels,
                "include_excerpt": include_excerpt,
                "limit": limit,
            },
            next_cursor=next_cursor,
            redactions=counter.count,
        )
        scope = f" trong space {space_key}" if space_key else ""
        return ToolOutcome(result, query_description=f'tài liệu Confluence cho "{query}"{scope}')

    # -- confluence_get_page ----------------------------------------------------------

    async def get_page(
        self, *, page_id: str, body_format: str = "markdown", max_chars: int = 20000
    ) -> ToolOutcome:
        started = time.monotonic()
        _check_page_id(page_id)
        if not 500 <= max_chars <= 100000:
            raise invalid_input("max_chars", "phải nằm trong khoảng 500..100000", SOURCE)
        if body_format not in ("markdown", "text"):
            raise invalid_input("body_format", "chỉ nhận 'markdown' hoặc 'text'", SOURCE)
        echo = {"page_id": page_id, "body_format": body_format, "max_chars": max_chars}

        try:
            raw = await self._client.get_content(page_id, expand=_PAGE_EXPAND)
        except ToolError as exc:
            if _is_not_found(exc):
                return ToolOutcome(
                    not_found_result(SourceType.CONFLUENCE, started=started, query_echo=echo),
                    identifier=f"Page {page_id}",
                )
            raise

        storage = (((raw.get("body") or {}).get("storage")) or {}).get("value", "")
        converter = storage_to_markdown if body_format == "markdown" else storage_to_text
        text = converter(storage)
        warnings: list[str] = []
        truncated = False
        if len(text) > max_chars:
            text, truncated = text[:max_chars], True
        max_bytes, clamp_warnings = effective_max_bytes(self._common.max_output_bytes, self._common)
        warnings.extend(clamp_warnings)
        text, byte_truncated = truncate_bytes(text, max_bytes)
        truncated = truncated or byte_truncated
        if truncated:
            warnings.append("nội dung trang đã bị cắt; tăng max_chars (tối đa 100000) nếu cần")

        counter = RedactionCounter()
        content = safe_text(
            text,
            source=SOURCE,
            content_id=str(raw["id"]),
            counter=counter,
            disabled=self._common.redact_disabled,
        )
        item, citation = map_page(
            raw,
            base_url=self._settings.base_url,
            links_base=self._links_base(raw),
            citation_ref=0,
            content=content,
        )
        result = build_result(
            SourceType.CONFLUENCE,
            [item],
            [citation],
            started=started,
            query_echo=echo,
            truncated=truncated,
            warnings=warnings,
            redactions=counter.count,
        )
        return ToolOutcome(result, identifier=f"Page {page_id}")

    # -- confluence_list_spaces -------------------------------------------------------

    async def list_spaces(
        self, *, query: str | None = None, limit: int = 20, cursor: str | None = None
    ) -> ToolOutcome:
        started = time.monotonic()
        _check_limit(limit)
        if query is not None and len(query) > 128:
            raise invalid_input("query", "tối đa 128 ký tự", SOURCE)
        start = int(decode_cursor(cursor, source=SOURCE).get("start", 0))
        payload = await self._client.list_spaces(limit=limit, start=start)
        raw_results = payload.get("results") or []
        warnings: list[str] = []
        results = raw_results
        if query:
            needle = query.lower()
            results = [
                r
                for r in raw_results
                if needle in str(r.get("key", "")).lower()
                or needle in str(r.get("name", "")).lower()
            ]
            warnings.append("query được lọc trên từng trang kết quả; dùng cursor để xem trang tiếp")
        links_base = self._links_base(payload)
        items: list[dict[str, Any]] = []
        citations: list[Citation] = []
        for index, raw in enumerate(results):
            item, citation = map_space(
                raw, base_url=self._settings.base_url, links_base=links_base, citation_ref=index
            )
            items.append(item)
            citations.append(citation)
        next_cursor = None
        if (payload.get("_links") or {}).get("next") and raw_results:
            next_cursor = encode_cursor({"start": start + len(raw_results)})
        result = build_result(
            SourceType.CONFLUENCE,
            items,
            citations,
            started=started,
            query_echo={"query": query, "limit": limit},
            next_cursor=next_cursor,
            warnings=warnings if items else [],
        )
        return ToolOutcome(
            result,
            query_description=f'space Confluence khớp "{query}"' if query else "space Confluence",
        )

    # -- confluence_list_page_children ------------------------------------------------

    async def list_page_children(
        self, *, page_id: str, limit: int = 20, cursor: str | None = None
    ) -> ToolOutcome:
        started = time.monotonic()
        _check_page_id(page_id)
        _check_limit(limit)
        start = int(decode_cursor(cursor, source=SOURCE).get("start", 0))
        echo = {"page_id": page_id, "limit": limit}
        try:
            payload = await self._client.list_children(page_id, limit=limit, start=start)
        except ToolError as exc:
            if _is_not_found(exc):
                return ToolOutcome(
                    not_found_result(SourceType.CONFLUENCE, started=started, query_echo=echo),
                    identifier=f"Page {page_id}",
                )
            raise
        results = payload.get("results") or []
        links_base = self._links_base(payload)
        items: list[dict[str, Any]] = []
        citations: list[Citation] = []
        for index, raw in enumerate(results):
            item, citation = map_child(
                raw, base_url=self._settings.base_url, links_base=links_base, citation_ref=index
            )
            items.append(item)
            citations.append(citation)
        next_cursor = None
        if (payload.get("_links") or {}).get("next") and results:
            next_cursor = encode_cursor({"start": start + len(results)})
        result = build_result(
            SourceType.CONFLUENCE,
            items,
            citations,
            started=started,
            query_echo=echo,
            next_cursor=next_cursor,
        )
        return ToolOutcome(result, query_description=f"trang con của page {page_id}")
