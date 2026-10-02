#!/usr/bin/env python3
"""CHG-003 Step 2 — READ-ONLY Confluence space discovery over the real tenant.

Runs the already-shipped read-only `confluence_list_spaces` path (GET /rest/api/space)
through the egress-guarded httpx client (enforce_egress=True → only *.atlassian.net is
dialled; any other host is refused before dial). It performs NO write and NO ingest; it
only lists the spaces the configured account can see so the CEO can choose the ingest
scope. Prints space KEY + NAME + TYPE only — never any secret. Token value never printed.

Usage (with credentials/.ingest-sources.env sourced into the environment):
    uv run python docs/.../evidence/deploy-prod/chg003_space_discovery.py
"""

from __future__ import annotations

import asyncio
import sys

from mcp_common.config import CommonSettings, load_settings

from mcp_confluence.client import SOURCE, ConfluenceClient
from mcp_confluence.read_api import ConfluenceReadApi
from mcp_confluence.settings import Settings


async def _run() -> int:
    settings = load_settings(Settings, source=SOURCE)
    common = CommonSettings()
    # enforce_egress=True: the single egress choke point is armed; only the configured
    # allow-list host (*.atlassian.net) may be dialled.
    client = ConfluenceClient(settings, common=common, enforce_egress=True)
    api = ConfluenceReadApi(client, settings, common)
    seen: list[dict[str, str]] = []
    try:
        cursor: str | None = None
        pages = 0
        while True:
            outcome = await api.list_spaces(limit=100, cursor=cursor)
            result = outcome.result
            items = getattr(result, "items", None)
            if items is None and isinstance(result, dict):
                items = result.get("items", [])
            for it in items or []:
                get = it.get if isinstance(it, dict) else (lambda k, d=None: getattr(it, k, d))
                seen.append(
                    {
                        "key": str(get("key", "")),
                        "name": str(get("name", "")),
                        "type": str(get("type", "") or ""),
                    }
                )
            next_cursor = getattr(result, "next_cursor", None)
            if next_cursor is None and isinstance(result, dict):
                next_cursor = result.get("next_cursor")
            pages += 1
            if not next_cursor or pages >= 20:
                break
            cursor = next_cursor
    finally:
        await client.aclose()

    seen.sort(key=lambda r: r["key"])
    print(f"spaces_discovered: {len(seen)}")
    print("key | name | type")
    print("--- | ---- | ----")
    for r in seen:
        print(f"{r['key']} | {r['name']} | {r['type']}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_run()))
