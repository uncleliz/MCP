"""Kafka transport layer (ADR-0009): the `confluent-kafka` adapter behind the `KafkaReader` port.

Read-only guarantees in this file (ADR-0009 A1/A2, R17):

* only an `AdminClient` and short-lived `Consumer`s are ever created — there is no producer
  anywhere in the package (an AST test enforces it, with `subscribe`/commit/admin writes);
* `allow.auto.create.topics=false` on BOTH client kinds, and topic discovery is *always* the
  cluster-wide `list_topics()` filtered client-side — a metadata request that names a topic
  would create that topic on a broker with auto-create enabled, turning the negative test of
  FR-007/AC-002 into a write;
* `peek` uses `assign()` with explicit start offsets (no consumer group membership),
  `enable.auto.commit=false`, `enable.auto.offset.store=false`, a throw-away `group.id` and
  never calls the commit API, so other groups' committed offsets cannot move;
* the startup assert (:meth:`ConfluentKafkaReader.verify_readonly`) re-checks that
  configuration and, if ACL introspection is allowed, that our principal holds no
  write-capable ACL.

`socket.timeout.ms` / `metadata.request.timeout.ms` = 8000 (ADR-0009 A3); note librdkafka
2.x marks `metadata.request.timeout.ms` as unused, so the per-call `timeout`/`request_timeout`
below are what actually bound each request (spike S4).
"""

from __future__ import annotations

import math
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from confluent_kafka import (
    Consumer,
    ConsumerGroupState,
    ConsumerGroupTopicPartitions,
    KafkaError,
    KafkaException,
    TopicPartition,
)
from confluent_kafka.admin import (
    AclBindingFilter,
    AclOperation,
    AclPermissionType,
    AdminClient,
    ConfigResource,
    ResourcePatternType,
    ResourceType,
)
from mcp_common.config import CommonSettings
from mcp_common.errors import ErrorCode, ToolError, map_exception_to_tool_error
from mcp_common.runtime import BoundedExecutor

from mcp_kafka.ports import (
    GroupDetail,
    GroupMember,
    GroupSummary,
    PartitionMeta,
    PeekRequest,
    PeekResult,
    RawMessage,
    ReadonlyVerdict,
    TopicMeta,
    Watermarks,
)
from mcp_kafka.settings import Settings

__all__ = [
    "ConfluentKafkaReader",
    "CredentialReport",
    "KafkaClient",
    "SOURCE",
    "assert_readonly_config",
    "build_admin_config",
    "build_consumer_config",
    "to_tool_error",
]

SOURCE = "kafka"
TIMEOUT_MS = 8000
TIMEOUT_S = 8
GROUP_PREFIX = "mcp-readonly-"
_POLL_SLICE_S = 1.0
_OFFSET_INVALID = -1001
_WRITE_CAPABLE_ACL_OPERATIONS = frozenset(
    {"ALL", "WRITE", "CREATE", "DELETE", "ALTER", "ALTER_CONFIGS", "IDEMPOTENT_WRITE",
     "CLUSTER_ACTION"}
)  # fmt: skip
_STATE_NAMES = {
    "UNKNOWN": "Unknown",
    "PREPARING_REBALANCING": "PreparingRebalance",
    "COMPLETING_REBALANCING": "CompletingRebalance",
    "STABLE": "Stable",
    "DEAD": "Dead",
    "EMPTY": "Empty",
}
_STATE_ENUMS = {name: ConsumerGroupState[key] for key, name in _STATE_NAMES.items()}


# -- configuration ---------------------------------------------------------------------------


def _base_config(settings: Settings) -> dict[str, Any]:
    conf: dict[str, Any] = {
        "bootstrap.servers": settings.bootstrap_servers,
        "client.id": settings.client_id,
        "security.protocol": settings.security_protocol,
        "allow.auto.create.topics": False,
        "socket.timeout.ms": TIMEOUT_MS,
        "metadata.request.timeout.ms": TIMEOUT_MS,
    }
    if settings.security_protocol.startswith("SASL") and settings.sasl_password:
        conf["sasl.mechanism"] = settings.sasl_mechanism
        conf["sasl.username"] = settings.sasl_username
        conf["sasl.password"] = settings.sasl_password.get_secret_value()
    if settings.ssl_ca_location:
        conf["ssl.ca.location"] = settings.ssl_ca_location
    return conf


def build_admin_config(settings: Settings) -> dict[str, Any]:
    return _base_config(settings)


def build_consumer_config(settings: Settings) -> dict[str, Any]:
    """A new throw-away consumer identity every time (`group.id` is never reused)."""
    return {
        **_base_config(settings),
        "group.id": f"{GROUP_PREFIX}{uuid.uuid4()}",
        "enable.auto.commit": False,
        "enable.auto.offset.store": False,
        "auto.offset.reset": "error",
        "enable.partition.eof": False,
    }


def assert_readonly_config(admin_conf: dict[str, Any], consumer_conf: dict[str, Any]) -> list[str]:
    """Violations of the read-only client configuration (ADR-0009 A2); `[]` means OK."""
    problems: list[str] = []
    for kind, conf in (("admin", admin_conf), ("consumer", consumer_conf)):
        if conf.get("allow.auto.create.topics") is not False:
            problems.append(f"{kind}: allow.auto.create.topics must be false (R17)")
    if consumer_conf.get("enable.auto.commit") is not False:
        problems.append("consumer: enable.auto.commit must be false")
    if not str(consumer_conf.get("group.id", "")).startswith(GROUP_PREFIX):
        problems.append(f"consumer: group.id must be a throw-away '{GROUP_PREFIX}*' id")
    for key in ("transactional.id", "enable.idempotence"):
        if key in admin_conf or key in consumer_conf:
            problems.append(f"{key} must not be configured (no write path)")
    return problems


# -- error mapping ------------------------------------------------------------------------------


def to_tool_error(exc: Exception, host: str | None) -> ToolError:
    """Classify a confluent-kafka exception into the contract's error taxonomy."""
    hint = f"kiểm tra VPN/kết nối nội bộ tới {host or 'Kafka'}"
    if isinstance(exc, KafkaException) and exc.args and isinstance(exc.args[0], KafkaError):
        name = exc.args[0].name()
        if name in {"_TIMED_OUT", "REQUEST_TIMED_OUT", "_TIMED_OUT_QUEUE"}:
            return ToolError(
                ErrorCode.UPSTREAM_TIMEOUT, f"Kafka timeout ({name}).", SOURCE, True,
                details={"host": host, "hint": hint, "kafka_error": name},
            )  # fmt: skip
        if name in {"_TRANSPORT", "_ALL_BROKERS_DOWN", "_RESOLVE", "BROKER_NOT_AVAILABLE",
                    "NETWORK_EXCEPTION", "_SSL"}:  # fmt: skip
            return ToolError(
                ErrorCode.UPSTREAM_UNAVAILABLE, f"Kafka broker unavailable ({name}).", SOURCE, True,
                details={"host": host, "hint": hint, "kafka_error": name},
            )  # fmt: skip
        if name in {"_AUTHENTICATION", "SASL_AUTHENTICATION_FAILED", "ILLEGAL_SASL_STATE"}:
            return ToolError(
                ErrorCode.UNAUTHORIZED, f"Kafka authentication failed ({name}).", SOURCE, False,
                details={"kafka_error": name},
            )  # fmt: skip
        if name.endswith("AUTHORIZATION_FAILED"):
            return ToolError(
                ErrorCode.FORBIDDEN, f"Kafka authorization failed ({name}).", SOURCE, False,
                details={"kafka_error": name},
            )  # fmt: skip
        return ToolError(
            ErrorCode.UPSTREAM_ERROR, f"Kafka error ({name}).", SOURCE, True,
            details={"kafka_error": name},
        )  # fmt: skip
    return map_exception_to_tool_error(exc, source=SOURCE, host=host)


# -- the adapter ---------------------------------------------------------------------------------


def _group_state(state: Any) -> str | None:
    return None if state is None else _STATE_NAMES.get(getattr(state, "name", ""), str(state))


class ConfluentKafkaReader:
    """`KafkaReader` over `confluent-kafka` (decision of spike S4, ADR-0009)."""

    def __init__(
        self,
        settings: Settings,
        *,
        admin_factory: Callable[[dict[str, Any]], Any] | None = None,
        consumer_factory: Callable[[dict[str, Any]], Any] | None = None,
    ) -> None:
        self._settings = settings
        self._admin_factory = admin_factory or AdminClient
        self._consumer_factory = consumer_factory or Consumer
        self._admin_client: Any | None = None

    def _admin(self) -> Any:
        if self._admin_client is None:
            self._admin_client = self._admin_factory(build_admin_config(self._settings))
        return self._admin_client

    def _new_consumer(self) -> Any:
        return self._consumer_factory(build_consumer_config(self._settings))

    def close(self) -> None:
        self._admin_client = None  # librdkafka tears the handle down on garbage collection

    # -- metadata ---------------------------------------------------------------------------------

    def list_topics(self) -> list[TopicMeta]:
        # Cluster-wide on purpose: never name a topic in a metadata request (R17).
        metadata = self._admin().list_topics(timeout=TIMEOUT_S)
        topics = []
        for name, topic in sorted(metadata.topics.items()):
            partitions = tuple(
                PartitionMeta(
                    id=int(p.id),
                    leader=None if p.leader is None or p.leader < 0 else int(p.leader),
                    replicas=tuple(int(r) for r in p.replicas),
                    isrs=tuple(int(r) for r in p.isrs),
                )
                for _, p in sorted(topic.partitions.items())
            )
            topics.append(TopicMeta(name, partitions, internal=name.startswith("__")))
        return topics

    def topic_configs(self, topic: str) -> dict[str, str | None]:
        resource = ConfigResource(ConfigResource.Type.TOPIC, topic)
        futures = self._admin().describe_configs([resource], request_timeout=TIMEOUT_S)
        entries = futures[resource].result()
        return {name: entry.value for name, entry in sorted(entries.items())}

    def watermarks(self, topic: str, partitions: list[int]) -> dict[int, Watermarks]:
        consumer = self._new_consumer()
        try:
            out = {}
            for partition in partitions:
                low, high = consumer.get_watermark_offsets(
                    TopicPartition(topic, partition), timeout=TIMEOUT_S, cached=False
                )
                out[partition] = Watermarks(int(low), int(high))
            return out
        finally:
            consumer.close()

    # -- peek: assign + explicit offsets, nothing is ever committed --------------------------------

    def _start_offsets(
        self, consumer: Any, request: PeekRequest, marks: dict[int, Watermarks]
    ) -> dict[int, int]:
        parts = request.partitions
        if request.mode == "latest":
            per_partition = math.ceil(request.limit / len(parts))
            return {p: max(marks[p].low, marks[p].high - per_partition) for p in parts}
        if request.mode == "earliest":
            return {p: marks[p].low for p in parts}
        if request.mode == "offset":
            wanted = request.offset or 0
            return {p: min(max(wanted, marks[p].low), marks[p].high) for p in parts}
        since = request.timestamp_ms or 0
        asked = [TopicPartition(request.topic, p, since) for p in parts]
        found = consumer.offsets_for_times(asked, timeout=TIMEOUT_S)
        return {
            tp.partition: marks[tp.partition].high if tp.offset < 0 else int(tp.offset)
            for tp in found
        }

    def peek(self, request: PeekRequest) -> PeekResult:
        consumer = self._new_consumer()
        try:
            marks = {
                p: Watermarks(
                    *map(
                        int,
                        consumer.get_watermark_offsets(
                            TopicPartition(request.topic, p), timeout=TIMEOUT_S, cached=False
                        ),
                    )
                )
                for p in request.partitions
            }
            starts = self._start_offsets(consumer, request, marks)
            active = {p: s for p, s in starts.items() if s < marks[p].high}
            if not active:
                return PeekResult(())
            consumer.assign(
                [TopicPartition(request.topic, p, s) for p, s in sorted(active.items())]
            )
            available = sum(marks[p].high - s for p, s in active.items())
            want = min(available, request.limit if request.mode != "latest" else available)
            deadline = time.monotonic() + request.timeout_s
            collected: list[RawMessage] = []
            finished: set[int] = set()
            while len(collected) < want and finished != set(active):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                message = consumer.poll(min(_POLL_SLICE_S, remaining))
                if message is None:
                    continue
                error = message.error()
                if error is not None:
                    if error.code() == KafkaError._PARTITION_EOF:
                        continue
                    raise KafkaException(error)
                collected.append(_raw_message(message))
                if message.offset() + 1 >= marks[message.partition()].high:
                    finished.add(message.partition())
            timed_out = len(collected) < want and finished != set(active)
            collected.sort(key=lambda m: (m.timestamp_ms or 0, m.partition, m.offset))
            kept = (
                collected[-request.limit :]
                if request.mode == "latest"
                else collected[: request.limit]
            )
            return PeekResult(tuple(kept), timed_out)
        finally:
            consumer.close()

    # -- consumer groups ----------------------------------------------------------------------

    def list_consumer_groups(self, states: list[str]) -> list[GroupSummary]:
        wanted = {_STATE_ENUMS[s] for s in states if s in _STATE_ENUMS} or None
        result = (
            self._admin().list_consumer_groups(states=wanted, request_timeout=TIMEOUT_S).result()
        )
        if not result.valid and result.errors:
            raise result.errors[0]
        groups = []
        for listing in result.valid:
            kind = getattr(getattr(listing, "type", None), "name", None)
            groups.append(
                GroupSummary(
                    group_id=listing.group_id,
                    state=_group_state(listing.state),
                    is_simple=listing.is_simple_consumer_group,
                    protocol_type=kind.lower() if kind and kind != "UNKNOWN" else None,
                )
            )
        return sorted(groups, key=lambda g: g.group_id)

    def describe_consumer_group(self, group_id: str) -> GroupDetail | None:
        futures = self._admin().describe_consumer_groups([group_id], request_timeout=TIMEOUT_S)
        try:
            description = futures[group_id].result()
        except KafkaException as exc:
            if exc.args and exc.args[0].code() == KafkaError.GROUP_ID_NOT_FOUND:
                return None
            raise
        members = tuple(
            GroupMember(
                member_id=m.member_id,
                client_id=m.client_id,
                host=m.host,
                assignments=tuple(
                    (tp.topic, int(tp.partition)) for tp in m.assignment.topic_partitions
                ),
            )
            for m in description.members
        )
        return GroupDetail(group_id, _group_state(description.state), members)

    def committed_offsets(self, group_id: str) -> dict[tuple[str, int], int]:
        request = ConsumerGroupTopicPartitions(group_id)
        futures = self._admin().list_consumer_group_offsets([request], request_timeout=TIMEOUT_S)
        offsets: dict[tuple[str, int], int] = {}
        for tp in futures[group_id].result().topic_partitions:
            invalid = tp.error is not None or tp.offset is None or tp.offset < 0
            offsets[(tp.topic, int(tp.partition))] = (
                -1 if invalid or tp.offset == _OFFSET_INVALID else int(tp.offset)
            )
        return offsets

    # -- startup assert (ADR-0009 A2) ---------------------------------------------------------

    def verify_readonly(self) -> ReadonlyVerdict:
        settings = self._settings
        problems = assert_readonly_config(
            build_admin_config(settings), build_consumer_config(settings)
        )
        if problems:
            return ReadonlyVerdict(tuple(problems))
        if not settings.sasl_username:
            return ReadonlyVerdict(
                notes=("no SASL principal configured; describe_acls check skipped",)
            )
        principal = f"User:{settings.sasl_username}"
        acl_filter = AclBindingFilter(
            ResourceType.ANY, None, ResourcePatternType.ANY, principal, None,  # type: ignore[arg-type]
            AclOperation.ANY, AclPermissionType.ALLOW,
        )  # fmt: skip
        try:
            bindings = self._admin().describe_acls(acl_filter, request_timeout=TIMEOUT_S).result()
        except Exception as exc:  # noqa: BLE001 - ACL introspection is best effort
            return ReadonlyVerdict(
                notes=(
                    f"describe_acls not available ({type(exc).__name__}); verify that "
                    f"{principal} has only Describe+Read and no Create/Write/Delete/Alter ACL",
                )
            )
        bad = sorted(
            {
                b.operation.name
                for b in bindings
                if b.permission_type == AclPermissionType.ALLOW
                and b.operation.name in _WRITE_CAPABLE_ACL_OPERATIONS
            }
        )
        if bad:
            return ReadonlyVerdict(
                (f"{principal} holds write-capable ACLs: {', '.join(bad)} (ADR-0009 A1)",)
            )
        return ReadonlyVerdict()


def _raw_message(message: Any) -> RawMessage:
    ts_type, ts_ms = message.timestamp()
    kind = {1: "create_time", 2: "log_append_time"}.get(ts_type, "not_available")
    return RawMessage(
        topic=message.topic(),
        partition=int(message.partition()),
        offset=int(message.offset()),
        timestamp_ms=None if kind == "not_available" or ts_ms is None or ts_ms < 0 else int(ts_ms),
        timestamp_type=kind,  # type: ignore[arg-type]
        key=message.key(),
        value=message.value(),
        headers=tuple((str(k), v) for k, v in (message.headers() or [])),
    )


# -- async gateway --------------------------------------------------------------------------------


@dataclass
class CredentialReport:
    ok: bool
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class KafkaClient:
    """Runs a `KafkaReader` on the bounded executor and maps errors to the contract taxonomy."""

    def __init__(
        self,
        settings: Settings,
        *,
        common: CommonSettings | None = None,
        reader: Any | None = None,
        executor: BoundedExecutor | None = None,
    ) -> None:
        self._settings = settings
        self._common = common or CommonSettings()
        self._reader = reader or ConfluentKafkaReader(settings)
        self._executor = executor or BoundedExecutor(max_workers=4)
        self._host = settings.first_host

    async def _run(self, func: Callable[..., Any], *args: Any) -> Any:
        try:
            return await self._executor.run(func, *args, source=SOURCE, host=self._host)
        except ToolError:
            raise
        except Exception as exc:  # noqa: BLE001 - mapped to the contract taxonomy
            raise to_tool_error(exc, self._host) from exc

    async def aclose(self) -> None:
        self._reader.close()
        self._executor.shutdown(wait=False)

    async def list_topics(self) -> list[TopicMeta]:
        return list(await self._run(self._reader.list_topics))

    async def topic_configs(self, topic: str) -> dict[str, str | None]:
        return dict(await self._run(self._reader.topic_configs, topic))

    async def watermarks(self, topic: str, partitions: list[int]) -> dict[int, Watermarks]:
        return dict(await self._run(self._reader.watermarks, topic, partitions))

    async def peek(self, request: PeekRequest) -> PeekResult:
        return await self._run(self._reader.peek, request)  # type: ignore[no-any-return]

    async def list_consumer_groups(self, states: list[str]) -> list[GroupSummary]:
        return list(await self._run(self._reader.list_consumer_groups, states))

    async def describe_consumer_group(self, group_id: str) -> GroupDetail | None:
        return await self._run(self._reader.describe_consumer_group, group_id)  # type: ignore[no-any-return]

    async def committed_offsets(self, group_id: str) -> dict[tuple[str, int], int]:
        return dict(await self._run(self._reader.committed_offsets, group_id))

    # -- startup credential check (ADR-0003 A1, ADR-0009 A2) ----------------------------------

    async def verify_credentials(self) -> CredentialReport:
        try:
            verdict: ReadonlyVerdict = await self._run(self._reader.verify_readonly)
        except ToolError as exc:
            return CredentialReport(False, [f"{exc.code.value}: {exc.message}"])
        if not verdict.ok:
            return CredentialReport(False, list(verdict.violations), list(verdict.notes))
        try:
            await self.list_topics()
        except ToolError as exc:
            hint = exc.details.get("hint")
            reason = f"{exc.code.value}: {exc.message}"
            return CredentialReport(
                False, [f"{reason} ({hint})" if hint else reason], list(verdict.notes)
            )
        return CredentialReport(True, [], list(verdict.notes))

    async def credential_check(self) -> bool:
        """`mcp_common.runtime.serve(credential_check=...)` hook."""
        return (await self.verify_credentials()).ok
