"""T-061 / TC-035, TC-036, TC-038: the real boto3 wire protocol against an in-process AWS
emulator (moto server mode). It is a stand-in for the LocalStack container of
infra/docker-compose.yml, which cannot run here (no Docker daemon); the same flows against
LocalStack live in `test_integration.py` (marker `live`).

Message counts on every queue are compared before and after the whole tool sweep: no tool may
receive, send or delete anything (FR-010/AC-003).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import boto3
import pytest
from mcp_common.config import CommonSettings
from mcp_common.contract_testing import load_contract, validate_structured_content
from mcp_sqs_sns.client import SqsSnsClient
from mcp_sqs_sns.read_api import SqsSnsReadApi
from mcp_sqs_sns.server import build_server
from mcp_sqs_sns.settings import Settings
from pydantic import SecretStr

REGION = "ap-southeast-1"
CONTRACT = load_contract()


@pytest.fixture(scope="module")
def endpoint() -> Iterator[str]:
    pytest.importorskip("moto")
    from moto.server import ThreadedMotoServer

    server = ThreadedMotoServer(ip_address="127.0.0.1", port=0, verbose=False)
    server.start()
    host, port = server.get_host_and_port()
    try:
        yield f"http://{host}:{port}"
    finally:
        server.stop()


def _admin(service: str, endpoint: str) -> Any:
    return boto3.client(
        service, region_name=REGION, endpoint_url=endpoint,
        aws_access_key_id="testing", aws_secret_access_key="testing",  # noqa: S106
    )  # fmt: skip


@pytest.fixture(scope="module")
def seeded(endpoint: str) -> dict[str, str]:
    sqs, sns = _admin("sqs", endpoint), _admin("sns", endpoint)
    dlq_url = sqs.create_queue(QueueName="payment-events-dlq")["QueueUrl"]
    dlq_arn = sqs.get_queue_attributes(QueueUrl=dlq_url, AttributeNames=["QueueArn"])["Attributes"][
        "QueueArn"
    ]
    main_url = sqs.create_queue(
        QueueName="payment-events",
        Attributes={
            "RedrivePolicy": f'{{"deadLetterTargetArn":"{dlq_arn}","maxReceiveCount":"5"}}',
            "VisibilityTimeout": "30",
        },
        tags={"team": "payment"},
    )["QueueUrl"]
    sqs.create_queue(QueueName="orders")
    for i in range(3):
        sqs.send_message(QueueUrl=main_url, MessageBody=f"payload {i}")
    topic_arn = sns.create_topic(Name="payment-events")["TopicArn"]
    sns.create_topic(Name="orders-topic")
    main_arn = sqs.get_queue_attributes(QueueUrl=main_url, AttributeNames=["QueueArn"])[
        "Attributes"
    ]["QueueArn"]
    sns.subscribe(TopicArn=topic_arn, Protocol="sqs", Endpoint=main_arn)
    return {"main_url": main_url, "dlq_url": dlq_url, "topic_arn": topic_arn, "main_arn": main_arn}


def _counts(endpoint: str) -> dict[str, tuple[str, str, str]]:
    sqs = _admin("sqs", endpoint)
    out = {}
    for url in sqs.list_queues().get("QueueUrls", []):
        attrs = sqs.get_queue_attributes(QueueUrl=url, AttributeNames=["All"])["Attributes"]
        out[url] = (
            attrs["ApproximateNumberOfMessages"],
            attrs["ApproximateNumberOfMessagesNotVisible"],
            attrs["ApproximateNumberOfMessagesDelayed"],
        )
    return out


@pytest.fixture
def api(endpoint: str, seeded: dict[str, str]) -> SqsSnsReadApi:
    settings = Settings(
        region=REGION, endpoint_url=endpoint,
        aws_access_key_id=SecretStr("testing"), aws_secret_access_key=SecretStr("testing"),
    )  # fmt: skip
    common = CommonSettings()
    return SqsSnsReadApi(SqsSnsClient(settings, common=common), common, settings)


@pytest.mark.asyncio
async def test_TC_035_queue_and_topic_attributes_over_the_wire(
    api: SqsSnsReadApi, seeded: dict[str, str]
) -> None:
    queue = (await api.get_queue_attributes(queue_name="payment-events", include_tags=True)).result
    item = queue.items[0]
    assert queue.status.value == "ok"
    assert item["approximate_number_of_messages"] == 3 and item["arn"] == seeded["main_arn"]
    assert item["redrive_policy"]["maxReceiveCount"] == 5
    assert item["redrive_policy"]["deadLetterTargetArn"].endswith("payment-events-dlq")
    assert item["tags"] == {"team": "payment"} and item["visibility_timeout_s"] == 30
    assert queue.citations[0].locator == {"queue_arn": seeded["main_arn"]}
    topic = (await api.get_topic_attributes(topic_arn=seeded["topic_arn"])).result
    assert topic.status.value == "ok" and topic.items[0]["name"] == "payment-events"
    attrs = topic.items[0]
    # moto does not keep the subscription counters of real SNS; only the shape is checked here
    # (the subscription itself is asserted through sns_list_subscriptions_by_topic below).
    assert all(
        isinstance(attrs[k], int) and attrs[k] >= 0
        for k in ("subscriptions_confirmed", "subscriptions_pending", "subscriptions_deleted")
    )


@pytest.mark.asyncio
async def test_list_tools_over_the_wire(api: SqsSnsReadApi, seeded: dict[str, str]) -> None:
    queues = (await api.list_queues(name_prefix="payment")).result
    assert {i["queue_name"] for i in queues.items} == {"payment-events", "payment-events-dlq"}
    assert all(i["arn"] and i["arn"].endswith(i["queue_name"]) for i in queues.items)
    sources = (await api.list_dead_letter_source_queues(queue_name="payment-events-dlq")).result
    assert [i["queue_name"] for i in sources.items] == ["payment-events"]
    nothing = await api.list_dead_letter_source_queues(queue_name="orders")
    assert nothing.result.status.value == "empty"
    topics = (await api.list_topics(name_prefix="payment")).result
    assert [i["topic_arn"] for i in topics.items] == [seeded["topic_arn"]]
    subs = (await api.list_subscriptions_by_topic(topic_arn=seeded["topic_arn"])).result
    assert subs.items[0]["protocol"] == "sqs" and subs.items[0]["endpoint"] == seeded["main_arn"]


@pytest.mark.asyncio
async def test_TC_036_missing_queue_and_topic_are_not_found_over_the_wire(
    api: SqsSnsReadApi, seeded: dict[str, str]
) -> None:
    assert (
        await api.get_queue_attributes(queue_name="does-not-exist")
    ).result.status.value == "not_found"
    ghost_topic = seeded["topic_arn"].rsplit(":", 1)[0] + ":no-such-topic"
    assert (
        await api.get_topic_attributes(topic_arn=ghost_topic)
    ).result.status.value == "not_found"
    assert (
        await api.list_subscriptions_by_topic(topic_arn=ghost_topic)
    ).result.status.value == "not_found"


@pytest.mark.asyncio
async def test_pagination_over_the_wire_resumes_exactly(api: SqsSnsReadApi) -> None:
    seen: list[str] = []
    cursor: str | None = None
    for _ in range(5):
        page = (await api.list_queues(limit=1, cursor=cursor)).result
        seen.extend(i["queue_name"] for i in page.items)
        cursor = page.meta.next_cursor
        if not cursor:
            break
    assert sorted(seen) == ["orders", "payment-events", "payment-events-dlq"]
    assert len(seen) == len(set(seen))


@pytest.mark.asyncio
async def test_TC_038_full_tool_sweep_leaves_every_message_count_unchanged(
    endpoint: str, seeded: dict[str, str], api: SqsSnsReadApi
) -> None:
    before = _counts(endpoint)
    assert before[seeded["main_url"]][0] == "3"  # the seed is really there
    server = build_server(api)
    from mcp.shared.memory import create_connected_server_and_client_session

    calls = [
        ("sqs_list_queues", {}),
        ("sqs_get_queue_attributes", {"queue_name": "payment-events", "include_tags": True}),
        ("sqs_list_dead_letter_source_queues", {"queue_name": "payment-events-dlq"}),
        ("sns_list_topics", {}),
        ("sns_get_topic_attributes", {"topic_arn": seeded["topic_arn"]}),
        ("sns_list_subscriptions_by_topic", {"topic_arn": seeded["topic_arn"]}),
    ]
    async with create_connected_server_and_client_session(server) as session:
        for _ in range(3):
            for name, args in calls:
                result = await session.call_tool(name, arguments=args)
                assert not result.isError, (name, result.content)
                validate_structured_content(CONTRACT, name, result.structuredContent)
    after = _counts(endpoint)
    assert after == before  # no message was received (not-visible stays 0), sent or deleted


@pytest.mark.asyncio
async def test_startup_check_runs_against_the_emulator(
    endpoint: str, seeded: dict[str, str]
) -> None:
    settings = Settings(
        region=REGION, endpoint_url=endpoint,
        aws_access_key_id=SecretStr("testing"), aws_secret_access_key=SecretStr("testing"),
    )  # fmt: skip
    report = await SqsSnsClient(settings).verify_credentials()
    assert report.account == "123456789012"
    assert report.ok  # moto cannot simulate IAM policies: a warning, never a silent pass
