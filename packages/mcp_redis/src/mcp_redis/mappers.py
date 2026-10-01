"""Redis replies -> contract item schemas + `Citation` (FR-008/AC-001, FR-015).

Pure functions, no I/O. Redis keys have no URL, so `Citation.uri` is always `None` and the
`locator` carries `{key, db}` (contract invariant 6).
"""

from __future__ import annotations

import base64
import re
from datetime import UTC, datetime
from typing import Any

from mcp_common.envelope import Citation, SourceType

__all__ = [
    "decode_value",
    "info_section_to_map",
    "is_sensitive_field",
    "key_citation",
    "map_key_info",
    "map_key_summary",
    "map_key_value",
    "map_server_info",
    "text",
    "ttl_to_seconds",
]

_SENSITIVE_FIELD = re.compile(
    r"pass(word|wd)?|secret|token|api[_-]?key|credential|authorization|private[_-]?key",
    re.IGNORECASE,
)
_UTF8_TAIL_TRIM = 3


def text(value: Any) -> str:
    """bytes/str/number -> str (keys are shown with replacement characters if not UTF-8)."""
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value)


def is_sensitive_field(name: str) -> bool:
    return bool(_SENSITIVE_FIELD.search(name))


def ttl_to_seconds(ttl: Any) -> int | None:
    """`TTL` reply: -1 = persistent (None); -2 = missing (caller handles before)."""
    value = int(ttl)
    return None if value < 0 else value


def decode_value(raw: bytes, fmt: str = "auto", *, partial: bool = False) -> tuple[str, str]:
    """bytes -> `(text, kind)`; `kind` is `json` | `utf8` | `base64`.

    `auto`/`json`/`utf8` return text when the bytes are valid UTF-8 (JSON is only a label
    when the text parses as a JSON object/array); otherwise — and for `base64` — the bytes
    come back as `base64:<...>`. `partial=True` tolerates a multi-byte character cut in
    half at the end (the value was truncated by `GETRANGE`).
    """
    import json

    if fmt != "base64":
        for trim in range(_UTF8_TAIL_TRIM + 1 if partial else 1):
            chunk = raw[: len(raw) - trim] if trim else raw
            try:
                decoded = chunk.decode("utf-8")
            except UnicodeDecodeError:
                continue
            if fmt in ("auto", "json"):
                try:
                    if isinstance(json.loads(decoded), dict | list):
                        return decoded, "json"
                except ValueError:
                    pass
            return decoded, "utf8"
    return "base64:" + base64.b64encode(raw).decode("ascii"), "base64"


def key_citation(key: str, db: int, label: str) -> Citation:
    return Citation(
        source_type=SourceType.REDIS,
        label=label[:512],
        uri=None,
        locator={"key": key, "db": db},
        retrieved_at=datetime.now(UTC),
    )


def map_key_summary(
    key: str, key_type: str, ttl_s: int | None, db: int, *, citation_ref: int
) -> tuple[dict[str, Any], Citation]:
    item = {"key": key, "type": key_type, "ttl_s": ttl_s, "db": db, "citation_ref": citation_ref}
    return item, key_citation(key, db, f"key {key} ({key_type}, db {db})")


def map_key_value(
    *,
    key: str,
    key_type: str,
    ttl_s: int | None,
    db: int,
    encoding: str | None,
    memory_usage_bytes: int | None,
    element_count: int | None,
    value: Any,
    truncated: bool,
    redactions: int,
    citation_ref: int,
) -> tuple[dict[str, Any], Citation]:
    item = {
        "key": key,
        "type": key_type,
        "ttl_s": ttl_s,
        "db": db,
        "encoding": encoding,
        "memory_usage_bytes": memory_usage_bytes,
        "element_count": element_count,
        "value": value,
        "truncated": truncated,
        "redactions": redactions,
        "citation_ref": citation_ref,
    }
    return item, key_citation(key, db, f"key {key} ({key_type}, db {db})")


def map_key_info(
    *,
    key: str,
    key_type: str,
    ttl_s: int | None,
    db: int,
    encoding: str | None,
    memory_usage_bytes: int | None,
    length: int | None,
    citation_ref: int,
) -> tuple[dict[str, Any], Citation]:
    item = {
        "key": key,
        "exists": True,
        "type": key_type,
        "ttl_s": ttl_s,
        "db": db,
        "encoding": encoding,
        "memory_usage_bytes": memory_usage_bytes,
        "length": length,
        "citation_ref": citation_ref,
    }
    suffix = f"TTL {ttl_s}s" if ttl_s is not None else "no TTL"
    return item, key_citation(key, db, f"key {key} ({key_type}, {suffix})")


def info_section_to_map(raw: Any) -> dict[str, str | None]:
    """`INFO <section>` reply (redis-py parses it to a dict) -> `{field: str}`."""
    if not isinstance(raw, dict):
        return {}
    flat: dict[str, str | None] = {}
    for name, value in raw.items():
        if value is None:
            flat[text(name)] = None
        elif isinstance(value, dict):
            flat[text(name)] = ",".join(f"{text(k)}={text(v)}" for k, v in value.items())
        else:
            flat[text(name)] = text(value)
    return flat


def map_server_info(
    *,
    sections: dict[str, dict[str, str | None]],
    requested: list[str],
    dbsize: int,
    acl_user: str | None,
    readonly_confirmed: bool | None,
) -> tuple[dict[str, Any], Citation]:
    item = {
        "sections": sections,
        "dbsize": dbsize,
        "acl_user": acl_user,
        "readonly_confirmed": readonly_confirmed,
        "citation_ref": 0,
    }
    who = acl_user or "unknown user"
    mode = {True: "read-only", False: "NOT read-only", None: "read-only unverified"}[
        readonly_confirmed
    ]
    citation = Citation(
        source_type=SourceType.REDIS,
        label=f"redis server info (user {who}, {mode})",
        uri=None,
        locator={"sections": requested},
        retrieved_at=datetime.now(UTC),
    )
    return item, citation
