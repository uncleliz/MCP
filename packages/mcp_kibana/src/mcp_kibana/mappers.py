"""Kibana payloads -> contract item schemas + web `Citation` (FR-005, FR-015).

Pure functions, no I/O. Every URL here is a *Kibana web* link a human can open — never an API
URL. `dashboard_url` is a pure function of its inputs (no network).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

from mcp_common.envelope import Citation, SourceType

__all__ = [
    "TYPE_LABELS",
    "dashboard_url",
    "format_time",
    "map_dashboard_link",
    "map_saved_object",
    "object_url",
    "rison_quote",
]

TYPE_LABELS = {
    "dashboard": "Dashboard",
    "visualization": "Visualization",
    "lens": "Lens",
    "search": "Saved search",
    "index-pattern": "Index pattern",
}

_APP_PATHS = {
    "dashboard": "/app/dashboards#/view/{id}",
    "visualization": "/app/visualize#/edit/{id}",
    "lens": "/app/lens#/edit/{id}",
    "search": "/app/discover#/view/{id}",
    "index-pattern": "/app/management/kibana/indexPatterns/patterns/{id}",
}
_RISON_SAFE = "!:,'()*-._~"


def _space_prefix(space: str | None) -> str:
    return f"/s/{space}" if space else ""


def object_url(base_url: str, type_: str, id_: str, space: str | None) -> str:
    path = _APP_PATHS[type_].format(id=quote(id_, safe=""))
    return f"{base_url}{_space_prefix(space)}{path}"


def format_time(value: datetime) -> str:
    """UTC ISO-8601 with a `Z` suffix; milliseconds only when the input has them."""
    utc = value.astimezone(UTC)
    if utc.microsecond:
        return utc.strftime("%Y-%m-%dT%H:%M:%S.") + f"{utc.microsecond // 1000:03d}Z"
    return utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def rison_quote(text: str) -> str:
    """Escape a string for a Rison `'...'` literal (`!` and `'` are escaped with `!`)."""
    return text.replace("!", "!!").replace("'", "!'")


def dashboard_url(
    base_url: str,
    dashboard_id: str,
    space: str | None,
    time_from: datetime,
    time_to: datetime,
    query: str | None,
) -> str:
    """`.../app/dashboards#/view/<id>?_g=(time:(from:'..',to:'..'))[&_a=(query:(...))]`."""
    url = object_url(base_url, "dashboard", dashboard_id, space)
    url += f"?_g=(time:(from:'{format_time(time_from)}',to:'{format_time(time_to)}'))"
    if query:
        rison = f"(query:(language:kuery,query:'{rison_quote(query)}'))"
        url += f"&_a={quote(rison, safe=_RISON_SAFE)}"
    return url


def _cite(label: str, uri: str, locator: dict[str, Any]) -> Citation:
    return Citation(
        source_type=SourceType.KIBANA,
        label=label[:512] or uri,
        uri=uri,
        locator=locator,
        retrieved_at=datetime.now(UTC),
    )


def _panel_count(raw: dict[str, Any]) -> int | None:
    if raw.get("type") != "dashboard":
        return None
    try:
        panels = json.loads((raw.get("attributes") or {}).get("panelsJSON") or "null")
    except ValueError:
        return None
    return len(panels) if isinstance(panels, list) else None


def map_saved_object(
    raw: dict[str, Any],
    *,
    base_url: str,
    space: str | None,
    title: str,
    description: str | None,
    citation_ref: int,
) -> tuple[dict[str, Any], Citation]:
    type_, id_ = str(raw["type"]), str(raw["id"])
    url = object_url(base_url, type_, id_, space)
    item = {
        "id": id_,
        "type": type_,
        "title": title,
        "description": description,
        "updated_at": raw.get("updated_at"),
        "space": space or "default",
        "url": url,
        "panel_count": _panel_count(raw),
        "references": [
            {"id": str(ref["id"]), "type": str(ref["type"]), "name": ref.get("name")}
            for ref in raw.get("references") or []
        ],
        "citation_ref": citation_ref,
    }
    label = f"{TYPE_LABELS.get(type_, type_)}: {title}"
    return item, _cite(label, url, {"type": type_, "id": id_})


def map_dashboard_link(
    *,
    dashboard_id: str,
    title: str | None,
    url: str,
    time_from: datetime,
    time_to: datetime,
    query: str | None,
) -> tuple[dict[str, Any], Citation]:
    item = {
        "id": dashboard_id,
        "title": title,
        "url": url,
        "time_from": time_from.astimezone(UTC).isoformat(),
        "time_to": time_to.astimezone(UTC).isoformat(),
        "query": query,
        "citation_ref": 0,
    }
    window = f"{format_time(time_from)}–{format_time(time_to)}"
    return item, _cite(
        f"{title or dashboard_id} ({window})",
        url,
        {
            "type": "dashboard",
            "id": dashboard_id,
            "time_from": format_time(time_from),
            "time_to": format_time(time_to),
        },
    )
