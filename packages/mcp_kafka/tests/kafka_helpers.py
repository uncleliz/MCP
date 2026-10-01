"""An in-memory Kafka cluster behind fake `confluent_kafka` Admin/Consumer objects.

The cluster can simulate a broker with `auto.create.topics.enable=true`: a metadata request
that names a topic then *creates* it (R17). Tests assert the reader never does that, never
commits, never subscribes. Payloads are hand-written to confluent-kafka's documented shapes
(no broker is reachable from this container); the real broker is covered by the `live` tests.
"""

from __future__ import annotations

from concurrent.futures import Future
from types import SimpleNamespace
from typing import Any

from confluent_kafka import (
    ConsumerGroupState,
    ConsumerGroupTopicPartitions,
    KafkaError,
    KafkaException,
    TopicPartition,
)
from confluent_kafka.admin import AclOperation, AclPermissionType, ConsumerGroupDescription
from confluent_kafka.admin._group import MemberAssignment, MemberDescription

TOPIC = "payment.events"
CREATE_TIME, LOG_APPEND_TIME = 1, 2


def kafka_error(code: int) -> KafkaException:
    return KafkaException(KafkaError(code))


def done(value: Any = None, exc: Exception | None = None) -> Future:
    future: Future = Future()
    if exc is not None:
        future.set_exception(exc)
    else:
        future.set_result(value)
    return future


class FakeMessage:
    def __init__(self, topic: str, partition: int, offset: int, spec: dict[str, Any]) -> None:
        self._topic, self._partition, self._offset, self._spec = topic, partition, offset, spec

    def topic(self) -> str:
        return self._topic

    def partition(self) -> int:
        return self._partition

    def offset(self) -> int:
        return self._offset

    def timestamp(self) -> tuple[int, int]:
        return self._spec.get("ts_type", CREATE_TIME), self._spec.get("ts", 1_790_000_000_000)

    def key(self) -> bytes | None:
        return self._spec.get("key")

    def value(self) -> bytes | None:
        return self._spec.get("value")

    def headers(self) -> list[tuple[str, bytes | None]] | None:
        return self._spec.get("headers")

    def error(self) -> Any:
        return self._spec.get("error")


def msg(value: bytes | None, **spec: Any) -> dict[str, Any]:
    return {"value": value, **spec}


class FakeCluster:
    """topics[name][partition] = {"low": int, "messages": [spec, ...]}."""

    def __init__(self, auto_create: bool = True) -> None:
        self.auto_create = auto_create
        self.topics: dict[str, dict[int, dict[str, Any]]] = {}
        self.configs: dict[str, dict[str, str | None]] = {}
        self.groups: dict[str, dict[str, Any]] = {}
        self.acls: list[Any] | Exception = []
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.created: list[str] = []
        self.consumer_confs: list[dict[str, Any]] = []
        self.admin_confs: list[dict[str, Any]] = []
        self.forbidden_calls: list[str] = []  # commit / subscribe / store_offsets ...
        self.fail: Exception | None = None

    def add_topic(
        self, name: str, partitions: dict[int, list[dict[str, Any]]], low: int = 0
    ) -> None:
        self.topics[name] = {p: {"low": low, "messages": list(m)} for p, m in partitions.items()}
        self.configs.setdefault(name, {"cleanup.policy": "delete", "retention.ms": "604800000"})

    def high(self, topic: str, partition: int) -> int:
        part = self.topics[topic][partition]
        return part["low"] + len(part["messages"])

    def record(self, name: str, **kwargs: Any) -> None:
        self.calls.append((name, kwargs))

    def names(self, prefix: str) -> list[str]:
        return [name for name, _ in self.calls if name.startswith(prefix)]


class FakeAdmin:
    def __init__(self, conf: dict[str, Any], cluster: FakeCluster) -> None:
        self.cluster = cluster
        cluster.admin_confs.append(conf)

    def list_topics(self, topic: str | None = None, timeout: float = -1) -> Any:
        c = self.cluster
        c.record("list_topics", topic=topic, timeout=timeout)
        if c.fail:
            raise c.fail
        if topic is not None and c.auto_create and topic not in c.topics:
            c.add_topic(topic, {0: []})  # what a real broker with auto-create would do
            c.created.append(topic)
        topics = {
            name: SimpleNamespace(
                topic=name,
                error=None,
                partitions={
                    p: SimpleNamespace(id=p, leader=1, replicas=[1, 2, 3], isrs=[1, 2, 3])
                    for p in parts
                },
            )
            for name, parts in c.topics.items()
            if topic is None or name == topic
        }
        return SimpleNamespace(topics=topics)

    def describe_configs(self, resources: list[Any], request_timeout: float = -1) -> dict:
        out = {}
        for resource in resources:
            self.cluster.record("describe_configs", topic=resource.name)
            entries = {
                key: SimpleNamespace(name=key, value=value)
                for key, value in self.cluster.configs.get(resource.name, {}).items()
            }
            out[resource] = done(entries)
        return out

    def list_consumer_groups(self, states: Any = None, request_timeout: float = -1) -> Future:
        self.cluster.record("list_consumer_groups", states=states)
        valid = [
            SimpleNamespace(
                group_id=gid, is_simple_consumer_group=False, state=info["state"], type=None
            )
            for gid, info in self.cluster.groups.items()
            if not states or info["state"] in states
        ]
        return done(SimpleNamespace(valid=valid, errors=[]))

    def describe_consumer_groups(self, group_ids: list[str], request_timeout: float = -1) -> dict:
        out = {}
        for gid in group_ids:
            self.cluster.record("describe_consumer_groups", group=gid)
            info = self.cluster.groups.get(gid)
            if info is None:
                out[gid] = done(exc=kafka_error(KafkaError.GROUP_ID_NOT_FOUND))
                continue
            members = [
                MemberDescription(
                    member_id=m["id"], client_id=m["client"], host=m["host"],
                    assignment=MemberAssignment([TopicPartition(t, p) for t, p in m["assign"]]),
                )
                for m in info["members"]
            ]  # fmt: skip
            out[gid] = done(
                ConsumerGroupDescription(
                    gid, False, members, "range", info["state"], SimpleNamespace(id=1)
                )
            )
        return out

    def list_consumer_group_offsets(self, requests: list[Any], request_timeout: float = -1) -> dict:
        out = {}
        for request in requests:
            gid = request.group_id
            self.cluster.record("list_consumer_group_offsets", group=gid)
            info = self.cluster.groups.get(gid, {"offsets": {}})
            tps = [TopicPartition(t, p, o) for (t, p), o in info["offsets"].items()]
            out[gid] = done(ConsumerGroupTopicPartitions(gid, tps))
        return out

    def describe_acls(self, acl_filter: Any, request_timeout: float = -1) -> Future:
        self.cluster.record("describe_acls", principal=acl_filter.principal)
        if isinstance(self.cluster.acls, Exception):
            return done(exc=self.cluster.acls)
        return done(list(self.cluster.acls))


class FakeConsumer:
    """Records everything; `commit`/`subscribe`/... are tripwires, not behaviour."""

    def __init__(self, conf: dict[str, Any], cluster: FakeCluster) -> None:
        self.cluster = cluster
        self.conf = conf
        cluster.consumer_confs.append(conf)
        self._positions: dict[tuple[str, int], int] = {}
        self.closed = False

    def assign(self, partitions: list[TopicPartition]) -> None:
        self.cluster.record(
            "assign", partitions=[(p.topic, p.partition, p.offset) for p in partitions]
        )
        for p in partitions:
            self._positions[(p.topic, p.partition)] = p.offset

    def poll(self, timeout: float = 0.0) -> FakeMessage | None:
        for (topic, partition), position in sorted(self._positions.items()):
            part = self.cluster.topics[topic][partition]
            index = position - part["low"]
            if 0 <= index < len(part["messages"]):
                self._positions[(topic, partition)] = position + 1
                return FakeMessage(topic, partition, position, part["messages"][index])
        return None

    def get_watermark_offsets(
        self, partition: TopicPartition, timeout: float = -1, cached: bool = False
    ) -> tuple[int, int]:
        self.cluster.record("get_watermark_offsets", topic=partition.topic, cached=cached)
        part = self.cluster.topics[partition.topic][partition.partition]
        return part["low"], part["low"] + len(part["messages"])

    def offsets_for_times(self, partitions: list[TopicPartition], timeout: float = -1) -> list:
        out = []
        for p in partitions:
            part = self.cluster.topics[p.topic][p.partition]
            hit = next(
                (
                    part["low"] + i
                    for i, m in enumerate(part["messages"])
                    if m.get("ts", 1_790_000_000_000) >= p.offset
                ),
                -1,
            )
            out.append(TopicPartition(p.topic, p.partition, hit))
        return out

    def close(self) -> None:
        self.closed = True

    def _tripwire(self, name: str) -> Any:
        def trip(*_a: Any, **_k: Any) -> Any:
            self.cluster.forbidden_calls.append(name)
            raise AssertionError(f"read-only violation: Consumer.{name}() was called")

        return trip

    def __getattr__(self, name: str) -> Any:
        if name in {"commit", "subscribe", "store_offsets", "incremental_assign", "seek_x"}:
            return self._tripwire(name)
        raise AttributeError(name)


def make_cluster() -> FakeCluster:
    cluster = FakeCluster()
    cluster.add_topic(
        TOPIC,
        {
            0: [
                msg(b'{"order_id":"8891","status":"RETRY"}', key=b"order-8891",
                    headers=[("content-type", b"application/json")], ts=1_790_000_000_000)
                for _ in range(3)
            ]
            + [msg(b"plain text event", ts=1_790_000_100_000)],
            1: [msg(b"p1-a", ts=1_790_000_050_000), msg(None, ts=1_790_000_060_000)],
        },
        low=1000,
    )  # fmt: skip
    cluster.add_topic("empty.topic", {0: []})
    cluster.groups["payment-worker"] = {
        "state": ConsumerGroupState.STABLE,
        "members": [{"id": "consumer-1-abc", "client": "consumer-1", "host": "/10.0.1.21",
                     "assign": [(TOPIC, 0), (TOPIC, 1)]}],
        "offsets": {(TOPIC, 0): 1001, (TOPIC, 1): 0},
    }  # fmt: skip
    cluster.groups["idle-group"] = {"state": ConsumerGroupState.EMPTY, "members": [], "offsets": {}}
    cluster.groups["dead-group"] = {"state": ConsumerGroupState.DEAD, "members": [], "offsets": {}}
    return cluster


def acl(operation: AclOperation, permission: AclPermissionType = AclPermissionType.ALLOW) -> Any:
    return SimpleNamespace(
        operation=operation, permission_type=permission, principal="User:mcp_ro", name="t*"
    )


class InMemoryReader:
    """A second `KafkaReader` implementation with no Kafka library at all (TC-028, R7).

    The tool-layer tests run against this adapter AND the confluent adapter and must pass
    unchanged for both: proof that swapping the client library never touches `read_api.py`.
    """

    def __init__(self, cluster: FakeCluster) -> None:
        self.cluster = cluster

    def list_topics(self):
        from mcp_kafka.ports import PartitionMeta, TopicMeta

        return [
            TopicMeta(
                name,
                tuple(PartitionMeta(p, 1, (1, 2, 3), (1, 2, 3)) for p in sorted(parts)),
                internal=name.startswith("__"),
            )
            for name, parts in sorted(self.cluster.topics.items())
        ]

    def topic_configs(self, topic):
        return dict(self.cluster.configs.get(topic, {}))

    def watermarks(self, topic, partitions):
        from mcp_kafka.ports import Watermarks

        return {p: Watermarks(self.cluster.topics[topic][p]["low"], self.cluster.high(topic, p))
                for p in partitions}  # fmt: skip

    def peek(self, request):
        from mcp_kafka.ports import PeekResult, RawMessage

        collected = []
        for p in request.partitions:
            part = self.cluster.topics[request.topic][p]
            low, high = part["low"], self.cluster.high(request.topic, p)
            if request.mode == "latest":
                start = max(low, high - -(-request.limit // len(request.partitions)))
            elif request.mode == "offset":
                start = min(max(request.offset or 0, low), high)
            else:
                start = low
            for index in range(start - low, len(part["messages"])):
                spec = part["messages"][index]
                collected.append(
                    RawMessage(
                        request.topic,
                        p,
                        low + index,
                        spec.get("ts"),
                        "create_time",
                        spec.get("key"),
                        spec.get("value"),
                        tuple(spec.get("headers") or ()),
                    )  # fmt: skip
                )
        collected.sort(key=lambda m: (m.timestamp_ms or 0, m.partition, m.offset))
        kept = (
            collected[-request.limit :] if request.mode == "latest" else collected[: request.limit]
        )
        return PeekResult(tuple(kept))

    def list_consumer_groups(self, states):
        from mcp_kafka.ports import GroupSummary

        names = {s for s in states}
        out = []
        for gid, info in sorted(self.cluster.groups.items()):
            state = {"STABLE": "Stable", "EMPTY": "Empty", "DEAD": "Dead"}[info["state"].name]
            if not names or state in names:
                out.append(GroupSummary(gid, state, False, None))
        return out

    def describe_consumer_group(self, group_id):
        from mcp_kafka.ports import GroupDetail, GroupMember

        info = self.cluster.groups.get(group_id)
        if info is None:
            return None
        members = tuple(
            GroupMember(m["id"], m["client"], m["host"], tuple(m["assign"]))
            for m in info["members"]
        )
        state = {"STABLE": "Stable", "EMPTY": "Empty", "DEAD": "Dead"}[info["state"].name]
        return GroupDetail(group_id, state, members)

    def committed_offsets(self, group_id):
        return {
            k: (v if v >= 0 else -1) for k, v in self.cluster.groups[group_id]["offsets"].items()
        }

    def verify_readonly(self):
        from mcp_kafka.ports import ReadonlyVerdict

        return ReadonlyVerdict()

    def close(self) -> None:
        return None


OK_CALLS: list[tuple[str, dict[str, Any]]] = [
    ("kafka_list_topics", {"pattern": "payment.*"}),
    ("kafka_describe_topic", {"topic": TOPIC, "include_configs": True}),
    ("kafka_peek_messages", {"topic": TOPIC, "from": "latest", "limit": 5, "value_format": "auto"}),
    ("kafka_list_consumer_groups", {"pattern": "payment-*"}),
    ("kafka_describe_consumer_group", {"group_id": "payment-worker"}),
]
