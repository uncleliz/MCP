"""T-046: read-only protocol of the confluent-kafka adapter + the startup assert (R17, ADR-0009)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from confluent_kafka import KafkaError
from confluent_kafka.admin import AclOperation, AclPermissionType
from kafka_helpers import TOPIC, FakeAdmin, FakeCluster, FakeConsumer, acl, kafka_error
from mcp_common.config import CommonSettings
from mcp_common.errors import ErrorCode, ToolError
from mcp_common.runtime import BoundedExecutor
from mcp_kafka.client import (
    ConfluentKafkaReader,
    KafkaClient,
    assert_readonly_config,
    build_admin_config,
    build_consumer_config,
    to_tool_error,
)
from mcp_kafka.ports import PeekRequest
from mcp_kafka.settings import Settings
from pydantic import SecretStr

import mcp_kafka

PACKAGE_DIR = Path(mcp_kafka.__file__).parent


# -- configuration (ADR-0009 A1/A3) ----------------------------------------------------------------


def test_FR_007_AC_003_admin_and_consumer_configs_are_read_only_and_bounded(
    settings: Settings,
) -> None:
    admin, consumer = build_admin_config(settings), build_consumer_config(settings)
    for conf in (admin, consumer):
        assert conf["allow.auto.create.topics"] is False  # R17 layer 1, on BOTH
        assert conf["socket.timeout.ms"] == 8000 and conf["metadata.request.timeout.ms"] == 8000
        assert conf["bootstrap.servers"] == "kafka.example.test:9092"
    assert consumer["enable.auto.commit"] is False
    assert consumer["enable.auto.offset.store"] is False
    assert consumer["group.id"].startswith("mcp-readonly-")
    assert build_consumer_config(settings)["group.id"] != consumer["group.id"]  # throwaway
    assert "transactional.id" not in consumer and "enable.idempotence" not in admin


def test_sasl_settings_are_translated_and_secrets_stay_out_of_repr() -> None:
    settings = Settings(
        bootstrap_servers="b1:9093,b2:9093",
        security_protocol="SASL_SSL",
        sasl_username="mcp_ro",
        sasl_password=SecretStr("kafka-pw-not-real"),
        ssl_ca_location="/etc/ssl/ca.pem",
    )
    conf = build_admin_config(settings)
    assert conf["security.protocol"] == "SASL_SSL" and conf["sasl.mechanism"] == "SCRAM-SHA-512"
    assert conf["sasl.username"] == "mcp_ro" and conf["sasl.password"] == "kafka-pw-not-real"
    assert conf["ssl.ca.location"] == "/etc/ssl/ca.pem"
    assert "kafka-pw-not-real" not in repr(settings)


def test_assert_readonly_config_accepts_ours_and_rejects_each_violation(
    settings: Settings,
) -> None:
    admin, consumer = build_admin_config(settings), build_consumer_config(settings)
    assert assert_readonly_config(admin, consumer) == []
    broken = [
        ({**admin, "allow.auto.create.topics": True}, consumer, "allow.auto.create.topics"),
        (admin, {**consumer, "allow.auto.create.topics": True}, "allow.auto.create.topics"),
        (admin, {**consumer, "enable.auto.commit": True}, "enable.auto.commit"),
        (admin, {**consumer, "group.id": "payment-worker"}, "group.id"),
        (admin, {**consumer, "transactional.id": "x"}, "transactional.id"),
        ({k: v for k, v in admin.items() if k != "allow.auto.create.topics"}, consumer,
         "allow.auto.create.topics"),
    ]  # fmt: skip
    for admin_conf, consumer_conf, needle in broken:
        problems = assert_readonly_config(admin_conf, consumer_conf)
        assert problems and any(needle in p for p in problems), needle


def test_adapter_builds_clients_only_with_readonly_configs(
    reader: ConfluentKafkaReader, cluster: FakeCluster
) -> None:
    reader.list_topics()
    reader.watermarks(TOPIC, [0])
    assert cluster.admin_confs and cluster.consumer_confs
    for conf in cluster.admin_confs + cluster.consumer_confs:
        assert conf["allow.auto.create.topics"] is False
    assert assert_readonly_config(cluster.admin_confs[0], cluster.consumer_confs[0]) == []


# -- R17: topic discovery never names a topic ---------------------------------------------------


def test_R17_list_topics_is_cluster_wide_and_never_passes_a_topic(
    reader: ConfluentKafkaReader, cluster: FakeCluster
) -> None:
    topics = reader.list_topics()
    assert {t.name for t in topics} == {TOPIC, "empty.topic"}
    calls = [kw for name, kw in cluster.calls if name == "list_topics"]
    assert calls and all(kw["topic"] is None for kw in calls)
    assert calls[0]["timeout"] == 8
    detail = next(t for t in topics if t.name == TOPIC)
    assert [p.id for p in detail.partitions] == [0, 1] and detail.replication_factor == 3
    assert detail.partitions[0].leader == 1 and detail.internal is False


def test_R17_internal_topics_are_flagged() -> None:
    cluster = FakeCluster()
    cluster.add_topic("__consumer_offsets", {0: []})
    reader = ConfluentKafkaReader(
        Settings(bootstrap_servers="k:9092"),
        admin_factory=lambda conf: FakeAdmin(conf, cluster),
        consumer_factory=lambda conf: FakeConsumer(conf, cluster),
    )
    assert reader.list_topics()[0].internal is True


def test_topic_configs_and_watermarks(reader: ConfluentKafkaReader, cluster: FakeCluster) -> None:
    assert reader.topic_configs(TOPIC)["retention.ms"] == "604800000"
    marks = reader.watermarks(TOPIC, [0, 1])
    assert (marks[0].low, marks[0].high, marks[0].count) == (1000, 1004, 4)
    assert (marks[1].low, marks[1].high) == (1000, 1002)
    assert all(not kw["cached"] for name, kw in cluster.calls if name == "get_watermark_offsets")


# -- peek: assign, no subscribe, no commit (FR-007/AC-003, TC-026/027) ----------------------------


def _peek(reader, **kw):
    base = {"topic": TOPIC, "partitions": (0, 1), "mode": "latest", "limit": 3, "timeout_s": 2.0}
    return reader.peek(PeekRequest(**{**base, **kw}))


def test_FR_007_AC_003_peek_assigns_manually_and_never_commits_or_subscribes(
    reader: ConfluentKafkaReader, cluster: FakeCluster
) -> None:
    result = _peek(reader)
    assert len(result.messages) == 3 and not result.timed_out
    assert cluster.forbidden_calls == []
    assert "assign" in cluster.names("assign")
    assert all(c["enable.auto.commit"] is False for c in cluster.consumer_confs)
    assert all(c["group.id"].startswith("mcp-readonly-") for c in cluster.consumer_confs)


def test_peek_latest_takes_the_tail_of_each_partition_in_offset_order(reader) -> None:
    result = _peek(reader, partitions=(0,), limit=2)
    assert [m.offset for m in result.messages] == [1002, 1003]
    assert result.messages[1].value == b"plain text event"


def test_peek_earliest_and_offset_clamp_to_the_watermarks(reader) -> None:
    earliest = _peek(reader, partitions=(0,), mode="earliest", limit=2)
    assert [m.offset for m in earliest.messages] == [1000, 1001]
    clamped = _peek(reader, partitions=(0,), mode="offset", offset=5, limit=2)
    assert [m.offset for m in clamped.messages] == [1000, 1001]  # below low -> low
    beyond = _peek(reader, partitions=(0,), mode="offset", offset=99999, limit=2)
    assert beyond.messages == ()  # above high -> nothing, not an error
    mid = _peek(reader, partitions=(0,), mode="offset", offset=1002, limit=5)
    assert [m.offset for m in mid.messages] == [1002, 1003]


def test_peek_timestamp_mode_uses_offsets_for_times(reader) -> None:
    result = _peek(reader, partitions=(0,), mode="timestamp", timestamp_ms=1_790_000_100_000)
    assert [m.offset for m in result.messages] == [1003]
    after_all = _peek(reader, partitions=(0,), mode="timestamp", timestamp_ms=1_800_000_000_000)
    assert after_all.messages == ()


def test_peek_decodes_message_fields(reader) -> None:
    first = _peek(reader, partitions=(0,), mode="earliest", limit=1).messages[0]
    assert first.topic == TOPIC and first.partition == 0 and first.key == b"order-8891"
    assert first.headers == (("content-type", b"application/json"),)
    assert first.timestamp_ms == 1_790_000_000_000 and first.timestamp_type == "create_time"
    tombstone = _peek(reader, partitions=(1,), mode="earliest", limit=2).messages[1]
    assert tombstone.value is None and tombstone.headers == ()


def test_peek_empty_topic_and_timeout_flag(reader, cluster: FakeCluster) -> None:
    assert _peek(reader, topic="empty.topic", partitions=(0,)).messages == ()
    cluster.topics[TOPIC][0]["messages"].append(
        {"error": kafka_error(KafkaError._TRANSPORT).args[0]}
    )
    with pytest.raises(Exception) as exc:  # a real consumer error is surfaced, not swallowed
        _peek(reader, partitions=(0,), mode="offset", offset=1004, limit=1)
    assert "TRANSPORT" in str(exc.value)


def test_peek_stops_at_the_deadline_with_partial_result(
    settings: Settings, cluster: FakeCluster
) -> None:
    class Slow(FakeConsumer):
        def poll(self, timeout: float = 0.0):
            import time

            time.sleep(0.05)
            return None

    reader = ConfluentKafkaReader(
        settings,
        admin_factory=lambda conf: FakeAdmin(conf, cluster),
        consumer_factory=lambda conf: Slow(conf, cluster),
    )
    # high watermark is ahead of the (stuck) consumer, so it must give up at timeout_s
    result = reader.peek(
        PeekRequest(topic=TOPIC, partitions=(0,), mode="earliest", limit=3, timeout_s=0.2)
    )
    assert result.timed_out and result.messages == ()


def test_peek_closes_its_consumer(settings: Settings, cluster: FakeCluster) -> None:
    made: list[FakeConsumer] = []

    def factory(conf):
        consumer = FakeConsumer(conf, cluster)
        made.append(consumer)
        return consumer

    reader = ConfluentKafkaReader(
        settings, admin_factory=lambda c: FakeAdmin(c, cluster), consumer_factory=factory
    )
    _peek(reader)
    assert made and all(c.closed for c in made)


# -- consumer groups ------------------------------------------------------------------------------


def test_consumer_group_listing_maps_state_names_and_filters(reader) -> None:
    groups = {g.group_id: g for g in reader.list_consumer_groups([])}
    assert groups["payment-worker"].state == "Stable" and groups["idle-group"].state == "Empty"
    assert groups["payment-worker"].is_simple is False
    only_stable = reader.list_consumer_groups(["Stable"])
    assert [g.group_id for g in only_stable] == ["payment-worker"]


def test_describe_consumer_group_members_and_missing(reader) -> None:
    detail = reader.describe_consumer_group("payment-worker")
    assert detail is not None and detail.state == "Stable"
    member = detail.members[0]
    assert member.member_id == "consumer-1-abc" and member.host == "/10.0.1.21"
    assert member.assignments == ((TOPIC, 0), (TOPIC, 1))
    assert reader.describe_consumer_group("no-such-group") is None


def test_committed_offsets_are_read_without_side_effects(reader, cluster: FakeCluster) -> None:
    offsets = reader.committed_offsets("payment-worker")
    assert offsets == {(TOPIC, 0): 1001, (TOPIC, 1): 0}
    assert cluster.names("list_consumer_group_offsets") and cluster.forbidden_calls == []
    assert reader.committed_offsets("idle-group") == {}


def test_invalid_committed_offset_is_reported_as_minus_one(
    settings: Settings, cluster: FakeCluster
) -> None:
    cluster.groups["payment-worker"]["offsets"][(TOPIC, 1)] = -1001  # OFFSET_INVALID
    reader = ConfluentKafkaReader(
        settings,
        admin_factory=lambda conf: FakeAdmin(conf, cluster),
        consumer_factory=lambda conf: FakeConsumer(conf, cluster),
    )
    assert reader.committed_offsets("payment-worker")[(TOPIC, 1)] == -1


# -- startup assert (ADR-0009 A2) -----------------------------------------------------------------


def test_verify_readonly_ok_when_acls_grant_only_describe_and_read(
    sasl_reader, cluster: FakeCluster
) -> None:
    cluster.acls = [acl(AclOperation.READ), acl(AclOperation.DESCRIBE)]
    verdict = sasl_reader.verify_readonly()
    assert verdict.ok and verdict.violations == ()
    assert cluster.names("describe_acls")


@pytest.mark.parametrize(
    "operation",
    [AclOperation.ALL, AclOperation.WRITE, AclOperation.CREATE, AclOperation.DELETE,
     AclOperation.ALTER, AclOperation.ALTER_CONFIGS, AclOperation.IDEMPOTENT_WRITE],
)  # fmt: skip
def test_verify_readonly_refuses_allow_acls_for_write_capable_operations(
    sasl_reader, cluster: FakeCluster, operation: AclOperation
) -> None:
    cluster.acls = [acl(AclOperation.READ), acl(operation)]
    verdict = sasl_reader.verify_readonly()
    assert not verdict.ok and operation.name in verdict.violations[0]


def test_verify_readonly_deny_acls_do_not_count(sasl_reader, cluster: FakeCluster) -> None:
    cluster.acls = [acl(AclOperation.CREATE, AclPermissionType.DENY), acl(AclOperation.READ)]
    assert sasl_reader.verify_readonly().ok


def test_verify_readonly_skips_acl_check_when_not_permitted_or_without_principal(
    settings: Settings, cluster: FakeCluster
) -> None:
    denied = ConfluentKafkaReader(
        Settings(bootstrap_servers="k:9092", security_protocol="SASL_SSL", sasl_username="u",
                 sasl_password=SecretStr("p")),
        admin_factory=lambda conf: FakeAdmin(conf, cluster),
        consumer_factory=lambda conf: FakeConsumer(conf, cluster),
    )  # fmt: skip
    cluster.acls = kafka_error(KafkaError.CLUSTER_AUTHORIZATION_FAILED)
    verdict = denied.verify_readonly()
    assert verdict.ok and any("describe_acls" in n for n in verdict.notes)
    plain = ConfluentKafkaReader(
        settings,
        admin_factory=lambda conf: FakeAdmin(conf, cluster),
        consumer_factory=lambda conf: FakeConsumer(conf, cluster),
    )
    asked_before = len(cluster.names("describe_acls"))
    verdict = plain.verify_readonly()
    assert verdict.ok and any("principal" in n for n in verdict.notes)
    assert len(cluster.names("describe_acls")) == asked_before  # nothing to scope the query to


# -- architecture tests (TC-026) ------------------------------------------------------------------


def _source_trees() -> dict[str, ast.AST]:
    return {p.name: ast.parse(p.read_text(encoding="utf-8")) for p in PACKAGE_DIR.glob("*.py")}


def test_TC_026_no_code_path_can_create_a_producer_subscribe_or_commit() -> None:
    forbidden_names = {"Producer", "SerializingProducer", "AIOProducer"}
    forbidden_attrs = {
        "subscribe", "commit", "store_offsets", "produce", "flush", "create_topics",
        "delete_topics", "create_partitions", "alter_configs", "incremental_alter_configs",
        "delete_records", "delete_consumer_groups", "alter_consumer_group_offsets",
        "create_acls", "delete_acls", "elect_leaders", "init_transactions",
    }  # fmt: skip
    offenders: list[str] = []
    for name, tree in _source_trees().items():
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in forbidden_names:
                offenders.append(f"{name}:{node.lineno} name {node.id}")
            if isinstance(node, ast.Attribute) and node.attr in forbidden_attrs | forbidden_names:
                offenders.append(f"{name}:{node.lineno} attr {node.attr}")
            if isinstance(node, ast.ImportFrom | ast.Import):
                for alias in node.names:
                    if alias.name in forbidden_names:
                        offenders.append(f"{name}:{node.lineno} import {alias.name}")
    assert offenders == []


def test_R17_no_metadata_call_ever_passes_a_topic_keyword() -> None:
    offenders: list[str] = []
    for name, tree in _source_trees().items():
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "list_topics"
                and (node.args or any(kw.arg == "topic" for kw in node.keywords))
            ):
                offenders.append(f"{name}:{node.lineno}")
    assert offenders == []


def test_TC_028_tool_layer_does_not_import_any_kafka_client_library() -> None:
    """R7: swapping the client library touches the adapter (`client.py`) only."""
    for module in ("read_api.py", "mappers.py", "tools.py", "ports.py", "server.py"):
        tree = ast.parse((PACKAGE_DIR / module).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            assert not any(n.split(".")[0] in {"confluent_kafka", "kafka"} for n in names), (
                module,
                names,
            )


# -- async gateway --------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gateway_runs_the_reader_on_the_bounded_executor(
    settings: Settings, common: CommonSettings, reader: ConfluentKafkaReader
) -> None:
    import threading

    names: list[str] = []
    original = reader.list_topics

    def spy():
        names.append(threading.current_thread().name)
        return original()

    reader.list_topics = spy  # type: ignore[method-assign]
    gateway = KafkaClient(settings, common=common, reader=reader, executor=BoundedExecutor(2))
    topics = await gateway.list_topics()
    assert {t.name for t in topics} == {TOPIC, "empty.topic"}
    assert names and names[0].startswith("ThreadPoolExecutor")
    await gateway.aclose()


@pytest.mark.asyncio
async def test_R16_exhausted_executor_fails_the_next_call_fast(
    settings: Settings, common: CommonSettings, reader: ConfluentKafkaReader
) -> None:
    import asyncio
    import threading
    import time

    release = threading.Event()
    reader.list_topics = lambda: (release.wait(10), [])[1]  # type: ignore[method-assign]
    gateway = KafkaClient(settings, common=common, reader=reader, executor=BoundedExecutor(2))
    hung = [asyncio.create_task(gateway.list_topics()) for _ in range(2)]
    await asyncio.sleep(0.1)
    started = time.monotonic()
    with pytest.raises(ToolError) as exc:
        await gateway.list_topics()
    assert exc.value.code == ErrorCode.UPSTREAM_UNAVAILABLE and time.monotonic() - started < 0.5
    assert "kafka.example.test:9092" in exc.value.details["hint"]
    release.set()
    await asyncio.gather(*hung)


# -- error mapping --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (KafkaError._TRANSPORT, ErrorCode.UPSTREAM_UNAVAILABLE),
        (KafkaError._ALL_BROKERS_DOWN, ErrorCode.UPSTREAM_UNAVAILABLE),
        (KafkaError._TIMED_OUT, ErrorCode.UPSTREAM_TIMEOUT),
        (KafkaError.REQUEST_TIMED_OUT, ErrorCode.UPSTREAM_TIMEOUT),
        (KafkaError._AUTHENTICATION, ErrorCode.UNAUTHORIZED),
        (KafkaError.SASL_AUTHENTICATION_FAILED, ErrorCode.UNAUTHORIZED),
        (KafkaError.TOPIC_AUTHORIZATION_FAILED, ErrorCode.FORBIDDEN),
        (KafkaError.GROUP_AUTHORIZATION_FAILED, ErrorCode.FORBIDDEN),
        (KafkaError.INVALID_REQUEST, ErrorCode.UPSTREAM_ERROR),
    ],
)
def test_kafka_errors_map_to_contract_error_codes(code: int, expected: ErrorCode) -> None:
    error = to_tool_error(kafka_error(code), host="kafka.example.test:9092")
    assert error.code == expected and error.source == "kafka"
    if expected in {ErrorCode.UPSTREAM_UNAVAILABLE, ErrorCode.UPSTREAM_TIMEOUT}:
        assert "VPN" in error.details["hint"] and "kafka.example.test" in error.details["hint"]


def test_non_kafka_exception_falls_back_to_internal() -> None:
    assert to_tool_error(RuntimeError("x"), "h").code == ErrorCode.INTERNAL


# -- startup check via the gateway ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_startup_check_ok_and_notes(
    sasl_client: KafkaClient, client: KafkaClient, cluster: FakeCluster
) -> None:
    cluster.acls = [acl(AclOperation.READ)]
    report = await sasl_client.verify_credentials()
    assert report.ok and report.reasons == [] and report.warnings == []
    assert await sasl_client.credential_check() is True
    plain = await client.verify_credentials()  # no SASL principal -> note, not failure
    assert plain.ok and any("principal" in w for w in plain.warnings)


@pytest.mark.asyncio
async def test_startup_check_refuses_write_acls(
    sasl_client: KafkaClient, cluster: FakeCluster
) -> None:
    cluster.acls = [acl(AclOperation.CREATE)]
    report = await sasl_client.verify_credentials()
    assert not report.ok and "CREATE" in report.reasons[0]
    assert await sasl_client.credential_check() is False


@pytest.mark.asyncio
async def test_startup_check_fails_when_the_broker_is_unreachable(
    client: KafkaClient, cluster: FakeCluster
) -> None:
    cluster.fail = kafka_error(KafkaError._TRANSPORT)
    report = await client.verify_credentials()
    assert not report.ok and "upstream_unavailable" in report.reasons[0]


@pytest.mark.asyncio
async def test_startup_check_fails_on_a_non_readonly_config(
    settings: Settings,
    common: CommonSettings,
    reader: ConfluentKafkaReader,
    cluster: FakeCluster,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import mcp_kafka.client as client_module

    good = client_module.build_consumer_config
    monkeypatch.setattr(
        client_module, "build_consumer_config", lambda s: {**good(s), "enable.auto.commit": True}
    )  # a mis-built adapter
    gateway = KafkaClient(settings, common=common, reader=reader)
    report = await gateway.verify_credentials()
    assert not report.ok and "enable.auto.commit" in report.reasons[0]
    assert cluster.names("describe_acls") == []  # failed before touching the network
