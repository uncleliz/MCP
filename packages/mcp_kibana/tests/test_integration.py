"""Live integration tests against a real Kibana.

Skipped by default (see packages/conftest.py): they need a real Kibana reachable over VPN and
a read-only account. Run manually with MCP_LIVE_TESTS=1 plus
MCP_KIBANA_{BASE_URL,USERNAME,PASSWORD}.
"""

from __future__ import annotations

import pytest
from mcp_common.config import CommonSettings, load_settings
from mcp_kibana.client import KibanaClient
from mcp_kibana.read_api import KibanaReadApi
from mcp_kibana.settings import Settings

pytestmark = pytest.mark.live


@pytest.mark.asyncio
async def test_live_startup_check_and_dashboard_search() -> None:
    settings = load_settings(Settings, source="kibana")
    common = CommonSettings()
    client = KibanaClient(settings, common=common)
    try:
        report = await client.verify_credentials()
        assert report.ok, report.reasons
        outcome = await KibanaReadApi(client, common).find_saved_objects(query="a", limit=1)
        assert outcome.result.status.value in {"ok", "empty"}
    finally:
        await client.aclose()
