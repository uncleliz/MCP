"""Kafka payloads -> contract item schemas + `Citation` (FR-007/AC-001, FR-015).

Pure functions, no I/O, and no Kafka client import (they work on `ports` types). Kafka has no
web URL, so `Citation.uri` is `None` and the `locator` carries topic/partition/offset (offsets
are *strings*: they can exceed 2^53). Free text arrives already redacted/wrapped/budgeted.
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from typing import Any

from mcp_common.envelope import Citation, SourceType

from mcp_kafka.ports import GroupDetail, GroupMember, GroupSummary, TopicMeta, Watermarks

__all__ = [
    "decode_bytes",
    "iso_ms",
    "map_consumer_group",
    "map_group_detail",
    "map_group_member",
    "map_message",
    "map_topic",
    "map_topic_detail",
]


def _cite(label: str, locator: dict[str, Any]) -> Citation:
    return Citation(
        source_type=SourceType.KAFKA,
        label=label[:512],
        uri=None,
        locator=locator,
        retrieved_at=datetime.now(UTC),
    )


def iso_ms(value: int | None) -> str | None:
    return None if value is None else datetime.fromtimestamp(value / 1000, UTC).isoformat()


def decode_bytes(raw: bytes | None, fmt: str = "auto") -> tuple[str | None, str]:
    """bytes -> `(text, kind)`; `kind` is `json` | `utf8` | `base64` | `none` (tombstone).

    `auto` picks `json` when the UTF-8 text parses as JSON, else `utf8`, else `base64`;
    `json` falls back to `utf8`/`base64` when the payload is not valid JSON.
    """
    if raw is None:
        return None, "none"
    if fmt != "base64":
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = None
        if text is not None:
            if fmt in ("auto", "json"):
                try:
                    json.loads(text)
                    return text, "json"
                except ValueError:
                    pass
            return text, "utf8"
    return base64.b64encode(raw).decode("ascii"), "base64"


def map_topic(topic: TopicMeta, *, citation_ref: int) -> tuple[dict[str, Any], Citation]:
    item = {
        "topic": topic.name,
        "partition_count": max(len(topic.partitions), 1),
        "replication_factor": topic.replication_factor,
        "internal": topic.internal,
        "citation_ref": citation_ref,
    }
    return item, _cite(
        f"topic {topic.name} ({len(topic.partitions)} partitions)", {"topic": topic.name}
    )


def map_topic_detail(
    topic: TopicMeta,
    marks: dict[int, Watermarks],
    configs: dict[str, str | None] | None,
) -> tuple[dict[str, Any], Citation]:
    partitions = []
    for part in topic.partitions:
        mark = marks[part.id]
        partitions.append(
            {
                "partition": part.id,
                "leader": part.leader,
                "replicas": list(part.replicas),
                "isrs": list(part.isrs),
                "low_watermark": str(mark.low),
                "high_watermark": str(mark.high),
                "approx_message_count": str(mark.count),
            }
        )
    total = sum(marks[p.id].count for p in topic.partitions)
    item = {
        "topic": topic.name,
        "internal": topic.internal,
        "partitions": partitions,
        "configs": configs,
        "approx_message_count": str(total),
        "citation_ref": 0,
    }
    lows = [marks[p.id].low for p in topic.partitions]
    highs = [marks[p.id].high for p in topic.partitions]
    first = partitions[0] if partitions else {}
    return item, _cite(
        f"topic {topic.name} ({len(partitions)} partitions, offsets "
        f"{min(lows, default=0)}–{max(highs, default=0)})",
        {
            "topic": topic.name,
            "partition": first.get("partition"),
            "low_watermark": first.get("low_watermark"),
            "high_watermark": first.get("high_watermark"),
            "partitions": {
                str(p["partition"]): [p["low_watermark"], p["high_watermark"]] for p in partitions
            },
        },
    )


def map_message(
    *,
    topic: str,
    partition: int,
    offset: int,
    timestamp_ms: int | None,
    timestamp_type: str,
    key: str | None,
    value: str | None,
    value_format: str,
    headers: dict[str, str | None],
    size_bytes: int | None,
    truncated: bool,
    citation_ref: int,
) -> tuple[dict[str, Any], Citation]:
    stamp = iso_ms(timestamp_ms)
    item = {
        "topic": topic,
        "partition": partition,
        "offset": str(offset),
        "timestamp": stamp,
        "timestamp_type": timestamp_type,
        "key": key,
        "value": value,
        "value_format": value_format,
        "headers": headers,
        "size_bytes": size_bytes,
        "truncated": truncated,
        "citation_ref": citation_ref,
    }
    label = f"{topic}[{partition}]@{offset}" + (f" @ {stamp}" if stamp else "")
    return item, _cite(label, {"topic": topic, "partition": partition, "offset": str(offset)})


def map_consumer_group(
    group: GroupSummary, *, citation_ref: int
) -> tuple[dict[str, Any], Citation]:
    item = {
        "group_id": group.group_id,
        "state": group.state,
        "is_simple_consumer_group": group.is_simple,
        "protocol_type": group.protocol_type,
        "citation_ref": citation_ref,
    }
    state = f" ({group.state})" if group.state else ""
    return item, _cite(f"consumer group {group.group_id}{state}", {"group_id": group.group_id})


def map_group_member(member: GroupMember) -> dict[str, Any]:
    return {
        "member_id": member.member_id,
        "client_id": member.client_id,
        "host": member.host,
        "assignments": [{"topic": t, "partition": p} for t, p in member.assignments],
    }


def map_group_detail(
    detail: GroupDetail,
    *,
    partition_offsets: list[dict[str, Any]],
    total_lag: int,
    include_members: bool,
) -> tuple[dict[str, Any], Citation]:
    item = {
        "group_id": detail.group_id,
        "state": detail.state,
        "members": [map_group_member(m) for m in detail.members] if include_members else None,
        "partition_offsets": partition_offsets,
        "total_lag": total_lag,
        "citation_ref": 0,
    }
    return item, _cite(
        f"consumer group {detail.group_id} lag={total_lag}", {"group_id": detail.group_id}
    )
