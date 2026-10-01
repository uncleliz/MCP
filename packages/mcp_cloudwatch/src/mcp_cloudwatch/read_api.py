"""CloudWatch tool layer: input bounds, paging, Logs Insights lifecycle, redaction, budget.

Tool bounds (`limit <= 100`, mandatory time range <= `MCP_MAX_TIME_RANGE_DAYS`, `timeout_s`
below the tool deadline, the Logs Insights command allowlist) live here, not in `client.py`.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime
from typing import Any

from mcp_common.config import CommonSettings
from mcp_common.envelope import Citation, SourceType
from mcp_common.errors import ErrorCode, NotPermittedError, ToolError
from mcp_common.logging import get_logger
from mcp_common.runtime import tool_deadline_for
from mcp_common.tooling import (
    CallState,
    ToolOutcome,
    build_result,
    decode_cursor,
    effective_max_bytes,
    encode_cursor,
    invalid_input,
    not_found_result,
    parse_time_range,
    sanitize_json,
)

from mcp_cloudwatch import mappers
from mcp_cloudwatch.client import SOURCE, CloudWatchClient
from mcp_cloudwatch.settings import Settings

__all__ = ["INSIGHTS_COMMANDS", "CloudWatchReadApi", "validate_insights_query"]

INSIGHTS_COMMANDS = ("fields", "filter", "stats", "sort", "limit", "parse", "dedup", "display")
_STATS = ("Average", "Sum", "Minimum", "Maximum", "SampleCount", "p50", "p90", "p95", "p99")
_PERIODS = (60, 300, 900, 3600, 21600, 86400)
_HISTORY_TYPES = ("ConfigurationUpdate", "StateUpdate", "Action")
_ALARM_STATES = ("OK", "ALARM", "INSUFFICIENT_DATA")
_FAILED_QUERY_STATES = ("Failed", "Cancelled", "Timeout")
_LOG_GROUPS_PAGE = 50
_MAX_LIST_METRIC_PAGES = 5
_MAX_METRIC_PAGES = 10
_MAX_DATAPOINTS = 1440
_REGEX_OPENERS = re.compile(r"(?:like|=~)\s*$", re.IGNORECASE)

_logger = get_logger(SOURCE)


def _is_not_found(exc: ToolError) -> bool:
    return exc.code == ErrorCode.UPSTREAM_ERROR and exc.details.get("upstream_status") == 404


def _split_pipes(query: str) -> list[str]:
    """Split a Logs Insights query on `|`, ignoring pipes inside quotes and `/regex/`."""
    segments: list[str] = []
    buffer: list[str] = []
    quote: str | None = None
    in_regex = False
    for index, char in enumerate(query):
        previous = query[index - 1] if index else ""
        if in_regex:
            buffer.append(char)
            if char == "/" and previous != "\\":
                in_regex = False
        elif quote:
            buffer.append(char)
            if char == quote and previous != "\\":
                quote = None
        elif char in "'\"`":
            quote = char
            buffer.append(char)
        elif char == "/" and _REGEX_OPENERS.search("".join(buffer)):
            in_regex = True
            buffer.append(char)
        elif char == "|":
            segments.append("".join(buffer))
            buffer = []
        else:
            buffer.append(char)
    segments.append("".join(buffer))
    return segments


def validate_insights_query(query: str) -> None:
    """Refuse any pipeline stage whose command is not in :data:`INSIGHTS_COMMANDS`."""
    for segment in _split_pipes(query):
        match = re.match(r"\s*([A-Za-z_]+)", segment)
        command = match.group(1) if match else ""
        if command.lower() not in INSIGHTS_COMMANDS:
            raise NotPermittedError(
                f"Lệnh Logs Insights '{command}' không nằm trong allowlist.",
                source=SOURCE,
                operation=f"insights command '{command}'",
                allowlist=list(INSIGHTS_COMMANDS),
            )


class CloudWatchReadApi:
    def __init__(
        self, client: CloudWatchClient, common: CommonSettings, settings: Settings
    ) -> None:
        self._client = client
        self._common = common
        self._settings = settings

    # -- validation helpers ------------------------------------------------------------

    @staticmethod
    def _limit(limit: int) -> None:
        if not 1 <= limit <= 100:
            raise invalid_input("limit", "phải nằm trong khoảng 1..100", SOURCE)

    @staticmethod
    def _text_len(value: str | None, field: str, low: int, high: int) -> None:
        if value is not None and not low <= len(value) <= high:
            raise invalid_input(field, f"độ dài phải từ {low} đến {high} ký tự", SOURCE)

    def _window(self, time_from: datetime, time_to: datetime) -> tuple[datetime, datetime]:
        return parse_time_range(
            time_from, time_to, max_days=self._common.max_time_range_days, source=SOURCE
        )

    def _timeout(self, timeout_s: int, tool: str) -> None:
        if not 1 <= timeout_s <= 22:
            raise invalid_input("timeout_s", "phải nằm trong khoảng 1..22", SOURCE)
        deadline = tool_deadline_for(tool, self._common)
        if timeout_s >= deadline:
            raise invalid_input(
                "timeout_s", f"phải nhỏ hơn deadline của tool ({deadline:g}s)", SOURCE
            )

    def _state(self, budget: int | None = None, warnings: list[str] | None = None) -> CallState:
        return CallState(
            budget if budget is not None else self._common.max_output_bytes,
            warnings,
            redact_disabled=self._common.redact_disabled,
        )

    @staticmethod
    def _token(cursor: str | None, key: str = "t") -> str | None:
        token = decode_cursor(cursor, source=SOURCE).get(key)
        if token is not None and not isinstance(token, str):
            raise invalid_input("cursor", "cursor không hợp lệ; dùng đúng meta.next_cursor", SOURCE)
        return token

    @staticmethod
    def _drop_none(params: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in params.items() if v is not None}

    @staticmethod
    def _not_found(call: CallState, echo: dict[str, Any], identifier: str) -> ToolOutcome:
        result = not_found_result(SourceType.CLOUDWATCH, started=call.started, query_echo=echo)
        return ToolOutcome(result, identifier=identifier)

    # -- cloudwatch_list_log_groups ------------------------------------------------------

    async def list_log_groups(
        self, *, name_prefix: str | None = None, limit: int = 20, cursor: str | None = None
    ) -> ToolOutcome:
        self._limit(limit)
        self._text_len(name_prefix, "name_prefix", 0, 512)
        token = self._token(cursor)
        call = self._state()
        items: list[dict[str, Any]] = []
        citations: list[Citation] = []
        while len(items) < limit:
            response = await self._client.call(
                "logs",
                "DescribeLogGroups",
                **self._drop_none(
                    {
                        "limit": min(_LOG_GROUPS_PAGE, limit - len(items)),
                        "logGroupNamePrefix": name_prefix,
                        "nextToken": token,
                    }
                ),
            )
            for raw in response.get("logGroups", []):
                item, citation = mappers.map_log_group(raw, citation_ref=len(citations))
                items.append(item)
                citations.append(citation)
            token = response.get("nextToken")
            if not token:
                break
        result = build_result(
            SourceType.CLOUDWATCH, items, citations, started=call.started,
            query_echo={"name_prefix": name_prefix, "limit": limit},
            next_cursor=encode_cursor({"t": token}) if token else None,
        )  # fmt: skip
        return ToolOutcome(result, query_description=f"log group khớp '{name_prefix or '*'}'")

    # -- cloudwatch_filter_log_events -----------------------------------------------------

    async def filter_log_events(
        self,
        *,
        log_group_name: str,
        time_from: datetime,
        time_to: datetime,
        filter_pattern: str | None = None,
        log_stream_name_prefix: str | None = None,
        limit: int = 20,
        cursor: str | None = None,
        max_bytes: int = 65536,
    ) -> ToolOutcome:
        self._text_len(log_group_name, "log_group_name", 1, 512)
        self._text_len(filter_pattern, "filter_pattern", 0, 1024)
        self._limit(limit)
        if not 1024 <= max_bytes <= 131072:
            raise invalid_input("max_bytes", "phải nằm trong khoảng 1024..131072", SOURCE)
        start, end = self._window(time_from, time_to)
        token = self._token(cursor)
        budget, clamp_warnings = effective_max_bytes(max_bytes, self._common)
        call = self._state(budget, clamp_warnings)
        echo = {"log_group_name": log_group_name, "filter_pattern": filter_pattern,
                "time_from": mappers.iso(start), "time_to": mappers.iso(end)}  # fmt: skip
        try:
            response = await self._client.call(
                "logs",
                "FilterLogEvents",
                **self._drop_none(
                    {
                        "logGroupName": log_group_name,
                        "filterPattern": filter_pattern,
                        "logStreamNamePrefix": log_stream_name_prefix,
                        "startTime": int(start.timestamp() * 1000),
                        "endTime": int(end.timestamp() * 1000),
                        "limit": limit,
                        "nextToken": token,
                    }
                ),
            )
        except ToolError as exc:
            if _is_not_found(exc):
                return self._not_found(call, echo, f"Log group '{log_group_name}'")
            raise
        events = response.get("events", [])
        items: list[dict[str, Any]] = []
        citations: list[Citation] = []
        for raw in events:
            if call.budget.exhausted:
                break
            already_cut = call.budget.truncated
            message = call.text(
                raw.get("message", ""), SOURCE, f"{log_group_name}:{raw.get('eventId')}"
            )
            item, citation = mappers.map_log_event(
                raw, log_group=log_group_name, message=message or "",
                truncated=call.budget.truncated and not already_cut, citation_ref=len(citations),
            )  # fmt: skip
            items.append(item)
            citations.append(citation)
        next_token = response.get("nextToken")
        if len(items) < len(events):
            call.truncated_elsewhere = True
            call.warn("đã đạt max_bytes; thu hẹp khoảng thời gian hoặc filter_pattern")
            next_token = None  # cannot resume mid-page
        elif not events and next_token:
            call.warn("trang này không có event nhưng chưa quét hết cửa sổ; dùng next_cursor")
        result = build_result(
            SourceType.CLOUDWATCH, items, citations, started=call.started, query_echo=echo,
            next_cursor=encode_cursor({"t": next_token}) if next_token else None,
            truncated=call.truncated, warnings=call.warnings, redactions=call.counter.count,
        )  # fmt: skip
        return ToolOutcome(result, query_description=f"log event trong '{log_group_name}'")

    # -- cloudwatch_run_logs_insights ------------------------------------------------------

    async def run_logs_insights(
        self,
        *,
        log_group_names: list[str],
        query: str,
        time_from: datetime,
        time_to: datetime,
        limit: int = 20,
        timeout_s: int = 20,
    ) -> ToolOutcome:
        if not 1 <= len(log_group_names) <= 20:
            raise invalid_input("log_group_names", "cần 1..20 log group", SOURCE)
        for name in log_group_names:
            self._text_len(name, "log_group_names", 1, 512)
        self._text_len(query, "query", 1, 4096)
        self._limit(limit)
        start, end = self._window(time_from, time_to)
        self._timeout(timeout_s, "cloudwatch_run_logs_insights")
        validate_insights_query(query)
        call = self._state()
        groups = list(log_group_names)
        echo = {"log_group_names": groups, "limit": limit, "timeout_s": timeout_s}
        try:
            started = await self._client.call(
                "logs",
                "StartQuery",
                logGroupNames=groups,
                startTime=int(start.timestamp()),
                endTime=int(end.timestamp()),
                queryString=query,
                limit=limit,
            )
        except ToolError as exc:
            if _is_not_found(exc):
                return self._not_found(call, echo, f"Log group {', '.join(groups)}")
            raise
        query_id = str(started["queryId"])

        response, timed_out = await self._poll_query(query_id, timeout_s)
        rows = []
        for raw in (response.get("results") or [])[:limit]:
            values = {f["field"]: f.get("value") for f in raw if f["field"] != "@ptr"}
            rows.append(
                mappers.map_insights_row(
                    sanitize_json(values, call, source=SOURCE, content_id=f"insights:{query_id}")
                )
            )
        if timed_out:
            call.truncated_elsewhere = True
            call.warn(
                f"query dừng sau {timeout_s}s, StopQuery đã được gọi; kết quả chưa đầy đủ và "
                "KHÔNG có cursor để tiếp tục — chạy lại với khung thời gian hẹp hơn"
            )
        label = f"Logs Insights {', '.join(groups)}" + (" (kết quả một phần)" if timed_out else "")
        scope = mappers.scope_citation(
            label,
            {"log_groups": groups, "query": query, "time_from": mappers.iso(start),
             "time_to": mappers.iso(end)},
            start=start, end=end,
        )  # fmt: skip
        result = build_result(
            SourceType.CLOUDWATCH, rows, [scope] if rows else [], started=call.started,
            query_echo=echo, truncated=call.truncated, warnings=call.warnings,
            redactions=call.counter.count, scope_citation=scope,
        )  # fmt: skip
        return ToolOutcome(result, query_description=f"hàng Logs Insights cho '{query[:60]}'")

    async def _poll_query(self, query_id: str, timeout_s: int) -> tuple[dict[str, Any], bool]:
        """Poll `GetQueryResults`; `StopQuery` (shielded, exactly once) unless it finished."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_s
        finished = False
        response: dict[str, Any] = {}
        try:
            while True:
                response = await self._client.call("logs", "GetQueryResults", queryId=query_id)
                status = response.get("status")
                if status == "Complete":
                    finished = True
                    return response, False
                if status in _FAILED_QUERY_STATES:
                    finished = True
                    raise ToolError(
                        ErrorCode.UPSTREAM_ERROR,
                        f"Logs Insights query kết thúc với trạng thái {status}.",
                        SOURCE,
                        status == "Timeout",
                        details={"query_status": status},
                    )
                if loop.time() >= deadline:
                    return response, True
                await asyncio.sleep(self._settings.insights_poll_interval)
        finally:
            if not finished:
                await self._stop_query(query_id)

    async def _stop_query(self, query_id: str) -> None:
        """ADR-0006 A3 / 0008 A4: StopQuery inside `try/finally` + `asyncio.shield`."""
        stop = asyncio.ensure_future(self._client.call("logs", "StopQuery", queryId=query_id))
        try:
            await asyncio.shield(stop)
        except asyncio.CancelledError:
            stop.add_done_callback(lambda t: None if t.cancelled() else t.exception())
            raise
        except ToolError as exc:
            _logger.warning(
                "StopQuery failed", extra={"tool": "cloudwatch_run_logs_insights",
                                           "error_code": exc.code.value}
            )  # fmt: skip

    # -- cloudwatch_list_metrics -------------------------------------------------------------

    async def list_metrics(
        self,
        *,
        namespace: str | None = None,
        metric_name_prefix: str | None = None,
        dimensions: list[dict[str, str]] | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> ToolOutcome:
        dimensions = self._dimensions(dimensions)
        self._limit(limit)
        self._text_len(namespace, "namespace", 0, 256)
        self._text_len(metric_name_prefix, "metric_name_prefix", 0, 256)
        state = decode_cursor(cursor, source=SOURCE)
        token, skip = state.get("t"), state.get("s", 0)
        if (token is not None and not isinstance(token, str)) or not isinstance(skip, int):
            raise invalid_input("cursor", "cursor không hợp lệ; dùng đúng meta.next_cursor", SOURCE)
        call = self._state()
        items: list[dict[str, Any]] = []
        citations: list[Citation] = []
        next_state: dict[str, Any] | None = None
        for _ in range(_MAX_LIST_METRIC_PAGES):
            response = await self._client.call(
                "cloudwatch",
                "ListMetrics",
                **self._drop_none(
                    {
                        "Namespace": namespace,
                        "Dimensions": mappers.dimensions_in(dimensions) or None,
                        "NextToken": token,
                    }
                ),
            )
            page = [
                m
                for m in response.get("Metrics", [])
                if not metric_name_prefix or m["MetricName"].startswith(metric_name_prefix)
            ][skip:]
            consumed_before, skip = skip, 0
            take = page[: limit - len(items)]
            for raw in take:
                item, citation = mappers.map_metric_definition(raw, citation_ref=len(citations))
                items.append(item)
                citations.append(citation)
            next_token = response.get("NextToken")
            if len(take) < len(page):  # the page holds more than `limit` allows
                next_state = {"t": token, "s": consumed_before + len(take)}
                break
            if len(items) >= limit or not next_token:
                next_state = {"t": next_token, "s": 0} if next_token else None
                break
            token = next_token
        else:
            next_state = {"t": token, "s": 0}
        result = build_result(
            SourceType.CLOUDWATCH, items, citations, started=call.started,
            query_echo={"namespace": namespace, "metric_name_prefix": metric_name_prefix,
                        "limit": limit},
            next_cursor=encode_cursor(next_state) if next_state else None,
        )  # fmt: skip
        return ToolOutcome(result, query_description="metric khớp bộ lọc")

    def _dimensions(self, dimensions: list[dict[str, str]] | None) -> list[dict[str, str]]:
        dims = list(dimensions or [])
        if len(dims) > 10:
            raise invalid_input("dimensions", "tối đa 10 dimension", SOURCE)
        for dim in dims:
            if not isinstance(dim, dict) or not {"name", "value"} <= set(dim):
                raise invalid_input("dimensions", "mỗi dimension cần name và value", SOURCE)
        return dims

    # -- cloudwatch_get_metric_data ------------------------------------------------------------

    async def get_metric_data(
        self,
        *,
        namespace: str,
        metric_name: str,
        time_from: datetime,
        time_to: datetime,
        dimensions: list[dict[str, str]] | None = None,
        stat: str = "Average",
        period_s: int = 300,
    ) -> ToolOutcome:
        self._text_len(namespace, "namespace", 1, 256)
        self._text_len(metric_name, "metric_name", 1, 256)
        dims = self._dimensions(dimensions)
        if stat not in _STATS:
            raise invalid_input("stat", f"phải thuộc {list(_STATS)}", SOURCE)
        if period_s not in _PERIODS:
            raise invalid_input("period_s", f"phải thuộc {list(_PERIODS)}", SOURCE)
        start, end = self._window(time_from, time_to)
        call = self._state()
        echo = {"namespace": namespace, "metric_name": metric_name, "stat": stat,
                "period_s": period_s}  # fmt: skip
        aws_dims = mappers.dimensions_in(dims)
        existing = await self._client.call(
            "cloudwatch",
            "ListMetrics",
            **self._drop_none(
                {"Namespace": namespace, "MetricName": metric_name, "Dimensions": aws_dims or None}
            ),
        )
        if not existing.get("Metrics"):
            return self._not_found(call, echo, f"Metric {namespace}/{metric_name}")
        metric = {"Namespace": namespace, "MetricName": metric_name}
        if aws_dims:
            metric["Dimensions"] = aws_dims  # type: ignore[assignment]
        query = {
            "MetricDataQueries": [
                {
                    "Id": "m1",
                    "MetricStat": {"Metric": metric, "Period": period_s, "Stat": stat},
                    "ReturnData": True,
                }
            ],
            "StartTime": start,
            "EndTime": end,
            "ScanBy": "TimestampAscending",
        }
        points: list[tuple[datetime, float]] = []
        token: str | None = None
        for _ in range(_MAX_METRIC_PAGES):
            response = await self._client.call(
                "cloudwatch", "GetMetricData", **query, **self._drop_none({"NextToken": token})
            )
            for result in response.get("MetricDataResults", []):
                points.extend(
                    zip(result.get("Timestamps", []), result.get("Values", []), strict=False)
                )
            token = response.get("NextToken")
            if not token or len(points) >= _MAX_DATAPOINTS:
                break
        points.sort(key=lambda p: p[0])
        if len(points) > _MAX_DATAPOINTS or token:
            call.truncated_elsewhere = True
            call.warn(f"chỉ trả {_MAX_DATAPOINTS} datapoint đầu; thu hẹp khoảng thời gian")
        datapoints = [
            {"timestamp": mappers.iso(ts), "value": float(value)}
            for ts, value in points[:_MAX_DATAPOINTS]
        ]
        items: list[dict[str, Any]] = []
        citations: list[Citation] = []
        if datapoints:
            item, citation = mappers.map_metric_series(
                namespace=namespace, metric_name=metric_name, dimensions=dims, stat=stat,
                period_s=period_s, datapoints=datapoints, start=start, end=end,
            )  # fmt: skip
            items, citations = [item], [citation]
        result = build_result(
            SourceType.CLOUDWATCH, items, citations, started=call.started, query_echo=echo,
            truncated=call.truncated, warnings=call.warnings,
        )  # fmt: skip
        return ToolOutcome(result, query_description=f"datapoint của {namespace}/{metric_name}")

    # -- cloudwatch_describe_alarms ---------------------------------------------------------------

    async def describe_alarms(
        self,
        *,
        alarm_name_prefix: str | None = None,
        state_value: str | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> ToolOutcome:
        self._limit(limit)
        self._text_len(alarm_name_prefix, "alarm_name_prefix", 0, 256)
        if state_value is not None and state_value not in _ALARM_STATES:
            raise invalid_input("state_value", f"phải thuộc {list(_ALARM_STATES)}", SOURCE)
        token = self._token(cursor)
        call = self._state()
        response = await self._client.call(
            "cloudwatch",
            "DescribeAlarms",
            **self._drop_none(
                {
                    "AlarmNamePrefix": alarm_name_prefix,
                    "StateValue": state_value,
                    "AlarmTypes": ["MetricAlarm"],
                    "MaxRecords": limit,
                    "NextToken": token,
                }
            ),
        )
        items: list[dict[str, Any]] = []
        citations: list[Citation] = []
        for raw in response.get("MetricAlarms", []):
            reason = raw.get("StateReason")
            item, citation = mappers.map_alarm(
                raw, state_reason=call.plain(reason) if reason else None,
                citation_ref=len(citations),
            )  # fmt: skip
            items.append(item)
            citations.append(citation)
        if not items:
            call.warn("no alarm matched — nêu rõ 'không có alarm CloudWatch nào' trong câu trả lời")
        next_token = response.get("NextToken")
        result = build_result(
            SourceType.CLOUDWATCH, items, citations, started=call.started,
            query_echo={"alarm_name_prefix": alarm_name_prefix, "state_value": state_value},
            next_cursor=encode_cursor({"t": next_token}) if next_token else None,
            warnings=call.warnings, redactions=call.counter.count,
        )  # fmt: skip
        return ToolOutcome(result, query_description="alarm CloudWatch khớp bộ lọc")

    # -- cloudwatch_describe_alarm_history ----------------------------------------------------

    async def describe_alarm_history(
        self,
        *,
        alarm_name: str,
        time_from: datetime,
        time_to: datetime,
        history_item_type: str | None = "StateUpdate",
        limit: int = 20,
        cursor: str | None = None,
    ) -> ToolOutcome:
        self._text_len(alarm_name, "alarm_name", 1, 256)
        if history_item_type is not None and history_item_type not in _HISTORY_TYPES:
            raise invalid_input("history_item_type", f"phải thuộc {list(_HISTORY_TYPES)}", SOURCE)
        self._limit(limit)
        start, end = self._window(time_from, time_to)
        token = self._token(cursor)
        call = self._state()
        echo = {"alarm_name": alarm_name, "history_item_type": history_item_type}
        known = await self._client.call(
            "cloudwatch",
            "DescribeAlarms",
            AlarmNames=[alarm_name],
            AlarmTypes=["MetricAlarm", "CompositeAlarm"],
        )
        if not known.get("MetricAlarms") and not known.get("CompositeAlarms"):
            return self._not_found(call, echo, f"Alarm '{alarm_name}'")
        response = await self._client.call(
            "cloudwatch",
            "DescribeAlarmHistory",
            **self._drop_none(
                {
                    "AlarmName": alarm_name,
                    "HistoryItemType": history_item_type,
                    "StartDate": start,
                    "EndDate": end,
                    "MaxRecords": limit,
                    "ScanBy": "TimestampDescending",
                    "NextToken": token,
                }
            ),
        )
        items = []
        for raw in response.get("AlarmHistoryItems", []):
            summary = raw.get("HistorySummary")
            items.append(
                mappers.map_alarm_history(raw, summary=call.plain(summary) if summary else None)
            )
        scope = mappers.scope_citation(
            f"alarm history {alarm_name}",
            {"alarm_name": alarm_name, "time_from": mappers.iso(start),
             "time_to": mappers.iso(end)},
            start=start, end=end,
        )  # fmt: skip
        next_token = response.get("NextToken")
        result = build_result(
            SourceType.CLOUDWATCH, items, [scope] if items else [], started=call.started,
            query_echo=echo, next_cursor=encode_cursor({"t": next_token}) if next_token else None,
            warnings=call.warnings, redactions=call.counter.count,
        )  # fmt: skip
        return ToolOutcome(result, query_description=f"lịch sử alarm '{alarm_name}'")
