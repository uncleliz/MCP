"""Live integration tests against LocalStack (infra/docker-compose.yml) or a real account.

Skipped by default (see packages/conftest.py): they need a running Docker daemon. Run with
`docker compose -f infra/docker-compose.yml up -d localstack`, then
`MCP_LIVE_TESTS=1 MCP_SQS_SNS_REGION=ap-southeast-1 MCP_SQS_SNS_ENDPOINT_URL=http://localhost:4566
 MCP_SQS_SNS_AWS_ACCESS_KEY_ID=test MCP_SQS_SNS_AWS_SECRET_ACCESS_KEY=test uv run pytest -m live`.
Seed queue/topic: `mcp-dev-queue` / `mcp-dev-topic` (infra/localstack/init.sh).
Never calls a write API; asserts the message count of the dev queue is unchanged.
"""

from __future__ import annotations

import boto3
import pytest
from mcp_common.config import CommonSettings, load_settings
from mcp_sqs_sns.client import SqsSnsClient
from mcp_sqs_sns.read_api import SqsSnsReadApi
from mcp_sqs_sns.settings import Settings

pytestmark = pytest.mark.live


@pytest.mark.asyncio
async def test_live_metadata_reads_leave_message_counts_unchanged() -> None:
    settings = load_settings(Settings, source="sqs-sns")
    common = CommonSettings()
    client = SqsSnsClient(settings, common=common)
    admin = boto3.client(
        "sqs", region_name=settings.region, endpoint_url=settings.endpoint_url,
        aws_access_key_id="test", aws_secret_access_key="test",  # noqa: S106
    )  # fmt: skip
    url = admin.get_queue_url(QueueName="mcp-dev-queue")["QueueUrl"]

    def counts() -> dict[str, str]:
        attrs = admin.get_queue_attributes(QueueUrl=url, AttributeNames=["All"])["Attributes"]
        return {k: v for k, v in attrs.items() if k.startswith("ApproximateNumberOfMessages")}

    before = counts()
    try:
        api = SqsSnsReadApi(client, common, settings)
        queue = await api.get_queue_attributes(queue_name="mcp-dev-queue")
        assert queue.result.status.value == "ok"
        topics = await api.list_topics(name_prefix="mcp-dev")
        assert topics.result.status.value == "ok"
        missing = await api.get_queue_attributes(queue_name="does-not-exist")
        assert missing.result.status.value == "not_found"
    finally:
        await client.aclose()
    assert counts() == before
