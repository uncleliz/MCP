"""Live integration tests against real AWS CloudWatch.

Skipped by default (see packages/conftest.py): they need real AWS credentials with a read-only
IAM policy. Run manually with MCP_LIVE_TESTS=1 plus MCP_CLOUDWATCH_REGION (and credentials via
MCP_CLOUDWATCH_AWS_* or the default chain). Never calls a write API.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from mcp_cloudwatch.client import CloudWatchClient
from mcp_cloudwatch.read_api import CloudWatchReadApi
from mcp_cloudwatch.settings import Settings
from mcp_common.config import CommonSettings, load_settings

pytestmark = pytest.mark.live


@pytest.mark.asyncio
async def test_live_startup_check_log_groups_and_alarms() -> None:
    settings = load_settings(Settings, source="cloudwatch")
    common = CommonSettings()
    client = CloudWatchClient(settings, common=common)
    try:
        report = await client.verify_credentials()
        assert report.ok, report.reasons
        api = CloudWatchReadApi(client, common, settings)
        groups = await api.list_log_groups(limit=1)
        assert groups.result.status.value in {"ok", "empty"}
        alarms = await api.describe_alarms(limit=1)
        assert alarms.result.status.value in {"ok", "empty"}
        now = datetime.now(UTC)
        history = await api.describe_alarm_history(
            alarm_name="does-not-exist", time_from=now - timedelta(hours=1), time_to=now
        )
        assert history.result.status.value == "not_found"
    finally:
        await client.aclose()
