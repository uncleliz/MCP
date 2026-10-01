"""Constants and payloads shared by the mcp-kibana tests.

The payloads are hand-written to the shape of the Kibana saved-objects HTTP API docs (no live
Kibana is reachable from this container); they have NOT been captured from a real instance.
`test_integration.py` (marker `live`) covers the real thing.
"""

from __future__ import annotations

from typing import Any

import httpx

BASE = "https://kibana.example.test:5601"
API = f"{BASE}/api"
DASH_ID = "8f3c1a20-1111-4aaa-bbbb-000000000001"

DASHBOARD: dict[str, Any] = {
    "type": "dashboard",
    "id": DASH_ID,
    "updated_at": "2026-09-12T02:00:00.000Z",
    "namespaces": ["default"],
    "attributes": {
        "title": "Payment Service Overview",
        "description": "Latency, error rate, queue depth. password=hunter2hunter2",
        "panelsJSON": '[{"panelIndex":"1"},{"panelIndex":"2"},{"panelIndex":"3"}]',
    },
    "references": [
        {"name": "kibanaSavedObjectMeta.searchSourceJSON.index", "type": "index-pattern",
         "id": "idx-app-logs"},
        {"name": "panel_1", "type": "lens", "id": "vis-error-rate"},
    ],
}  # fmt: skip

FIND_OK: dict[str, Any] = {
    "page": 1,
    "per_page": 20,
    "total": 1,
    "saved_objects": [
        {
            "type": "dashboard",
            "id": DASH_ID,
            "updated_at": "2026-09-12T02:00:00.000Z",
            "attributes": {"title": "Payment Service Overview", "description": "Latency"},
            "references": [{"name": "r", "type": "index-pattern", "id": "idx-app-logs"}],
        }
    ],
}
FIND_EMPTY: dict[str, Any] = {"page": 1, "per_page": 20, "total": 0, "saved_objects": []}
FIND_MORE: dict[str, Any] = {**FIND_OK, "per_page": 1, "total": 3}
STATUS_OK: dict[str, Any] = {
    "name": "kibana",
    "status": {"overall": {"level": "available", "summary": "All services are available"}},
}
NOT_FOUND_BODY: dict[str, Any] = {"statusCode": 404, "error": "Not Found", "message": "nope"}


def find_url(space: str | None = None) -> str:
    return f"{BASE}{f'/s/{space}' if space else ''}/api/saved_objects/_find"


def get_url(type_: str, id_: str, space: str | None = None) -> str:
    return f"{BASE}{f'/s/{space}' if space else ''}/api/saved_objects/{type_}/{id_}"


def ok(payload: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, json=payload)


OK_CALLS: list[tuple[str, dict[str, Any]]] = [
    ("kibana_find_saved_objects", {"query": "payment", "types": ["dashboard", "lens"]}),
    ("kibana_get_saved_object", {"type": "dashboard", "id": DASH_ID}),
    (
        "kibana_build_dashboard_link",
        {"dashboard_id": DASH_ID, "time_from": "2026-09-30T10:00:00Z",
         "time_to": "2026-09-30T12:00:00Z"},
    ),
]  # fmt: skip


def mock_ok(router: Any) -> None:
    router.get(find_url()).mock(return_value=ok(FIND_OK))
    router.get(get_url("dashboard", DASH_ID)).mock(return_value=ok(DASHBOARD))
