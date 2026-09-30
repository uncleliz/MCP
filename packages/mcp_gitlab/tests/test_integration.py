"""Live integration tests against a real GitLab instance.

Skipped by default (see packages/conftest.py): they need a real token and network reach.
Run manually with MCP_LIVE_TESTS=1 plus MCP_GITLAB_{BASE_URL,PRIVATE_TOKEN}.
"""

from __future__ import annotations

import pytest
from mcp_common.config import CommonSettings, load_settings
from mcp_gitlab.client import GitLabClient
from mcp_gitlab.read_api import GitLabReadApi
from mcp_gitlab.settings import Settings

pytestmark = pytest.mark.live


@pytest.mark.asyncio
async def test_live_startup_check_and_project_search() -> None:
    settings = load_settings(Settings, source="gitlab")
    common = CommonSettings()
    client = GitLabClient(settings, common=common)
    try:
        report = await client.verify_credentials()
        assert report.ok, report.reasons
        outcome = await GitLabReadApi(client, common).search_projects(query="a", limit=1)
        assert outcome.result.status.value in {"ok", "empty"}
    finally:
        await client.aclose()
