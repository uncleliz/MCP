"""The `KafkaReader` port (ADR-0009 Risks, R7): everything the tool layer needs from Kafka.

`read_api.py`, `mappers.py` and `tools.py` depend on this module only — never on a client
library — so swapping `confluent-kafka` for `kafka-python` means writing one new adapter
(implementing :class:`KafkaReader`) and nothing in the tool layer changes. The port is
deliberately read-only: there is no method that creates, writes, commits or deletes.

All methods are synchronous (both candidate libraries are); `client.py` runs them on the
bounded executor (ADR-0006 A1).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol

__all__ = [
    "GroupDetail",
    "GroupMember",
    "GroupSummary",
    "KafkaReader",
    "PartitionMeta",
    "PeekRequest",
    "PeekResult",
    "RawMessage",
    "ReadonlyVerdict",
    "TopicMeta",
    "Watermarks",
]

PeekMode = Literal["latest", "earliest", "offset", "timestamp"]


@dataclass(frozen=True)
class PartitionMeta:
    id: int
    leader: int | None
    replicas: tuple[int, ...] = ()
    isrs: tuple[int, ...] = ()


@dataclass(frozen=True)
class TopicMeta:
    name: str
    partitions: tuple[PartitionMeta, ...]
    internal: bool = False

    @property
    def replication_factor(self) -> int | None:
        return len(self.partitions[0].replicas) or None if self.partitions else None


@dataclass(frozen=True)
class Watermarks:
    low: int
    high: int

    @property
    def count(self) -> int:
        return max(self.high - self.low, 0)


@dataclass(frozen=True)
class RawMessage:
    topic: str
    partition: int
    offset: int
    timestamp_ms: int | None
    timestamp_type: Literal["create_time", "log_append_time", "not_available"]
    key: bytes | None
    value: bytes | None
    headers: tuple[tuple[str, bytes | None], ...] = ()


@dataclass(frozen=True)
class PeekRequest:
    topic: str
    partitions: tuple[int, ...]
    mode: PeekMode
    limit: int
    timeout_s: float
    offset: int | None = None
    timestamp_ms: int | None = None


@dataclass(frozen=True)
class PeekResult:
    messages: tuple[RawMessage, ...]
    timed_out: bool = False


@dataclass(frozen=True)
class GroupSummary:
    group_id: str
    state: str | None
    is_simple: bool | None
    protocol_type: str | None = None


@dataclass(frozen=True)
class GroupMember:
    member_id: str
    client_id: str | None
    host: str | None
    assignments: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True)
class GroupDetail:
    group_id: str
    state: str | None
    members: tuple[GroupMember, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ReadonlyVerdict:
    """Result of the startup read-only assertion (ADR-0009 A2)."""

    violations: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.violations


class KafkaReader(Protocol):
    def list_topics(self) -> list[TopicMeta]:
        """Cluster-wide topic metadata. Never a per-topic metadata request (R17)."""

    def topic_configs(self, topic: str) -> dict[str, str | None]: ...

    def watermarks(self, topic: str, partitions: list[int]) -> dict[int, Watermarks]: ...

    def peek(self, request: PeekRequest) -> PeekResult:
        """Read messages with manual assignment and no offset commit."""

    def list_consumer_groups(self, states: list[str]) -> list[GroupSummary]: ...

    def describe_consumer_group(self, group_id: str) -> GroupDetail | None:
        """None when the group does not exist."""

    def committed_offsets(self, group_id: str) -> dict[tuple[str, int], int]:
        """Committed offsets of a group (read-only); -1 means none."""

    def verify_readonly(self) -> ReadonlyVerdict: ...

    def close(self) -> None: ...
