"""OpenSearch tool layer: input bounds, query building, redaction, byte budget, paging.

Tool bounds (`limit <= 100`, mandatory time range <= `MCP_MAX_TIME_RANGE_DAYS`, `timeout_s`
below the tool deadline) live here, not in `client.py`, so `mcp-ingest`'s connector can crawl
without them (ADR-0012 A4) — the connector must never import this module.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any

from mcp_common.config import CommonSettings
from mcp_common.envelope import Citation, SourceType
from mcp_common.errors import ErrorCode, ToolError
from mcp_common.runtime import tool_deadline_for
from mcp_common.tooling import (
    CallState,
    ToolOutcome,
    build_result,
    decode_cursor,
    encode_cursor,
    invalid_input,
    not_found_result,
    parse_time_range,
    sanitize_json,
)

from mcp_opensearch import mappers
from mcp_opensearch.client import (
    ALLOWED_TOP_LEVEL_KEYS,
    SOURCE,
    OpenSearchClient,
    assert_body_allowed,
    not_permitted,
)

__all__ = ["OpenSearchReadApi"]

_INDEX_PATTERN = re.compile(r"^[^\s/\\?#]+$")
_INTERVAL_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "1d": 86400}
_MAX_HISTOGRAM_BUCKETS = 1000
_DEEP_PAGING_CAP = 1000
_MAX_DSL_SIZE = 100
_MAX_DSL_FROM = 900
# Keys a *user* DSL body may carry (contract `opensearch_search_dsl`); `timeout` is ours.
_USER_DSL_KEYS = tuple(k for k in ALLOWED_TOP_LEVEL_KEYS if k != "timeout")


def _is_not_found(exc: ToolError) -> bool:
    return exc.code == ErrorCode.UPSTREAM_ERROR and exc.details.get("upstream_status") == 404


def _utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _path(source: dict[str, Any], dotted: str) -> Any:
    node: Any = source
    if dotted in source:
        return source[dotted]
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


class OpenSearchReadApi:
    def __init__(self, client: OpenSearchClient, common: CommonSettings) -> None:
        self._client = client
        self._common = common

    # -- validation helpers ------------------------------------------------------------

    @staticmethod
    def _limit(limit: int) -> None:
        if not 1 <= limit <= 100:
            raise invalid_input("limit", "phải nằm trong khoảng 1..100", SOURCE)

    @staticmethod
    def _index(value: str, field: str) -> None:
        if not 1 <= len(value) <= 256:
            raise invalid_input(field, "độ dài phải từ 1 đến 256 ký tự", SOURCE)
        if not _INDEX_PATTERN.match(value):
            raise invalid_input(field, "không được chứa khoảng trắng hoặc / \\ ? #", SOURCE)

    @staticmethod
    def _query(query: str) -> None:
        if not 1 <= len(query) <= 2048:
            raise invalid_input("query", "độ dài phải từ 1 đến 2048 ký tự", SOURCE)

    def _timeout(self, timeout_s: int, tool: str) -> None:
        if not 1 <= timeout_s <= 22:
            raise invalid_input("timeout_s", "phải nằm trong khoảng 1..22", SOURCE)
        deadline = tool_deadline_for(tool, self._common)
        if timeout_s >= deadline:
            raise invalid_input(
                "timeout_s", f"phải nhỏ hơn deadline của tool ({deadline:g}s)", SOURCE
            )

    def _window(self, time_from: datetime, time_to: datetime) -> tuple[datetime, datetime]:
        return parse_time_range(
            time_from, time_to, max_days=self._common.max_time_range_days, source=SOURCE
        )

    def _state(self) -> CallState:
        return CallState(
            self._common.max_output_bytes, redact_disabled=self._common.redact_disabled
        )

    @staticmethod
    def _bool_query(query: str, time_field: str, start: datetime, end: datetime) -> dict[str, Any]:
        return {
            "bool": {
                "must": [
                    {
                        "query_string": {
                            "query": query,
                            "allow_leading_wildcard": False,
                            "analyze_wildcard": False,
                        }
                    }
                ],
                "filter": [{"range": {time_field: {"gte": _utc(start), "lte": _utc(end)}}}],
            }
        }

    async def _search(self, index: str, body: dict[str, Any], timeout_s: int) -> Any | None:
        """The search response, or None when the index does not exist."""
        try:
            return await self._client.search(index, body, request_timeout=timeout_s + 1)
        except ToolError as exc:
            if _is_not_found(exc):
                return None
            raise

    def _not_found(self, call: CallState, echo: dict[str, Any], identifier: str) -> ToolOutcome:
        result = not_found_result(SourceType.OPENSEARCH, started=call.started, query_echo=echo)
        return ToolOutcome(result, identifier=identifier)

    # -- documents (shared by search_logs and search_dsl) ------------------------------------

    def _documents(
        self,
        hits: list[dict[str, Any]],
        call: CallState,
        *,
        time_field: str,
        hide_time_field: bool,
    ) -> tuple[list[dict[str, Any]], list[Citation], int]:
        """Map hits to items; stops when the byte budget is spent. Returns the count used."""
        items: list[dict[str, Any]] = []
        citations: list[Citation] = []
        for raw in hits:
            if call.budget.exhausted:
                break
            source = dict(raw.get("_source") or {})
            timestamp = mappers.iso_or_none(_path(source, time_field))
            if hide_time_field:
                source.pop(time_field, None)
            content_id = f"{raw['_index']}/{raw['_id']}"
            clean = sanitize_json(source, call, source=SOURCE, content_id=content_id)
            highlights = {
                str(name): [call.plain(str(fragment)) for fragment in fragments]
                for name, fragments in (raw.get("highlight") or {}).items()
            } or None
            item, citation = mappers.map_document(
                raw,
                source=clean,
                timestamp=timestamp,
                highlights=highlights,
                citation_ref=len(citations),
            )
            items.append(item)
            citations.append(citation)
        return items, citations, len(items)

    # -- opensearch_list_indices -----------------------------------------------------------

    async def list_indices(
        self, *, pattern: str = "*", include_system: bool = False, limit: int = 20
    ) -> ToolOutcome:
        self._limit(limit)
        self._index(pattern, "pattern")
        call = self._state()
        try:
            rows = await self._client.cat_indices(pattern)
        except ToolError as exc:
            if not _is_not_found(exc):
                raise
            rows = []
        rows = [r for r in rows if include_system or not str(r["index"]).startswith(".")]
        kept, more = rows[:limit], len(rows) - limit
        if more > 0:
            call.warn(f"còn {more} index khớp nữa; thu hẹp pattern hoặc tăng limit")
            call.truncated_elsewhere = True
        items: list[dict[str, Any]] = []
        citations: list[Citation] = []
        for row in kept:
            item, citation = mappers.map_index(row, citation_ref=len(citations))
            items.append(item)
            citations.append(citation)
        result = build_result(
            SourceType.OPENSEARCH, items, citations, started=call.started,
            query_echo={"pattern": pattern, "include_system": include_system, "limit": limit},
            truncated=call.truncated, warnings=call.warnings,
        )  # fmt: skip
        return ToolOutcome(result, query_description=f"index khớp '{pattern}'")

    # -- opensearch_get_mapping ---------------------------------------------------------------

    async def get_mapping(self, *, index: str, field_filter: str | None = None) -> ToolOutcome:
        self._index(index, "index")
        call = self._state()
        echo = {"index": index}
        try:
            response = await self._client.get_mapping(index)
        except ToolError as exc:
            if _is_not_found(exc):
                return self._not_found(call, echo, f"Index '{index}'")
            raise
        if len(response) > 1:
            call.warn(f"pattern khớp {len(response)} index; mapping là hợp của các index")
        fields = mappers.flatten_mapping(response, field_filter)
        item, citation = mappers.map_mapping(index, fields)
        result = build_result(
            SourceType.OPENSEARCH, [item], [citation], started=call.started,
            query_echo=echo | ({"field_filter": field_filter} if field_filter else {}),
            warnings=call.warnings,
        )  # fmt: skip
        return ToolOutcome(result, identifier=f"Index '{index}'")

    # -- opensearch_search_logs -----------------------------------------------------------------

    async def search_logs(
        self,
        *,
        index_pattern: str,
        query: str,
        time_from: datetime,
        time_to: datetime,
        time_field: str = "@timestamp",
        fields: list[str] | None = None,
        sort_order: str = "desc",
        highlight: bool = False,
        limit: int = 20,
        cursor: str | None = None,
        timeout_s: int = 20,
    ) -> ToolOutcome:
        fields = list(fields or [])
        self._index(index_pattern, "index_pattern")
        self._query(query)
        self._limit(limit)
        if len(fields) > 30:
            raise invalid_input("fields", "tối đa 30 field", SOURCE)
        if sort_order not in ("asc", "desc"):
            raise invalid_input("sort_order", "phải là asc hoặc desc", SOURCE)
        if not 1 <= len(time_field) <= 256:
            raise invalid_input("time_field", "độ dài phải từ 1 đến 256 ký tự", SOURCE)
        start, end = self._window(time_from, time_to)
        self._timeout(timeout_s, "opensearch_search_logs")
        fingerprint = hashlib.sha1(  # noqa: S324 - cursor/query binding, not security
            json.dumps(
                [index_pattern, query, time_field, _utc(start), _utc(end), sort_order, fields]
            ).encode()
        ).hexdigest()[:16]
        state = decode_cursor(cursor, source=SOURCE)
        search_after = state.get("sa")
        if state and (state.get("fp") != fingerprint or not isinstance(search_after, list)):
            raise invalid_input(
                "cursor", "cursor thuộc một truy vấn khác; dùng đúng next_cursor", SOURCE
            )

        call = self._state()
        body: dict[str, Any] = {
            "query": self._bool_query(query, time_field, start, end),
            "size": limit + 1,
            "sort": [
                {time_field: {"order": sort_order, "unmapped_type": "date"}},
                {"_id": {"order": sort_order}},
            ],
            "timeout": f"{timeout_s}s",
            "track_total_hits": False,
        }
        if fields:
            body["_source"] = fields + ([] if time_field in fields else [time_field])
        if highlight:
            body["highlight"] = {
                "fields": {"*": {}},
                "pre_tags": ["**"],
                "post_tags": ["**"],
                "fragment_size": 200,
                "number_of_fragments": 2,
            }
        if search_after:
            body["search_after"] = search_after
        echo = {
            "index_pattern": index_pattern, "query": query, "time_field": time_field,
            "time_from": _utc(start), "time_to": _utc(end), "limit": limit,
        }  # fmt: skip
        response = await self._search(index_pattern, body, timeout_s)
        if response is None:
            return self._not_found(call, echo, f"Index '{index_pattern}'")

        hits = list((response.get("hits") or {}).get("hits") or [])
        page, overflow = hits[:limit], len(hits) > limit
        items, citations, used = self._documents(
            page, call, time_field=time_field,
            hide_time_field=bool(fields) and time_field not in fields,
        )  # fmt: skip
        has_more = overflow or used < len(page)
        if used < len(page):
            call.truncated_elsewhere = True
            call.warn("đã đạt giới hạn byte của response; dùng next_cursor để lấy tiếp")
        if response.get("timed_out"):
            call.truncated_elsewhere = True
            call.warn(f"truy vấn chạm timeout_s={timeout_s}s; kết quả có thể chưa đầy đủ")
        next_cursor = None
        if has_more and used:
            next_cursor = encode_cursor({"sa": page[used - 1].get("sort") or [], "fp": fingerprint})
        elif has_more:
            has_more = False
        result = build_result(
            SourceType.OPENSEARCH, items, citations, started=call.started, query_echo=echo,
            next_cursor=next_cursor, truncated=call.truncated, warnings=call.warnings,
            redactions=call.counter.count,
        )  # fmt: skip
        return ToolOutcome(result, query_description=f"log khớp '{query}' trong '{index_pattern}'")

    # -- opensearch_count ---------------------------------------------------------------------

    async def count(
        self,
        *,
        index_pattern: str,
        query: str,
        time_from: datetime,
        time_to: datetime,
        time_field: str = "@timestamp",
        timeout_s: int = 20,
    ) -> ToolOutcome:
        self._index(index_pattern, "index_pattern")
        self._query(query)
        start, end = self._window(time_from, time_to)
        self._timeout(timeout_s, "opensearch_count")
        call = self._state()
        echo = {"index_pattern": index_pattern, "query": query, "time_field": time_field,
                "time_from": _utc(start), "time_to": _utc(end)}  # fmt: skip
        try:
            response = await self._client.count(
                index_pattern,
                {"query": self._bool_query(query, time_field, start, end)},
                request_timeout=timeout_s + 1,
            )
        except ToolError as exc:
            if _is_not_found(exc):
                return self._not_found(call, echo, f"Index '{index_pattern}'")
            raise
        count = int(response.get("count") or 0)
        if count == 0:
            call.warn("0 document khớp truy vấn trong khoảng thời gian")
            items: list[dict[str, Any]] = []
            citations: list[Citation] = []
        else:
            item, citation = mappers.map_count(index_pattern, count, start, end, query)
            items, citations = [item], [citation]
        result = build_result(
            SourceType.OPENSEARCH, items, citations, started=call.started, query_echo=echo,
            warnings=call.warnings,
        )  # fmt: skip
        return ToolOutcome(result, query_description=f"document khớp '{query}'")

    # -- opensearch_aggregate -----------------------------------------------------------------

    async def aggregate(
        self,
        *,
        index_pattern: str,
        agg_type: str,
        field: str,
        time_from: datetime,
        time_to: datetime,
        query: str = "*",
        time_field: str = "@timestamp",
        interval: str | None = None,
        size: int = 10,
        timeout_s: int = 20,
    ) -> ToolOutcome:
        self._index(index_pattern, "index_pattern")
        if agg_type not in ("terms", "date_histogram"):
            raise invalid_input("agg_type", "phải là terms hoặc date_histogram", SOURCE)
        if not 1 <= len(field) <= 256:
            raise invalid_input("field", "độ dài phải từ 1 đến 256 ký tự", SOURCE)
        query = query or "*"
        if len(query) > 2048:
            raise invalid_input("query", "độ dài tối đa 2048 ký tự", SOURCE)
        if not 1 <= size <= 100:
            raise invalid_input("size", "phải nằm trong khoảng 1..100", SOURCE)
        start, end = self._window(time_from, time_to)
        if agg_type == "date_histogram":
            if interval not in _INTERVAL_SECONDS:
                raise invalid_input(
                    "interval", f"bắt buộc, thuộc {list(_INTERVAL_SECONDS)}", SOURCE
                )
            buckets = (end - start).total_seconds() / _INTERVAL_SECONDS[interval]
            if buckets > _MAX_HISTOGRAM_BUCKETS:
                raise invalid_input(
                    "interval",
                    f"khoảng thời gian / interval tạo ~{int(buckets)} bucket "
                    f"(tối đa {_MAX_HISTOGRAM_BUCKETS}); dùng interval lớn hơn",
                    SOURCE,
                )
            agg: dict[str, Any] = {"date_histogram": {"field": field, "fixed_interval": interval}}
        else:
            agg = {"terms": {"field": field, "size": size}}
        self._timeout(timeout_s, "opensearch_aggregate")
        call = self._state()
        body = {
            "size": 0,
            "query": self._bool_query(query, time_field, start, end),
            "aggs": {"agg": agg},
            "timeout": f"{timeout_s}s",
            "track_total_hits": False,
        }
        echo: dict[str, Any] = {
            "index_pattern": index_pattern, "agg_type": agg_type, "field": field,
            "query": query, "time_from": _utc(start), "time_to": _utc(end),
        }  # fmt: skip
        response = await self._search(index_pattern, body, timeout_s)
        if response is None:
            return self._not_found(call, echo, f"Index '{index_pattern}'")
        raw_buckets = ((response.get("aggregations") or {}).get("agg") or {}).get("buckets") or []
        items = [
            mappers.map_bucket(b, date_histogram=agg_type == "date_histogram") for b in raw_buckets
        ]
        citations: list[Citation] = []
        if items:
            window = f"{start.astimezone(UTC):%H:%M}–{end.astimezone(UTC):%H:%MZ}"
            citations = [
                mappers.scope_citation(
                    f"{agg_type}({field}) {index_pattern} {window}",
                    {
                        "index": index_pattern,
                        "agg_type": agg_type,
                        "field": field,
                        "time_from": _utc(start),
                        "time_to": _utc(end),
                    },
                )  # fmt: skip
            ]
        if response.get("timed_out"):
            call.truncated_elsewhere = True
            call.warn(f"truy vấn chạm timeout_s={timeout_s}s; kết quả có thể chưa đầy đủ")
        result = build_result(
            SourceType.OPENSEARCH, items, citations, started=call.started, query_echo=echo,
            truncated=call.truncated, warnings=call.warnings,
        )  # fmt: skip
        return ToolOutcome(result, query_description=f"bucket {agg_type}({field})")

    # -- opensearch_search_dsl ----------------------------------------------------------------

    async def search_dsl(
        self, *, index_pattern: str, body: dict[str, Any], limit: int = 20, timeout_s: int = 20
    ) -> ToolOutcome:
        self._index(index_pattern, "index_pattern")
        self._limit(limit)
        self._timeout(timeout_s, "opensearch_search_dsl")
        for key in body:
            if key not in _USER_DSL_KEYS:
                raise not_permitted(f"body.{key}", _USER_DSL_KEYS)
        assert_body_allowed(body)  # deep scan: script / runtime_mappings / scroll / pit / ...
        sent = copy.deepcopy(body)
        call = self._state()
        size = sent.get("size", limit)
        offset = sent.get("from", 0)
        for name, value, high in (("size", size, _MAX_DSL_SIZE), ("from", offset, _MAX_DSL_FROM)):
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= high:
                raise invalid_input(f"body.{name}", f"phải là số nguyên trong 0..{high}", SOURCE)
        if size > limit:
            call.warn(f"size giảm từ {size} xuống {limit} theo limit")
            size = limit
        if offset + size > _DEEP_PAGING_CAP:
            raise invalid_input(
                "body.from", f"from + size phải <= {_DEEP_PAGING_CAP}; dùng search_after", SOURCE
            )
        sent["size"] = size
        sent["timeout"] = f"{timeout_s}s"
        echo = {"index_pattern": index_pattern, "limit": limit, "body_keys": sorted(body)}
        response = await self._search(index_pattern, sent, timeout_s)
        if response is None:
            return self._not_found(call, echo, f"Index '{index_pattern}'")
        hits = list((response.get("hits") or {}).get("hits") or [])
        items, citations, used = self._documents(
            hits, call, time_field="@timestamp", hide_time_field=False
        )
        if used < len(hits):
            call.truncated_elsewhere = True
            call.warn("đã đạt giới hạn byte của response; thu hẹp query hoặc giảm size")
        if response.get("aggregations"):
            call.warn(
                "aggregations không nằm trong kết quả (chỉ trả document); dùng opensearch_aggregate"
            )
        if response.get("timed_out"):
            call.truncated_elsewhere = True
            call.warn(f"truy vấn chạm timeout_s={timeout_s}s; kết quả có thể chưa đầy đủ")
        result = build_result(
            SourceType.OPENSEARCH, items, citations, started=call.started, query_echo=echo,
            truncated=call.truncated, warnings=call.warnings, redactions=call.counter.count,
        )  # fmt: skip
        return ToolOutcome(result, query_description=f"document khớp DSL trong '{index_pattern}'")
