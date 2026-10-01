"""T-047: tool behaviour of mcp_kafka.read_api, identical for every `KafkaReader` adapter."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from confluent_kafka import KafkaError
from kafka_helpers import (
    TOPIC,
    FakeAdmin,
    FakeCluster,
    FakeConsumer,
    InMemoryReader,
    kafka_error,
    msg,
)
from mcp_common.config import CommonSettings
from mcp_common.errors import ErrorCode, ToolError
from mcp_common.runtime import BoundedExecutor
from mcp_common.testing import assert_envelope_invariants
from mcp_kafka.client import ConfluentKafkaReader, KafkaClient
from mcp_kafka.read_api import KafkaReadApi
from mcp_kafka.settings import Settings


@pytest.fixture(params=["confluent", "in-memory"])
def api(request, settings: Settings, common: CommonSettings, cluster: FakeCluster) -> KafkaReadApi:
    """TC-028: every test below runs once per adapter, without any change."""
    if request.param == "confluent":
        reader = ConfluentKafkaReader(
            settings,
            admin_factory=lambda conf: FakeAdmin(conf, cluster),
            consumer_factory=lambda conf: FakeConsumer(conf, cluster),
        )
    else:
        reader = InMemoryReader(cluster)
    client = KafkaClient(settings, common=common, reader=reader, executor=BoundedExecutor(4))
    return KafkaReadApi(client, common)


def _items(outcome) -> list[dict]:
    assert_envelope_invariants(outcome.result)
    return outcome.result.items


# -- kafka_list_topics ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_FR_007_AC_001_list_topics_pattern_and_citation(api: KafkaReadApi) -> None:
    outcome = await api.list_topics(pattern="payment.*")
    item = _items(outcome)[0]
    assert item["topic"] == TOPIC and item["partition_count"] == 2
    assert item["replication_factor"] == 3 and item["internal"] is False
    citation = outcome.result.citations[0]
    assert citation.locator == {"topic": TOPIC} and citation.uri is None


@pytest.mark.asyncio
async def test_list_topics_internal_limit_and_empty(
    api: KafkaReadApi, cluster: FakeCluster
) -> None:
    cluster.add_topic("__consumer_offsets", {0: []})
    visible = {i["topic"] for i in (await api.list_topics()).result.items}
    assert "__consumer_offsets" not in visible
    everything = {i["topic"] for i in (await api.list_topics(include_internal=True)).result.items}
    assert "__consumer_offsets" in everything
    limited = await api.list_topics(limit=1)
    assert len(limited.result.items) == 1 and limited.result.meta.truncated
    empty = await api.list_topics(pattern="zzz.*")
    assert empty.result.status.value == "empty" and empty.result.citations == []


@pytest.mark.parametrize(
    ("kwargs", "field"), [({"limit": 0}, "limit"), ({"pattern": "x" * 257}, "pattern")]
)
@pytest.mark.asyncio
async def test_list_topics_invalid_input(api: KafkaReadApi, kwargs: dict, field: str) -> None:
    with pytest.raises(ToolError) as exc:
        await api.list_topics(**kwargs)
    assert exc.value.details["field"] == field


# -- kafka_describe_topic -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_FR_007_AC_001_describe_topic_partitions_watermarks_as_strings(
    api: KafkaReadApi,
) -> None:
    outcome = await api.describe_topic(topic=TOPIC)
    item = _items(outcome)[0]
    assert item["approx_message_count"] == "6" and item["configs"]["retention.ms"] == "604800000"
    first = item["partitions"][0]
    assert (first["low_watermark"], first["high_watermark"]) == ("1000", "1004")
    assert first["approx_message_count"] == "4" and first["replicas"] == [1, 2, 3]
    assert all(isinstance(p["high_watermark"], str) for p in item["partitions"])
    assert any("xấp xỉ" in w for w in outcome.result.meta.warnings)
    assert outcome.result.citations[0].locator["topic"] == TOPIC


@pytest.mark.asyncio
async def test_describe_topic_without_configs(api: KafkaReadApi) -> None:
    item = _items(await api.describe_topic(topic=TOPIC, include_configs=False))[0]
    assert item["configs"] is None


@pytest.mark.asyncio
async def test_FR_007_AC_002_missing_topic_is_not_found_and_nothing_is_created(
    api: KafkaReadApi, cluster: FakeCluster
) -> None:
    """R17 / TC-025: on a broker with auto-create enabled, the AC-002 negative test must not
    mutate the cluster. The fake cluster *would* create the topic on a per-topic metadata
    request, so this proves we never send one."""
    assert cluster.auto_create is True
    before = sorted(cluster.topics)
    outcome = await api.describe_topic(topic="does.not.exist")
    assert outcome.result.status.value == "not_found" and outcome.result.items == []
    assert sorted(cluster.topics) == before and cluster.created == []
    assert all(kw["topic"] is None for name, kw in cluster.calls if name == "list_topics")


@pytest.mark.asyncio
async def test_R17_harness_sanity_a_per_topic_metadata_request_would_create_the_topic() -> None:
    cluster = FakeCluster(auto_create=True)
    admin = FakeAdmin({}, cluster)
    admin.list_topics(topic="victim", timeout=1)  # the forbidden call shape
    assert cluster.created == ["victim"] and "victim" in cluster.topics


@pytest.mark.parametrize(
    ("kwargs", "field"), [({"topic": ""}, "topic"), ({"topic": "t" * 250}, "topic")]
)
@pytest.mark.asyncio
async def test_describe_topic_invalid_input(api: KafkaReadApi, kwargs: dict, field: str) -> None:
    with pytest.raises(ToolError) as exc:
        await api.describe_topic(**kwargs)
    assert exc.value.details["field"] == field


# -- kafka_peek_messages --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_FR_007_AC_001_peek_cites_topic_partition_and_string_offset(
    api: KafkaReadApi,
) -> None:
    outcome = await api.peek_messages(topic=TOPIC, partition=0, limit=2)
    result = outcome.result
    assert [m["offset"] for m in _items(outcome)] == ["1002", "1003"]
    first = result.items[0]
    assert isinstance(first["offset"], str) and first["partition"] == 0
    locator = result.citations[first["citation_ref"]].locator
    assert locator == {"topic": TOPIC, "partition": 0, "offset": "1002"}
    assert any("assign()" in w and "no commit" in w for w in result.meta.warnings)


@pytest.mark.asyncio
async def test_peek_decodes_json_wraps_value_and_redacts(
    api: KafkaReadApi, cluster: FakeCluster
) -> None:
    cluster.add_topic(
        "secrets",
        {0: [msg(b'{"user":"a","password":"hunter2hunter2"}', key=b"k1",
                 headers=[("trace", b"abc"), ("none", None)], ts=1_790_000_000_000)]},
    )  # fmt: skip
    outcome = await api.peek_messages(topic="secrets", from_="earliest", limit=1)
    item = _items(outcome)[0]
    assert item["value_format"] == "json" and item["value"].startswith("<untrusted-content")
    assert "hunter2" not in item["value"] and outcome.result.meta.redactions >= 1
    assert item["key"] == "k1" and item["headers"] == {"trace": "abc", "none": None}
    assert item["timestamp"].startswith("2026-") and item["timestamp_type"] == "create_time"
    assert item["size_bytes"] == len(b'{"user":"a","password":"hunter2hunter2"}')


@pytest.mark.asyncio
async def test_peek_value_formats(api: KafkaReadApi, cluster: FakeCluster) -> None:
    cluster.add_topic("fmt", {0: [msg(b"\xff\xfe\x00"), msg(b"plain"), msg(None)]})
    items = _items(await api.peek_messages(topic="fmt", from_="earliest", limit=3))
    assert [i["value_format"] for i in items] == ["base64", "utf8", "none"]
    assert items[2]["value"] is None and items[2]["size_bytes"] is None
    forced = _items(
        await api.peek_messages(topic="fmt", from_="earliest", limit=3, value_format="base64")
    )
    assert [i["value_format"] for i in forced] == ["base64", "base64", "none"]
    asked_json = _items(
        await api.peek_messages(topic=TOPIC, partition=0, from_="earliest", limit=4,
                                value_format="json")
    )  # fmt: skip
    assert [i["value_format"] for i in asked_json] == ["json", "json", "json", "utf8"]


@pytest.mark.asyncio
async def test_peek_offset_and_timestamp_modes(api: KafkaReadApi) -> None:
    by_offset = await api.peek_messages(topic=TOPIC, partition=0, from_="offset", offset="1002")
    assert [m["offset"] for m in by_offset.result.items] == ["1002", "1003"]
    stamp = datetime(2026, 9, 21, tzinfo=UTC)  # epoch ms 1_790_000_000_000 is in this window
    by_time = await api.peek_messages(
        topic=TOPIC, partition=0, from_="timestamp", timestamp=stamp, limit=10
    )
    assert by_time.result.status.value in {"ok", "empty"}


@pytest.mark.asyncio
async def test_peek_empty_topic_is_empty_and_missing_topic_is_not_found(
    api: KafkaReadApi, cluster: FakeCluster
) -> None:
    empty = await api.peek_messages(topic="empty.topic")
    assert empty.result.status.value == "empty" and empty.result.citations == []
    assert any("rỗng" in w for w in empty.result.meta.warnings)
    before = sorted(cluster.topics)
    missing = await api.peek_messages(topic="ghost.topic")
    assert missing.result.status.value == "not_found"
    assert sorted(cluster.topics) == before and cluster.created == []  # R17 for peek too
    assert cluster.forbidden_calls == []


@pytest.mark.asyncio
async def test_peek_byte_budget_truncates_and_marks_partial(
    api: KafkaReadApi, cluster: FakeCluster
) -> None:
    cluster.add_topic("big", {0: [msg(b"x" * 3000, ts=1_790_000_000_000 + i) for i in range(6)]})
    outcome = await api.peek_messages(topic="big", from_="earliest", limit=6, max_bytes=1024)
    result = outcome.result
    assert result.status.value == "partial" and result.meta.truncated
    assert result.items[0]["truncated"] is True and len(result.items) < 6
    assert_envelope_invariants(result)


@pytest.mark.parametrize(
    ("kwargs", "field"),
    [
        ({"topic": ""}, "topic"), ({"partition": -1}, "partition"), ({"partition": 7}, "partition"),
        ({"from_": "middle"}, "from"), ({"from_": "offset"}, "offset"),
        ({"from_": "offset", "offset": "abc"}, "offset"),
        ({"from_": "offset", "offset": "1" * 20}, "offset"),
        ({"from_": "timestamp"}, "timestamp"),
        ({"from_": "timestamp", "timestamp": datetime(2026, 1, 1)}, "timestamp"),
        ({"limit": 0}, "limit"), ({"limit": 101}, "limit"),
        ({"value_format": "xml"}, "value_format"),
        ({"max_bytes": 10}, "max_bytes"), ({"timeout_s": 0}, "timeout_s"),
        ({"timeout_s": 23}, "timeout_s"),
    ],
)  # fmt: skip
@pytest.mark.asyncio
async def test_peek_invalid_input(api: KafkaReadApi, kwargs: dict, field: str) -> None:
    args = {"topic": TOPIC, **kwargs}
    with pytest.raises(ToolError) as exc:
        await api.peek_messages(**args)
    assert exc.value.code == ErrorCode.INVALID_INPUT and exc.value.details["field"] == field


@pytest.mark.asyncio
async def test_peek_timeout_s_must_be_below_the_per_tool_deadline(
    api: KafkaReadApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MCP_TOOL_DEADLINE_KAFKA_PEEK_MESSAGES", "10")
    with pytest.raises(ToolError) as exc:
        await api.peek_messages(topic=TOPIC, timeout_s=10)
    assert exc.value.details["field"] == "timeout_s"


@pytest.mark.asyncio
async def test_peek_timeout_in_the_adapter_makes_the_result_partial(
    api: KafkaReadApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mcp_kafka.ports import PeekResult

    async def timed_out(_request):
        return PeekResult((), timed_out=True)

    monkeypatch.setattr(api._client, "peek", timed_out)
    outcome = await api.peek_messages(topic=TOPIC, timeout_s=3)
    assert any("timeout_s=3s" in w for w in outcome.result.meta.warnings)
    assert outcome.result.meta.truncated


# -- kafka_list_consumer_groups -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_consumer_groups_pattern_states_and_empty(api: KafkaReadApi) -> None:
    outcome = await api.list_consumer_groups(pattern="payment-*")
    item = _items(outcome)[0]
    assert item["group_id"] == "payment-worker" and item["state"] == "Stable"
    assert item["is_simple_consumer_group"] is False
    assert outcome.result.citations[0].locator == {"group_id": "payment-worker"}
    stable = await api.list_consumer_groups(states=["Stable"])
    assert [g["group_id"] for g in stable.result.items] == ["payment-worker"]
    assert (await api.list_consumer_groups(pattern="zzz*")).result.status.value == "empty"
    limited = await api.list_consumer_groups(limit=1)
    assert len(limited.result.items) == 1 and limited.result.meta.truncated


@pytest.mark.parametrize(
    ("kwargs", "field"),
    [({"states": ["Bogus"]}, "states"), ({"states": ["Stable"] * 7}, "states"),
     ({"limit": 0}, "limit"), ({"pattern": "x" * 257}, "pattern")],
)  # fmt: skip
@pytest.mark.asyncio
async def test_list_consumer_groups_invalid_input(api: KafkaReadApi, kwargs, field) -> None:
    with pytest.raises(ToolError) as exc:
        await api.list_consumer_groups(**kwargs)
    assert exc.value.details["field"] == field


# -- kafka_describe_consumer_group ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_FR_009_AC_001_consumer_group_lag_per_partition(api: KafkaReadApi) -> None:
    outcome = await api.describe_consumer_group(group_id="payment-worker")
    item = _items(outcome)[0]
    rows = {(r["topic"], r["partition"]): r for r in item["partition_offsets"]}
    assert rows[(TOPIC, 0)] == {"topic": TOPIC, "partition": 0, "committed_offset": "1001",
                                "high_watermark": "1004", "lag": 3}  # fmt: skip
    assert rows[(TOPIC, 1)]["lag"] == 1002 and rows[(TOPIC, 1)]["committed_offset"] == "0"
    assert item["total_lag"] == 3 + 1002 and item["state"] == "Stable"
    member = item["members"][0]
    assert member["member_id"] == "consumer-1-abc" and member["assignments"][0] == {
        "topic": TOPIC, "partition": 0,
    }  # fmt: skip
    assert "lag=1005" in outcome.result.citations[0].label


@pytest.mark.asyncio
async def test_describe_consumer_group_without_members_and_never_commits(
    api: KafkaReadApi, cluster: FakeCluster
) -> None:
    item = _items(await api.describe_consumer_group(group_id="payment-worker",
                                                    include_members=False))[0]  # fmt: skip
    assert item["members"] is None
    assert cluster.groups["payment-worker"]["offsets"] == {(TOPIC, 0): 1001, (TOPIC, 1): 0}
    assert cluster.forbidden_calls == []  # TC-027: describing a group never moves its offsets


@pytest.mark.asyncio
async def test_describe_consumer_group_missing_or_dead_is_not_found(api: KafkaReadApi) -> None:
    for name in ("no-such-group", "dead-group"):
        outcome = await api.describe_consumer_group(group_id=name)
        assert outcome.result.status.value == "not_found", name


@pytest.mark.asyncio
async def test_describe_consumer_group_with_no_commits_has_zero_lag_rows(api: KafkaReadApi) -> None:
    item = _items(await api.describe_consumer_group(group_id="idle-group"))[0]
    assert item["partition_offsets"] == [] and item["total_lag"] == 0 and item["state"] == "Empty"


@pytest.mark.asyncio
async def test_describe_consumer_group_uncommitted_partition_lags_the_whole_log(
    api: KafkaReadApi, cluster: FakeCluster
) -> None:
    cluster.groups["payment-worker"]["offsets"][(TOPIC, 1)] = -1
    item = _items(await api.describe_consumer_group(group_id="payment-worker"))[0]
    row = next(r for r in item["partition_offsets"] if r["partition"] == 1)
    assert row["committed_offset"] == "-1" and row["lag"] == 2


@pytest.mark.parametrize(("group", "ok"), [("", False), ("g" * 257, False)])
@pytest.mark.asyncio
async def test_describe_consumer_group_invalid_input(
    api: KafkaReadApi, group: str, ok: bool
) -> None:
    with pytest.raises(ToolError) as exc:
        await api.describe_consumer_group(group_id=group)
    assert exc.value.details["field"] == "group_id" and not ok


# -- errors surface through the gateway -----------------------------------------------------------


@pytest.mark.asyncio
async def test_broker_errors_become_tool_errors(
    settings: Settings, common: CommonSettings, cluster: FakeCluster
) -> None:
    reader = ConfluentKafkaReader(
        settings,
        admin_factory=lambda conf: FakeAdmin(conf, cluster),
        consumer_factory=lambda conf: FakeConsumer(conf, cluster),
    )
    api = KafkaReadApi(KafkaClient(settings, common=common, reader=reader), common)
    cluster.fail = kafka_error(KafkaError._TRANSPORT)
    with pytest.raises(ToolError) as exc:
        await api.list_topics()
    assert exc.value.code == ErrorCode.UPSTREAM_UNAVAILABLE
    assert json.dumps(exc.value.details).count("kafka.example.test") >= 1
