"""Kibana tool layer: input bounds, redaction, paging and the deep-link builder."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from mcp_common.config import CommonSettings
from mcp_common.envelope import Citation, SourceType
from mcp_common.errors import ErrorCode, ToolError
from mcp_common.tooling import (
    CallState,
    ToolOutcome,
    build_result,
    decode_cursor,
    encode_cursor,
    invalid_input,
    not_found_result,
    parse_time_range,
)

from mcp_kibana import mappers
from mcp_kibana.client import OP_FIND, OP_GET, SOURCE, KibanaClient, check_space

__all__ = ["KibanaReadApi"]

SAVED_OBJECT_TYPES = ("dashboard", "visualization", "lens", "search", "index-pattern")


def _is_not_found(exc: ToolError) -> bool:
    return exc.code == ErrorCode.UPSTREAM_ERROR and exc.details.get("upstream_status") == 404


class KibanaReadApi:
    def __init__(self, client: KibanaClient, common: CommonSettings) -> None:
        self._client = client
        self._common = common

    def _state(self) -> CallState:
        return CallState(
            self._common.max_output_bytes, redact_disabled=self._common.redact_disabled
        )

    @staticmethod
    def _id(value: str, field: str) -> None:
        if not 1 <= len(value) <= 128:
            raise invalid_input(field, "độ dài phải từ 1 đến 128 ký tự", SOURCE)

    def _title(self, call: CallState, raw: dict[str, Any]) -> str:
        title = str((raw.get("attributes") or {}).get("title") or raw["id"])
        return call.plain(title) or str(raw["id"])

    def _map(
        self, call: CallState, raw: dict[str, Any], space: str | None, ref: int
    ) -> tuple[dict[str, Any], Citation]:
        description = (raw.get("attributes") or {}).get("description")
        return mappers.map_saved_object(
            raw,
            base_url=self._client.base_url,
            space=space,
            title=self._title(call, raw),
            description=call.text(description, SOURCE, f"{raw['type']}:{raw['id']}:description"),
            citation_ref=ref,
        )

    # -- kibana_find_saved_objects ----------------------------------------------------------

    async def find_saved_objects(
        self,
        *,
        query: str,
        types: list[str] | None = None,
        space: str | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> ToolOutcome:
        types = list(types) if types is not None else ["dashboard"]
        if not 1 <= len(query) <= 256:
            raise invalid_input("query", "độ dài phải từ 1 đến 256 ký tự", SOURCE)
        if not 1 <= len(types) <= 5 or any(t not in SAVED_OBJECT_TYPES for t in types):
            raise invalid_input("types", f"1..5 giá trị thuộc {list(SAVED_OBJECT_TYPES)}", SOURCE)
        if not 1 <= limit <= 100:
            raise invalid_input("limit", "phải nằm trong khoảng 1..100", SOURCE)
        check_space(space)
        page = decode_cursor(cursor, source=SOURCE).get("page", 1)
        if not isinstance(page, int) or page < 1:
            raise invalid_input("cursor", "cursor không hợp lệ; dùng đúng meta.next_cursor", SOURCE)

        call = self._state()
        data = await self._client.get(
            OP_FIND,
            params={
                "type": types,
                "search": f"{query}*",
                "search_fields": ["title", "description"],
                "fields": ["title", "description"],
                "per_page": limit,
                "page": page,
            },
            space=space,
        )
        items: list[dict[str, Any]] = []
        citations: list[Citation] = []
        for raw in data.get("saved_objects") or []:
            item, citation = self._map(call, raw, space, len(citations))
            items.append(item)
            citations.append(citation)
        total = int(data.get("total") or 0)
        next_cursor = encode_cursor({"page": page + 1}) if page * limit < total else None
        echo: dict[str, Any] = {"query": query, "types": types, "limit": limit}
        if space:
            echo["space"] = space
        result = build_result(
            SourceType.KIBANA,
            items,
            citations,
            started=call.started,
            query_echo=echo,
            next_cursor=next_cursor,
            truncated=call.truncated,
            warnings=call.warnings,
            redactions=call.counter.count,
        )
        return ToolOutcome(result, query_description=f"saved object khớp '{query}'")

    # -- kibana_get_saved_object ------------------------------------------------------------

    async def get_saved_object(
        self,
        *,
        type: str,
        id: str,
        space: str | None = None,  # noqa: A002 (contract names)
    ) -> ToolOutcome:
        if type not in SAVED_OBJECT_TYPES:
            raise invalid_input("type", f"phải thuộc {list(SAVED_OBJECT_TYPES)}", SOURCE)
        self._id(id, "id")
        check_space(space)
        call = self._state()
        echo = {"type": type, "id": id}
        try:
            raw = await self._client.get(OP_GET, {"type": type, "id": id}, space=space)
        except ToolError as exc:
            if not _is_not_found(exc):
                raise
            result = not_found_result(SourceType.KIBANA, started=call.started, query_echo=echo)
            return ToolOutcome(result, identifier=f"{type} '{id}'")
        item, citation = self._map(call, raw, space, 0)
        result = build_result(
            SourceType.KIBANA,
            [item],
            [citation],
            started=call.started,
            query_echo=echo,
            truncated=call.truncated,
            warnings=call.warnings,
            redactions=call.counter.count,
        )
        return ToolOutcome(result, identifier=f"{type} '{id}'")

    # -- kibana_build_dashboard_link ----------------------------------------------------------

    async def build_dashboard_link(
        self,
        *,
        dashboard_id: str,
        time_from: datetime,
        time_to: datetime,
        space: str | None = None,
        query: str | None = None,
    ) -> ToolOutcome:
        self._id(dashboard_id, "dashboard_id")
        check_space(space)
        start, end = parse_time_range(
            time_from, time_to, max_days=self._common.max_time_range_days, source=SOURCE
        )
        call = self._state()
        echo = {"dashboard_id": dashboard_id, "time_from": mappers.format_time(start),
                "time_to": mappers.format_time(end)}  # fmt: skip
        # The link itself is a pure function; this single GET only verifies the dashboard
        # exists and fetches its title (contract: exactly one GET).
        try:
            raw = await self._client.get(
                OP_GET, {"type": "dashboard", "id": dashboard_id}, space=space
            )
        except ToolError as exc:
            if not _is_not_found(exc):
                raise
            result = not_found_result(SourceType.KIBANA, started=call.started, query_echo=echo)
            return ToolOutcome(result, identifier=f"dashboard '{dashboard_id}'")
        url = mappers.dashboard_url(self._client.base_url, dashboard_id, space, start, end, query)
        item, citation = mappers.map_dashboard_link(
            dashboard_id=dashboard_id,
            title=self._title(call, raw),
            url=url,
            time_from=start,
            time_to=end,
            query=query,
        )
        result = build_result(
            SourceType.KIBANA,
            [item],
            [citation],
            started=call.started,
            query_echo=echo,
            warnings=call.warnings,
            redactions=call.counter.count,
        )
        return ToolOutcome(result, identifier=f"dashboard '{dashboard_id}'")
