"""Kafka tool layer: input bounds, port calls, decoding, redaction, byte budget.

Depends on the `KafkaClient` gateway and the `ports` types only — no Kafka client library —
so changing the adapter does not touch this module (R7, TC-028).
"""

from __future__ import annotations

import fnmatch
from datetime import datetime
from typing import Any

from mcp_common.config import CommonSettings
from mcp_common.envelope import Citation, SourceType
from mcp_common.errors import ToolError
from mcp_common.runtime import tool_deadline_for
from mcp_common.tooling import (
    CallState,
    ToolOutcome,
    build_result,
    effective_max_bytes,
    invalid_input,
    not_found_result,
)

from mcp_kafka import mappers
from mcp_kafka.client import SOURCE, KafkaClient
from mcp_kafka.ports import PeekRequest, TopicMeta

__all__ = ["KafkaReadApi"]

_FROM_MODES = ("latest", "earliest", "offset", "timestamp")
_VALUE_FORMATS = ("auto", "json", "utf8", "base64")
_GROUP_STATES = ("PreparingRebalance", "CompletingRebalance", "Stable", "Dead", "Empty", "Unknown")
_MAX_PARTITIONS_DESCRIBED = 256
_MAX_OFFSET_ROWS = 500
_APPROX_WARNING = "approx_message_count là xấp xỉ (không trừ retention/compaction)"
_READONLY_WARNING = "read-only consumer: assign() + no commit, group.id dùng-một-lần"


def _digits(value: str) -> bool:
    return value.isascii() and value.isdigit() and 1 <= len(value) <= 19


class KafkaReadApi:
    def __init__(self, client: KafkaClient, common: CommonSettings) -> None:
        self._client = client
        self._common = common

    # -- validation helpers ------------------------------------------------------------

    @staticmethod
    def _limit(limit: int, high: int = 100) -> None:
        if not 1 <= limit <= high:
            raise invalid_input("limit", f"phải nằm trong khoảng 1..{high}", SOURCE)

    @staticmethod
    def _name(value: str, field: str, high: int) -> None:
        if not 1 <= len(value) <= high:
            raise invalid_input(field, f"độ dài phải từ 1 đến {high} ký tự", SOURCE)

    def _state(self, budget: int | None = None, warnings: list[str] | None = None) -> CallState:
        return CallState(
            budget if budget is not None else self._common.max_output_bytes,
            warnings,
            redact_disabled=self._common.redact_disabled,
        )

    @staticmethod
    def _not_found(call: CallState, echo: dict[str, Any], identifier: str) -> ToolOutcome:
        result = not_found_result(SourceType.KAFKA, started=call.started, query_echo=echo)
        return ToolOutcome(result, identifier=identifier)

    async def _find_topic(self, topic: str) -> TopicMeta | None:
        # Cluster-wide metadata + client-side filter; never a per-topic request (R17).
        return next((t for t in await self._client.list_topics() if t.name == topic), None)

    # -- kafka_list_topics ---------------------------------------------------------------

    async def list_topics(
        self, *, pattern: str = "*", include_internal: bool = False, limit: int = 20
    ) -> ToolOutcome:
        self._limit(limit)
        if len(pattern) > 256:
            raise invalid_input("pattern", "độ dài tối đa 256 ký tự", SOURCE)
        call = self._state()
        topics = [
            t
            for t in await self._client.list_topics()
            if fnmatch.fnmatchcase(t.name, pattern) and (include_internal or not t.internal)
        ]
        kept = topics[:limit]
        if len(topics) > limit:
            call.warn(f"còn {len(topics) - limit} topic khớp nữa; thu hẹp pattern hoặc tăng limit")
            call.truncated_elsewhere = True
        items: list[dict[str, Any]] = []
        citations: list[Citation] = []
        for topic in kept:
            item, citation = mappers.map_topic(topic, citation_ref=len(citations))
            items.append(item)
            citations.append(citation)
        result = build_result(
            SourceType.KAFKA, items, citations, started=call.started,
            query_echo={"pattern": pattern, "include_internal": include_internal, "limit": limit},
            truncated=call.truncated, warnings=call.warnings,
        )  # fmt: skip
        return ToolOutcome(result, query_description=f"topic khớp '{pattern}'")

    # -- kafka_describe_topic ---------------------------------------------------------------

    async def describe_topic(self, *, topic: str, include_configs: bool = True) -> ToolOutcome:
        self._name(topic, "topic", 249)
        call = self._state()
        echo = {"topic": topic, "include_configs": include_configs}
        meta = await self._find_topic(topic)
        if meta is None:
            return self._not_found(call, echo, f"Topic '{topic}'")
        if len(meta.partitions) > _MAX_PARTITIONS_DESCRIBED:
            call.warn(
                f"chỉ mô tả {_MAX_PARTITIONS_DESCRIBED} partition đầu của {len(meta.partitions)}"
            )
            call.truncated_elsewhere = True
            meta = TopicMeta(meta.name, meta.partitions[:_MAX_PARTITIONS_DESCRIBED], meta.internal)
        marks = await self._client.watermarks(topic, [p.id for p in meta.partitions])
        configs: dict[str, str | None] | None = None
        if include_configs:
            raw = await self._client.topic_configs(topic)
            configs = {k: (call.plain(v) if v else v) for k, v in raw.items()}
        item, citation = mappers.map_topic_detail(meta, marks, configs)
        call.warn(_APPROX_WARNING)
        result = build_result(
            SourceType.KAFKA, [item], [citation], started=call.started, query_echo=echo,
            truncated=call.truncated, warnings=call.warnings, redactions=call.counter.count,
        )  # fmt: skip
        return ToolOutcome(result, identifier=f"Topic '{topic}'")

    # -- kafka_peek_messages ----------------------------------------------------------------------

    async def peek_messages(
        self,
        *,
        topic: str,
        partition: int | None = None,
        from_: str = "latest",
        offset: str | None = None,
        timestamp: datetime | None = None,
        limit: int = 10,
        value_format: str = "auto",
        max_bytes: int = 65536,
        timeout_s: int = 20,
    ) -> ToolOutcome:
        self._name(topic, "topic", 249)
        if partition is not None and partition < 0:
            raise invalid_input("partition", "phải >= 0", SOURCE)
        if from_ not in _FROM_MODES:
            raise invalid_input("from", f"phải thuộc {list(_FROM_MODES)}", SOURCE)
        self._limit(limit)
        if value_format not in _VALUE_FORMATS:
            raise invalid_input("value_format", f"phải thuộc {list(_VALUE_FORMATS)}", SOURCE)
        if not 1024 <= max_bytes <= 131072:
            raise invalid_input("max_bytes", "phải nằm trong khoảng 1024..131072", SOURCE)
        if not 1 <= timeout_s <= 22:
            raise invalid_input("timeout_s", "phải nằm trong khoảng 1..22", SOURCE)
        deadline = tool_deadline_for("kafka_peek_messages", self._common)
        if timeout_s >= deadline:
            raise invalid_input(
                "timeout_s", f"phải nhỏ hơn deadline của tool ({deadline:g}s)", SOURCE
            )
        start_offset: int | None = None
        start_ms: int | None = None
        if from_ == "offset":
            if offset is None or not _digits(offset):
                raise invalid_input("offset", "bắt buộc khi from=offset, dạng chuỗi số", SOURCE)
            start_offset = int(offset)
        if from_ == "timestamp":
            if timestamp is None:
                raise invalid_input("timestamp", "bắt buộc khi from=timestamp", SOURCE)
            if timestamp.tzinfo is None:
                raise invalid_input("timestamp", "thiếu timezone (ví dụ hậu tố Z)", SOURCE)
            start_ms = int(timestamp.timestamp() * 1000)

        budget, clamp_warnings = effective_max_bytes(max_bytes, self._common)
        call = self._state(budget, clamp_warnings)
        echo: dict[str, Any] = {"topic": topic, "partition": partition, "from": from_,
                                "limit": limit, "value_format": value_format}  # fmt: skip
        meta = await self._find_topic(topic)
        if meta is None:
            return self._not_found(call, echo, f"Topic '{topic}'")
        known = [p.id for p in meta.partitions]
        if partition is not None and partition not in known:
            raise invalid_input(
                "partition", f"topic chỉ có {len(known)} partition (0..{len(known) - 1})", SOURCE
            )
        partitions = (partition,) if partition is not None else tuple(known)
        outcome = await self._client.peek(
            PeekRequest(
                topic=topic, partitions=partitions, mode=from_,  # type: ignore[arg-type]
                limit=limit, timeout_s=float(timeout_s), offset=start_offset,
                timestamp_ms=start_ms,
            )
        )  # fmt: skip

        items: list[dict[str, Any]] = []
        citations: list[Citation] = []
        for raw in outcome.messages:
            if call.budget.exhausted:
                call.truncated_elsewhere = True
                call.warn("đã đạt max_bytes; giảm limit hoặc tăng max_bytes")
                break
            already_cut = call.budget.truncated
            text, kind = mappers.decode_bytes(raw.value, value_format)
            content_id = f"{raw.topic}[{raw.partition}]@{raw.offset}"
            value = call.text(text, SOURCE, content_id) if text else text
            key_text, _ = mappers.decode_bytes(raw.key, "utf8")
            headers = {
                k: (mappers.decode_bytes(v, "utf8")[0] if v is not None else None)
                for k, v in raw.headers
            }
            item, citation = mappers.map_message(
                topic=raw.topic, partition=raw.partition, offset=raw.offset,
                timestamp_ms=raw.timestamp_ms, timestamp_type=raw.timestamp_type,
                key=call.plain(key_text) if key_text else key_text, value=value,
                value_format=kind,
                headers={k: (call.plain(v) if v else v) for k, v in headers.items()},
                size_bytes=None if raw.value is None else len(raw.value),
                truncated=call.budget.truncated and not already_cut,
                citation_ref=len(citations),
            )  # fmt: skip
            items.append(item)
            citations.append(citation)
        if outcome.timed_out:
            call.truncated_elsewhere = True
            call.warn(f"hết timeout_s={timeout_s}s trước khi đọc đủ message")
        if not items:
            call.warn("topic rỗng hoặc không có message ở vị trí yêu cầu trong timeout")
        call.warn(_READONLY_WARNING)
        result = build_result(
            SourceType.KAFKA, items, citations, started=call.started, query_echo=echo,
            truncated=call.truncated, warnings=call.warnings, redactions=call.counter.count,
        )  # fmt: skip
        return ToolOutcome(result, query_description=f"message trong topic '{topic}'")

    # -- kafka_list_consumer_groups -----------------------------------------------------------

    async def list_consumer_groups(
        self, *, pattern: str = "*", states: list[str] | None = None, limit: int = 20
    ) -> ToolOutcome:
        states = list(states or [])
        self._limit(limit)
        if len(pattern) > 256:
            raise invalid_input("pattern", "độ dài tối đa 256 ký tự", SOURCE)
        if len(states) > 6 or any(s not in _GROUP_STATES for s in states):
            raise invalid_input("states", f"tối đa 6 giá trị thuộc {list(_GROUP_STATES)}", SOURCE)
        call = self._state()
        groups = [
            g
            for g in await self._client.list_consumer_groups(states)
            if fnmatch.fnmatchcase(g.group_id, pattern)
        ]
        if len(groups) > limit:
            call.warn(f"còn {len(groups) - limit} group khớp nữa; thu hẹp pattern hoặc tăng limit")
            call.truncated_elsewhere = True
        items: list[dict[str, Any]] = []
        citations: list[Citation] = []
        for group in groups[:limit]:
            item, citation = mappers.map_consumer_group(group, citation_ref=len(citations))
            items.append(item)
            citations.append(citation)
        result = build_result(
            SourceType.KAFKA, items, citations, started=call.started,
            query_echo={"pattern": pattern, "states": states, "limit": limit},
            truncated=call.truncated, warnings=call.warnings,
        )  # fmt: skip
        return ToolOutcome(result, query_description=f"consumer group khớp '{pattern}'")

    # -- kafka_describe_consumer_group --------------------------------------------------------

    async def describe_consumer_group(
        self, *, group_id: str, include_members: bool = True
    ) -> ToolOutcome:
        self._name(group_id, "group_id", 256)
        call = self._state()
        echo = {"group_id": group_id, "include_members": include_members}
        detail = await self._client.describe_consumer_group(group_id)
        if detail is None:
            return self._not_found(call, echo, f"Consumer group '{group_id}'")
        committed = await self._client.committed_offsets(group_id)
        if detail.state == "Dead" and not detail.members and not committed:
            return self._not_found(call, echo, f"Consumer group '{group_id}'")

        by_topic: dict[str, list[int]] = {}
        for topic, partition in sorted(committed):
            by_topic.setdefault(topic, []).append(partition)
        rows: list[dict[str, Any]] = []
        for topic, partitions in by_topic.items():
            try:
                marks = await self._client.watermarks(topic, partitions)
            except ToolError:
                call.warn(f"không đọc được watermark của topic '{topic}' (đã xoá?); bỏ qua lag")
                continue
            for partition in partitions:
                done, mark = committed[(topic, partition)], marks[partition]
                lag = max(mark.high - done, 0) if done >= 0 else mark.count
                rows.append(
                    {"topic": topic, "partition": partition, "committed_offset": str(done),
                     "high_watermark": str(mark.high), "lag": lag}
                )  # fmt: skip
        if len(rows) > _MAX_OFFSET_ROWS:
            call.warn(f"chỉ trả {_MAX_OFFSET_ROWS} partition đầu của {len(rows)}")
            call.truncated_elsewhere = True
        total_lag = sum(int(r["lag"]) for r in rows)
        item, citation = mappers.map_group_detail(
            detail, partition_offsets=rows[:_MAX_OFFSET_ROWS], total_lag=total_lag,
            include_members=include_members,
        )  # fmt: skip
        result = build_result(
            SourceType.KAFKA, [item], [citation], started=call.started, query_echo=echo,
            truncated=call.truncated, warnings=call.warnings,
        )  # fmt: skip
        return ToolOutcome(result, identifier=f"Consumer group '{group_id}'")
