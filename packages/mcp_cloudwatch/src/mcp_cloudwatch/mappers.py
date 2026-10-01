"""CloudWatch payloads -> contract item schemas + `Citation` (FR-006/AC-001, FR-015).

Pure functions, no I/O. CloudWatch has no web URL here, so `Citation.uri` is `None` and the
`locator` carries the identifier (log group/stream/timestamp, metric, alarm + window).
Free-text fields arrive already redacted/wrapped/budgeted from `read_api.py`.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from mcp_common.envelope import Citation, SourceType

__all__ = [
    "dimensions_in",
    "dimensions_out",
    "epoch_ms_to_iso",
    "iso",
    "map_alarm",
    "map_alarm_history",
    "map_insights_row",
    "map_log_event",
    "map_log_group",
    "map_metric_definition",
    "map_metric_series",
    "scope_citation",
]


def _cite(label: str, locator: dict[str, Any]) -> Citation:
    return Citation(
        source_type=SourceType.CLOUDWATCH,
        label=label[:512],
        uri=None,
        locator=locator,
        retrieved_at=datetime.now(UTC),
    )


def iso(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value else None


def epoch_ms_to_iso(value: int | None) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value / 1000, UTC).isoformat()


def dimensions_out(dimensions: list[dict[str, str]] | None) -> list[dict[str, str]]:
    """AWS `[{Name, Value}]` -> contract `[{name, value}]`."""
    return [{"name": d["Name"], "value": d["Value"]} for d in dimensions or []]


def dimensions_in(dimensions: list[dict[str, str]]) -> list[dict[str, str]]:
    """Contract `[{name, value}]` -> AWS `[{Name, Value}]`."""
    return [{"Name": d["name"], "Value": d["value"]} for d in dimensions]


def _window(start: datetime, end: datetime) -> str:
    return f"{start.astimezone(UTC):%H:%M}–{end.astimezone(UTC):%H:%MZ}"


def map_log_group(raw: dict[str, Any], *, citation_ref: int) -> tuple[dict[str, Any], Citation]:
    name = str(raw["logGroupName"])
    item = {
        "log_group_name": name,
        "arn": raw.get("arn"),
        "created_at": epoch_ms_to_iso(raw.get("creationTime")),
        "retention_in_days": raw.get("retentionInDays"),
        "stored_bytes": raw.get("storedBytes"),
        "citation_ref": citation_ref,
    }
    return item, _cite(f"log group {name}", {"log_group": name})


def map_log_event(
    raw: dict[str, Any], *, log_group: str, message: str, truncated: bool, citation_ref: int
) -> tuple[dict[str, Any], Citation]:
    timestamp = epoch_ms_to_iso(raw["timestamp"]) or ""
    stream = raw.get("logStreamName")
    item = {
        "log_group_name": log_group,
        "log_stream_name": stream,
        "timestamp": timestamp,
        "ingested_at": epoch_ms_to_iso(raw.get("ingestionTime")),
        "event_id": raw.get("eventId"),
        "message": message,
        "truncated": truncated,
        "citation_ref": citation_ref,
    }
    return item, _cite(
        f"{log_group} @ {timestamp}",
        {"log_group": log_group, "log_stream": stream, "timestamp": timestamp},
    )


def map_insights_row(values: dict[str, Any], *, citation_ref: int = 0) -> dict[str, Any]:
    return {"values": values, "citation_ref": citation_ref}


def scope_citation(
    label: str, locator: dict[str, Any], *, start: datetime, end: datetime
) -> Citation:
    """One citation covering a whole result (Insights query, metric window, ...)."""
    return _cite(f"{label} {_window(start, end)}", locator)


def map_metric_definition(
    raw: dict[str, Any], *, citation_ref: int
) -> tuple[dict[str, Any], Citation]:
    namespace, name = str(raw["Namespace"]), str(raw["MetricName"])
    dims = dimensions_out(raw.get("Dimensions"))
    item = {"namespace": namespace, "metric_name": name, "dimensions": dims,
            "citation_ref": citation_ref}  # fmt: skip
    suffix = f" ({', '.join(f'{d["name"]}={d["value"]}' for d in dims)})" if dims else ""
    return item, _cite(f"{namespace} {name}{suffix}", {"namespace": namespace, "metric_name": name})


def map_metric_series(
    *,
    namespace: str,
    metric_name: str,
    dimensions: list[dict[str, str]],
    stat: str,
    period_s: int,
    datapoints: list[dict[str, Any]],
    start: datetime,
    end: datetime,
) -> tuple[dict[str, Any], Citation]:
    item = {
        "namespace": namespace,
        "metric_name": metric_name,
        "dimensions": dimensions,
        "stat": stat,
        "period_s": period_s,
        "unit": None,
        "datapoints": datapoints,
        "citation_ref": 0,
    }
    return item, scope_citation(
        f"{namespace} {metric_name} {stat}",
        {
            "namespace": namespace,
            "metric_name": metric_name,
            "dimensions": dimensions,
            "stat": stat,
            "time_from": iso(start),
            "time_to": iso(end),
        },
        start=start,
        end=end,
    )


def map_alarm(
    raw: dict[str, Any], *, state_reason: str | None, citation_ref: int
) -> tuple[dict[str, Any], Citation]:
    name, state = str(raw["AlarmName"]), str(raw.get("StateValue") or "INSUFFICIENT_DATA")
    updated = raw.get("StateUpdatedTimestamp")
    item = {
        "alarm_name": name,
        "arn": raw.get("AlarmArn"),
        "state_value": state,
        "state_reason": state_reason,
        "state_updated_at": iso(updated) if updated else None,
        "namespace": raw.get("Namespace"),
        "metric_name": raw.get("MetricName"),
        "dimensions": dimensions_out(raw.get("Dimensions")),
        "comparison_operator": raw.get("ComparisonOperator"),
        "threshold": raw.get("Threshold"),
        "period_s": raw.get("Period"),
        "evaluation_periods": raw.get("EvaluationPeriods"),
        "actions_enabled": raw.get("ActionsEnabled"),
        "citation_ref": citation_ref,
    }
    at = f" @ {updated.astimezone(UTC):%H:%MZ}" if updated else ""
    return item, _cite(f"alarm {name} ({state}{at})", {"alarm_name": name})


def _states(history_data: str | None) -> tuple[str | None, str | None]:
    try:
        data = json.loads(history_data or "null")
    except ValueError:
        return None, None
    if not isinstance(data, dict):
        return None, None
    old = (data.get("oldState") or {}).get("stateValue")
    new = (data.get("newState") or {}).get("stateValue")
    return old, new


def map_alarm_history(
    raw: dict[str, Any], *, summary: str | None, citation_ref: int = 0
) -> dict[str, Any]:
    old, new = _states(raw.get("HistoryData"))
    return {
        "alarm_name": raw["AlarmName"],
        "timestamp": iso(raw["Timestamp"]),
        "history_item_type": raw["HistoryItemType"],
        "history_summary": summary,
        "old_state": old,
        "new_state": new,
        "citation_ref": citation_ref,
    }
