"""Live integration tests against a real Confluence Cloud tenant.

Skipped by default (see packages/conftest.py): they need real credentials and network
reach. Run manually with MCP_LIVE_TESTS=1 plus MCP_CONFLUENCE_{BASE_URL,EMAIL,API_TOKEN}.
"""

from __future__ import annotations

import pytest
from mcp_common.config import CommonSettings, load_settings
from mcp_confluence.client import ConfluenceClient
from mcp_confluence.read_api import ConfluenceReadApi
from mcp_confluence.settings import Settings

pytestmark = pytest.mark.live


@pytest.mark.asyncio
async def test_live_startup_check_and_search() -> None:
    settings = load_settings(Settings, source="confluence")
    common = CommonSettings()
    client = ConfluenceClient(settings, common=common)
    try:
        report = await client.verify_credentials()
        assert report.ok, report.reasons
        outcome = await ConfluenceReadApi(client, settings, common).search_pages(query="a", limit=1)
        assert outcome.result.status.value in {"ok", "empty"}
    finally:
        await client.aclose()
