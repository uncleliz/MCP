"""Live integration tests against the Kafka KRaft broker(s) of infra/docker-compose.yml.

Skipped by default (see packages/conftest.py): the Docker daemon is not available in CI or the
dev container, so these have NOT been run here. Run manually:

    docker compose -f infra/docker-compose.yml -f infra/docker-compose.kafka-autocreate.yml \\
        up -d kafka kafka-autocreate
    MCP_LIVE_TESTS=1 uv run pytest packages/mcp_kafka -m live

* `kafka` (localhost:9092) has auto-create OFF — the production-like broker.
* `kafka-autocreate` (localhost:9094) has `auto.create.topics.enable=true`, the setting under
  which a per-topic metadata request would silently create a topic (R17, ADR-0009 A1).

Seeding (topics/messages/group commits) uses a separate confluent-kafka admin/producer inside
this test module only — never through mcp-kafka, which has no write path.
"""

from __future__ import annotations

import os
import time
import uuid

import pytest
from confluent_kafka import Consumer, Producer
from confluent_kafka.admin import AdminClient, NewTopic
from mcp_common.config import CommonSettings
from mcp_kafka.client import ConfluentKafkaReader, KafkaClient
from mcp_kafka.read_api import KafkaReadApi
from mcp_kafka.settings import Settings

pytestmark = pytest.mark.live

STRICT = os.environ.get("MCP_KAFKA_IT_BOOTSTRAP", "localhost:9092")
AUTOCREATE = os.environ.get("MCP_KAFKA_IT_AUTOCREATE_BOOTSTRAP", "localhost:9094")


def _api(bootstrap: str) -> tuple[KafkaReadApi, KafkaClient]:
    settings = Settings(bootstrap_servers=bootstrap)
    client = KafkaClient(settings, common=CommonSettings(), reader=ConfluentKafkaReader(settings))
    return KafkaReadApi(client, CommonSettings()), client


def _seed(bootstrap: str, topic: str, count: int = 5) -> None:
    admin = AdminClient({"bootstrap.servers": bootstrap})
    for future in admin.create_topics([NewTopic(topic, num_partitions=2)]).values():
        future.result(timeout=30)
    producer = Producer({"bootstrap.servers": bootstrap})
    for i in range(count):
        producer.produce(topic, key=f"k{i}", value=f'{{"n": {i}}}', partition=i % 2)
    producer.flush(30)


def _topic_names(bootstrap: str) -> set[str]:
    return set(AdminClient({"bootstrap.servers": bootstrap}).list_topics(timeout=15).topics)


@pytest.mark.asyncio
async def test_live_describe_and_peek_cite_topic_partition_and_string_offset() -> None:
    topic = f"mcp-it-{uuid.uuid4().hex[:8]}"
    _seed(STRICT, topic)
    api, client = _api(STRICT)
    try:
        report = await client.verify_credentials()
        assert report.ok, report.reasons
        detail = (await api.describe_topic(topic=topic)).result
        assert detail.status.value == "ok" and detail.items[0]["approx_message_count"] == "5"
        peeked = (await api.peek_messages(topic=topic, from_="earliest", limit=5)).result
        assert len(peeked.items) == 5 and all(isinstance(i["offset"], str) for i in peeked.items)
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_live_R17_describing_a_missing_topic_on_an_autocreate_broker_creates_nothing() -> (
    None
):
    api, client = _api(AUTOCREATE)
    missing = f"mcp-it-missing-{uuid.uuid4().hex[:8]}"
    before = _topic_names(AUTOCREATE)
    try:
        for outcome in (
            await api.describe_topic(topic=missing),
            await api.peek_messages(topic=missing),
        ):
            assert outcome.result.status.value == "not_found"
        time.sleep(2)  # give a (wrongly) auto-created topic time to appear
        assert missing not in _topic_names(AUTOCREATE)
        assert _topic_names(AUTOCREATE) == before  # topic list identical before/after (TC-025)
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_live_peek_and_describe_group_do_not_move_another_groups_committed_offsets() -> None:
    topic, group = f"mcp-it-{uuid.uuid4().hex[:8]}", f"mcp-it-group-{uuid.uuid4().hex[:6]}"
    _seed(STRICT, topic, count=4)
    other = Consumer(
        {"bootstrap.servers": STRICT, "group.id": group, "enable.auto.commit": False,
         "auto.offset.reset": "earliest"}
    )  # fmt: skip
    other.subscribe([topic])
    taken = 0
    while taken < 2:
        message = other.poll(10)
        if message is not None and message.error() is None:
            taken += 1
    other.commit(asynchronous=False)  # the *other* consumer commits; mcp-kafka never does
    other.close()
    admin = AdminClient({"bootstrap.servers": STRICT})
    from confluent_kafka import ConsumerGroupTopicPartitions

    def committed() -> dict[tuple[str, int], int]:
        result = admin.list_consumer_group_offsets([ConsumerGroupTopicPartitions(group)])[group]
        return {(tp.topic, tp.partition): tp.offset for tp in result.result().topic_partitions}

    before = committed()
    api, client = _api(STRICT)
    try:
        await api.peek_messages(topic=topic, from_="earliest", limit=4)
        described = (await api.describe_consumer_group(group_id=group)).result
        assert described.status.value == "ok"
    finally:
        await client.aclose()
    assert committed() == before  # TC-027
