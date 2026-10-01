"""Live integration tests against a real OpenSearch cluster.

Skipped by default (see packages/conftest.py): they need a real cluster reachable over VPN and
a read-only account. Run manually with MCP_LIVE_TESTS=1 plus
MCP_OPENSEARCH_{HOSTS,USERNAME,PASSWORD}. Deliberately never calls a write API.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from mcp_common.config import CommonSettings, load_settings
from mcp_opensearch.client import OpenSearchClient
from mcp_opensearch.read_api import OpenSearchReadApi
from mcp_opensearch.settings import Settings

pytestmark = pytest.mark.live


@pytest.mark.asyncio
async def test_live_startup_check_indices_and_search() -> None:
    settings = load_settings(Settings, source="opensearch")
    common = CommonSettings()
    client = OpenSearchClient(settings, common=common)
    try:
        report = await client.verify_credentials()
        assert report.ok, report.reasons
        api = OpenSearchReadApi(client, common)
        indices = await api.list_indices(limit=1)
        assert indices.result.status.value in {"ok", "partial", "empty"}
        now = datetime.now(UTC)
        outcome = await api.count(
            index_pattern="*", query="*", time_from=now - timedelta(hours=1), time_to=now
        )
        assert outcome.result.status.value in {"ok", "empty"}
    finally:
        await client.aclose()
