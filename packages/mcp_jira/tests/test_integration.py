"""T-094: live Jira integration (marker `live`, skipped unless MCP_LIVE_TESTS=1).

Runs once per flavor against a real tenant to confirm the hand-written fixture shapes match the
real Cloud (`/rest/api/3`) and Server/DC (`/rest/api/2`) payloads (ADR-0019 Risks; spike S1).
Needs `MCP_JIRA_BASE_URL`, `MCP_JIRA_TOKEN` (+ `MCP_JIRA_EMAIL` on Cloud) and VPN.
"""

from __future__ import annotations

import os

import pytest
from mcp_common.config import CommonSettings, load_settings
from mcp_jira.client import SOURCE, JiraClient
from mcp_jira.settings import Settings


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_startup_check_and_search() -> None:
    if not os.environ.get("MCP_JIRA_BASE_URL"):
        pytest.skip("MCP_JIRA_BASE_URL not set")
    settings = load_settings(Settings, source=SOURCE)
    client = JiraClient(settings, common=CommonSettings())
    try:
        report = await client.verify_credentials()
        assert report.ok, report.reasons
        page = await client.search_issues("order by created DESC", max_results=1)
        assert isinstance(page.values, list)
    finally:
        await client.aclose()
