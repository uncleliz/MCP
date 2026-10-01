"""OpenSearch payloads -> contract item schemas + `Citation` (FR-004/AC-001, FR-015).

Pure functions, no I/O. OpenSearch has no web URL, so `Citation.uri` is `None` and the
`locator` carries the identifier (`{index, doc_id, timestamp}`, contract invariant 6).
Free-text fields arrive already redacted/wrapped/budgeted from `read_api.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from mcp_common.envelope import Citation, SourceType

__all__ = [
    "flatten_mapping",
    "iso_or_none",
    "map_bucket",
    "map_count",
    "map_document",
    "map_index",
    "map_mapping",
    "scope_citation",
]


def _cite(label: str, locator: dict[str, Any]) -> Citation:
    return Citation(
        source_type=SourceType.OPENSEARCH,
        label=label[:512],
        uri=None,
        locator=locator,
        retrieved_at=datetime.now(UTC),
    )


def iso_or_none(value: Any) -> str | None:
    """ISO-8601 (tz-aware) string of a timestamp (ISO text or epoch millis), else None."""
    if value is None or isinstance(value, bool):
        return None
    try:
        if isinstance(value, int | float):
            return datetime.fromtimestamp(value / 1000, UTC).isoformat()
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, OverflowError, OSError):
        return None
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)).isoformat()


def _int_or_none(value: Any) -> int | None:
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


def map_index(raw: dict[str, Any], *, citation_ref: int) -> tuple[dict[str, Any], Citation]:
    name = str(raw["index"])
    item = {
        "index": name,
        "health": raw.get("health"),
        "status": raw.get("status"),
        "docs_count": _int_or_none(raw.get("docs.count")),
        "store_size": raw.get("store.size"),
        "creation_date": raw.get("creation.date.string"),
        "citation_ref": citation_ref,
    }
    return item, _cite(f"index {name}", {"index": name})


def _walk_properties(properties: dict[str, Any], prefix: str, out: dict[str, str]) -> None:
    for name, spec in properties.items():
        path = f"{prefix}{name}"
        spec = spec or {}
        nested = spec.get("properties")
        if nested:
            if spec.get("type") == "nested":
                out[path] = "nested"
            _walk_properties(nested, f"{path}.", out)
            continue
        out[path] = str(spec.get("type") or "unknown")
        for sub, sub_spec in (spec.get("fields") or {}).items():
            out[f"{path}.{sub}"] = str((sub_spec or {}).get("type") or "unknown")


def flatten_mapping(response: dict[str, Any], field_filter: str | None) -> dict[str, str]:
    """`{index: {mappings: {properties}}}` (one or many indices) -> `{field path: type}`."""
    fields: dict[str, str] = {}
    for mapping in response.values():
        _walk_properties(
            ((mapping or {}).get("mappings") or {}).get("properties") or {}, "", fields
        )
    if field_filter:
        fields = {k: v for k, v in fields.items() if k.startswith(field_filter)}
    return dict(sorted(fields.items()))


def map_mapping(index: str, fields: dict[str, str]) -> tuple[dict[str, Any], Citation]:
    item = {"index": index, "fields": fields, "field_count": len(fields), "citation_ref": 0}
    return item, _cite(f"mapping {index}", {"index": index})


def map_document(
    hit: dict[str, Any],
    *,
    source: dict[str, Any],
    timestamp: str | None,
    highlights: dict[str, list[str]] | None,
    citation_ref: int,
) -> tuple[dict[str, Any], Citation]:
    index, doc_id = str(hit["_index"]), str(hit["_id"])
    item = {
        "index": index,
        "id": doc_id,
        "score": hit.get("_score"),
        "timestamp": timestamp,
        "source": source,
        "highlights": highlights,
        "citation_ref": citation_ref,
    }
    locator: dict[str, Any] = {"index": index, "doc_id": doc_id, "timestamp": timestamp}
    label = f"{index}/{doc_id}" + (f" @ {timestamp}" if timestamp else "")
    return item, _cite(label, locator)


def map_count(
    index_pattern: str, count: int, time_from: datetime, time_to: datetime, query: str
) -> tuple[dict[str, Any], Citation]:
    item = {
        "index_pattern": index_pattern,
        "count": count,
        "time_from": time_from.astimezone(UTC).isoformat(),
        "time_to": time_to.astimezone(UTC).isoformat(),
        "citation_ref": 0,
    }
    window = f"{time_from.astimezone(UTC):%H:%M}–{time_to.astimezone(UTC):%H:%MZ}"
    return item, _cite(
        f"count {index_pattern} ({query}, {window})",
        {
            "index": index_pattern,
            "query": query,
            "time_from": item["time_from"],
            "time_to": item["time_to"],
        },
    )


def map_bucket(bucket: dict[str, Any], *, date_histogram: bool) -> dict[str, Any]:
    if date_histogram:
        key = bucket.get("key_as_string") or iso_or_none(bucket.get("key")) or str(bucket["key"])
    else:
        key = str(bucket.get("key_as_string") or bucket["key"])
    return {"key": key, "doc_count": int(bucket.get("doc_count") or 0), "citation_ref": 0}


def scope_citation(label: str, locator: dict[str, Any]) -> Citation:
    """One citation covering a whole aggregation (index + field + window)."""
    return _cite(label, locator)
