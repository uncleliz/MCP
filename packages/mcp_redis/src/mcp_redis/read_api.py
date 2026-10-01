"""Redis tool layer: input bounds, typed reads, redaction, byte budget, `SCAN` paging.

Only commands in `client.ALLOWED_COMMANDS` are ever sent (the client enforces it); values
are read with bounded commands (`GETRANGE`/`HSCAN`/`SSCAN`/`LRANGE`/`ZRANGE`/`XRANGE`)
so a huge key can never be pulled whole into memory or context.
"""

from __future__ import annotations

import json
from typing import Any

from mcp_common.config import CommonSettings
from mcp_common.envelope import Citation, SourceType
from mcp_common.errors import ToolError
from mcp_common.tooling import (
    CallState,
    ToolOutcome,
    build_result,
    decode_cursor,
    effective_max_bytes,
    encode_cursor,
    invalid_input,
    not_found_result,
)

from mcp_redis import mappers
from mcp_redis.client import SOURCE, RedisClient, acl_write_grants
from mcp_redis.mappers import decode_value, is_sensitive_field, text

__all__ = ["RedisReadApi", "decode_value"]

_KEY_TYPES = ("string", "list", "set", "zset", "hash", "stream")
_INFO_SECTIONS = ("server", "clients", "memory", "persistence", "stats", "replication", "keyspace")
_MAX_SCAN_ROUNDS = 20
_SCAN_COUNT = 100
_MAX_PENDING_KEYS = 40
_LENGTH_COMMAND = {
    "string": "STRLEN",
    "hash": "HLEN",
    "list": "LLEN",
    "set": "SCARD",
    "zset": "ZCARD",
    "stream": "XLEN",
}
_REDACTED_FIELD = "«redacted:field»"
_RESP_PAIR = 2


def _pairs(reply: Any) -> list[tuple[Any, Any]]:
    """ZRANGE ... WITHSCORES reply: flat `[m, s, ...]` (RESP2) or `[(m, s), ...]`."""
    items = list(reply or [])
    if items and isinstance(items[0], list | tuple):
        return [(pair[0], pair[1]) for pair in items]
    return list(zip(items[0::2], items[1::2], strict=False))


def _truthy_int(value: Any) -> int | None:
    return None if value is None else int(value)


class RedisReadApi:
    def __init__(self, client: RedisClient, common: CommonSettings) -> None:
        self._client = client
        self._common = common

    # -- validation helpers ------------------------------------------------------------

    @staticmethod
    def _db(db: int) -> None:
        if not 0 <= db <= 15:
            raise invalid_input("db", "phải nằm trong khoảng 0..15", SOURCE)

    @staticmethod
    def _key(key: str) -> None:
        if not 1 <= len(key) <= 1024:
            raise invalid_input("key", "độ dài phải từ 1 đến 1024 ký tự", SOURCE)

    def _state(self, budget: int | None = None, warnings: list[str] | None = None) -> CallState:
        return CallState(
            budget if budget is not None else self._common.max_output_bytes,
            warnings,
            redact_disabled=self._common.redact_disabled,
        )

    def _redact(self, value: Any, call: CallState, *, key: str | None = None) -> Any:
        """Field-name + pattern redaction of a decoded value tree (R4, ADR-0015)."""
        if isinstance(value, dict):
            return {k: self._redact(v, call, key=str(k)) for k, v in value.items()}
        if isinstance(value, list):
            return [self._redact(v, call, key=key) for v in value]
        if isinstance(value, str):
            if key is not None and is_sensitive_field(key) and not self._common.redact_disabled:
                call.counter.count += 1
                return _REDACTED_FIELD
            return call.plain(value)
        if key is not None and is_sensitive_field(key) and not self._common.redact_disabled:
            call.counter.count += 1
            return _REDACTED_FIELD
        return value

    def _denied_outcome(self, key: str, db: int, started_call: CallState) -> ToolOutcome:
        result = not_found_result(
            SourceType.REDIS, started=started_call.started, query_echo={"key": key, "db": db}
        )
        result.meta.warnings.append("key khớp MCP_REDIS_KEY_DENY (policy) nên không được đọc")
        return ToolOutcome(result, identifier=f"Key '{key}'")

    def _missing_outcome(self, key: str, db: int, call: CallState) -> ToolOutcome:
        result = not_found_result(
            SourceType.REDIS, started=call.started, query_echo={"key": key, "db": db}
        )
        result.meta.warnings.append("key does not exist (nil)")
        return ToolOutcome(result, identifier=f"Key '{key}'")

    # -- redis_scan_keys ------------------------------------------------------------------

    async def scan_keys(
        self,
        *,
        pattern: str = "*",
        type_filter: str | None = None,
        db: int = 0,
        limit: int = 20,
        cursor: str | None = None,
    ) -> ToolOutcome:
        if not 1 <= limit <= 100:
            raise invalid_input("limit", "phải nằm trong khoảng 1..100", SOURCE)
        if not 1 <= len(pattern) <= 512:
            raise invalid_input("pattern", "độ dài phải từ 1 đến 512 ký tự", SOURCE)
        if type_filter is not None and type_filter not in _KEY_TYPES:
            raise invalid_input("type_filter", f"phải thuộc {list(_KEY_TYPES)}", SOURCE)
        self._db(db)
        fingerprint = f"{db}|{pattern}|{type_filter}"
        state = decode_cursor(cursor, source=SOURCE)
        if state and state.get("f") != fingerprint:
            raise invalid_input(
                "cursor", "cursor thuộc một truy vấn khác; dùng đúng next_cursor", SOURCE
            )
        position = state.get("c", 0)
        pending = [str(k) for k in state.get("p", [])]
        if not isinstance(position, int) or position < 0:
            raise invalid_input("cursor", "cursor không hợp lệ; dùng đúng meta.next_cursor", SOURCE)

        call = self._state()
        keys: list[str] = list(pending)
        seen = set(keys)
        denied = 0
        rounds = 0
        first = not cursor
        while len(keys) < limit and (first or position != 0) and rounds < _MAX_SCAN_ROUNDS:
            first = False
            rounds += 1
            args: list[Any] = ["SCAN", position, "MATCH", pattern, "COUNT", _SCAN_COUNT]
            if type_filter:
                args += ["TYPE", type_filter]
            reply = await self._client.execute(*args, db=db)
            position, batch = int(reply[0]), reply[1]
            for raw_key in batch:
                name = text(raw_key)
                if name in seen:
                    continue
                seen.add(name)
                if self._client.is_key_denied(name):
                    denied += 1
                    continue
                keys.append(name)
        returned, leftover = keys[:limit], keys[limit:]
        if len(leftover) > _MAX_PENDING_KEYS:
            call.warn(
                f"{len(leftover) - _MAX_PENDING_KEYS} key vượt giới hạn cursor đã bị bỏ qua; "
                "thu hẹp pattern để chắc chắn thấy đủ"
            )
            leftover = leftover[:_MAX_PENDING_KEYS]
        if denied:
            call.warn(f"{denied} key bị loại bởi MCP_REDIS_KEY_DENY")
        has_more = position != 0 or bool(leftover)
        if position != 0 and rounds >= _MAX_SCAN_ROUNDS and len(keys) < limit:
            call.warn("đã quét nhiều vòng mà chưa đủ key; dùng next_cursor để quét tiếp")
        next_cursor = (
            encode_cursor({"c": position, "p": leftover, "f": fingerprint}) if has_more else None
        )

        items: list[dict[str, Any]] = []
        citations: list[Citation] = []
        if returned:
            commands: list[tuple[Any, ...]] = []
            for name in returned:
                commands += [("TYPE", name), ("TTL", name)]
            replies = await self._client.execute_many(commands, db=db)
            for index, name in enumerate(returned):
                key_type = text(replies[2 * index])
                ttl = int(replies[2 * index + 1])
                if key_type == "none" or ttl == -2:
                    continue  # deleted/expired between SCAN and TYPE
                item, citation = mappers.map_key_summary(
                    name,
                    key_type if key_type in _KEY_TYPES else "unknown",
                    mappers.ttl_to_seconds(ttl),
                    db,
                    citation_ref=len(citations),
                )
                items.append(item)
                citations.append(citation)
        echo: dict[str, Any] = {"pattern": pattern, "db": db, "limit": limit}
        if type_filter:
            echo["type_filter"] = type_filter
        result = build_result(
            SourceType.REDIS,
            items,
            citations,
            started=call.started,
            query_echo=echo,
            next_cursor=next_cursor,
            warnings=call.warnings,
        )
        return ToolOutcome(result, query_description=f"key khớp '{pattern}' (db {db})")

    # -- redis_get_key ----------------------------------------------------------------------

    async def get_key(
        self,
        *,
        key: str,
        db: int = 0,
        element_limit: int = 100,
        max_bytes: int = 65536,
    ) -> ToolOutcome:
        self._key(key)
        self._db(db)
        if not 1 <= element_limit <= 500:
            raise invalid_input("element_limit", "phải nằm trong khoảng 1..500", SOURCE)
        if not 1024 <= max_bytes <= 131072:
            raise invalid_input("max_bytes", "phải nằm trong khoảng 1024..131072", SOURCE)
        budget, clamp_warnings = effective_max_bytes(max_bytes, self._common)
        call = self._state(budget, clamp_warnings)
        if self._client.is_key_denied(key):
            return self._denied_outcome(key, db, call)

        meta = await self._key_meta(key, db)
        if meta is None:
            return self._missing_outcome(key, db, call)
        key_type, ttl_s, encoding, memory = meta
        if key_type not in _KEY_TYPES:
            call.warn(f"kiểu '{key_type}' không được hỗ trợ đọc giá trị; dùng redis_key_info")
            value: Any = None
            element_count = None
            truncated = False
            before = 0
        else:
            before = call.counter.count
            value, element_count, truncated = await self._read_value(
                key, key_type, db, element_limit, budget, call
            )
        if truncated:
            call.truncated_elsewhere = True
        item, citation = mappers.map_key_value(
            key=key, key_type=key_type if key_type in _KEY_TYPES else "string",
            ttl_s=ttl_s, db=db, encoding=encoding, memory_usage_bytes=memory,
            element_count=element_count, value=value, truncated=truncated or call.truncated,
            redactions=call.counter.count - before, citation_ref=0,
        )  # fmt: skip
        if call.counter.count:
            call.warn(f"{call.counter.count} secret redacted")
        result = build_result(
            SourceType.REDIS,
            [item],
            [citation],
            started=call.started,
            query_echo={"key": key, "db": db, "element_limit": element_limit},
            truncated=truncated or call.truncated,
            warnings=call.warnings,
            redactions=call.counter.count,
        )
        return ToolOutcome(result, identifier=f"Key '{key}'")

    async def _key_meta(
        self, key: str, db: int
    ) -> tuple[str, int | None, str | None, int | None] | None:
        replies = await self._client.execute_many(
            [("TYPE", key), ("TTL", key), ("OBJECT", "ENCODING", key), ("MEMORY", "USAGE", key)],
            db=db,
        )
        key_type = text(replies[0])
        if key_type == "none" or int(replies[1]) == -2:
            return None
        encoding = None if replies[2] is None else text(replies[2])
        return key_type, mappers.ttl_to_seconds(replies[1]), encoding, _truthy_int(replies[3])

    async def _read_value(
        self, key: str, key_type: str, db: int, limit: int, budget: int, call: CallState
    ) -> tuple[Any, int | None, bool]:
        """`(value, element_count, truncated)` with values redacted and byte-budgeted."""
        if key_type == "string":
            return await self._read_string(key, db, budget, call)
        length = int(await self._client.execute(_LENGTH_COMMAND[key_type], key, db=db))
        if key_type == "hash":
            fields = await self._scan_collection("HSCAN", key, db, limit)
            hash_value = {
                text(k): decode_value(v, "auto", partial=False)[0] for k, v in fields.items()
            }
            trimmed = dict(list(hash_value.items())[:limit])
            return self._redact(trimmed, call), length, len(trimmed) < length
        if key_type == "set":
            members = await self._scan_collection("SSCAN", key, db, limit)
            texts = sorted(decode_value(m, "auto")[0] for m in members)[:limit]
            return self._redact(texts, call), length, len(texts) < length
        if key_type == "list":
            raw = await self._client.execute("LRANGE", key, 0, limit - 1, db=db)
            texts = [decode_value(v, "auto")[0] for v in raw]
            return self._redact(texts, call), length, len(texts) < length
        if key_type == "zset":
            raw = await self._client.execute("ZRANGE", key, 0, limit - 1, "WITHSCORES", db=db)
            members = [
                {"member": self._redact(decode_value(m, "auto")[0], call), "score": float(s)}
                for m, s in _pairs(raw)
            ]
            return members, length, len(members) < length
        raw = await self._client.execute("XRANGE", key, "-", "+", "COUNT", limit, db=db)
        entries = []
        for entry_id, fields in raw:
            pairs = fields.items() if isinstance(fields, dict) else _pairs(fields)
            decoded = {text(k): decode_value(v, "auto")[0] for k, v in pairs}
            entries.append({"id": text(entry_id), "fields": self._redact(decoded, call)})
        return entries, length, len(entries) < length

    async def _scan_collection(self, command: str, key: str, db: int, limit: int) -> Any:
        """Collect at least `limit` members via HSCAN/SSCAN (cursor loop, bounded rounds)."""
        position = 0
        collected: dict[Any, Any] | list[Any] | None = None
        for _ in range(_MAX_SCAN_ROUNDS):
            reply = await self._client.execute(
                command, key, position, "COUNT", max(limit, 100), db=db
            )
            position, batch = int(reply[0]), reply[1]
            if collected is None:
                collected = batch if isinstance(batch, dict) else list(batch)
            elif isinstance(collected, dict):
                collected.update(batch)
            else:
                collected.extend(batch)
            if position == 0 or len(collected) >= limit:
                break
        return collected if collected is not None else {}

    async def _read_string(
        self, key: str, db: int, budget: int, call: CallState
    ) -> tuple[Any, int | None, bool]:
        length = int(await self._client.execute("STRLEN", key, db=db))
        raw = await self._client.execute("GETRANGE", key, 0, max(budget, 1) - 1, db=db) or b""
        truncated = length > len(raw)
        body, kind = decode_value(raw, "auto", partial=truncated)
        if kind == "base64":
            call.warn("giá trị không phải UTF-8, trả về dạng base64")
            return body, None, truncated
        if kind == "json" and not truncated:
            tree = self._redact(json.loads(body), call)
            return json.dumps(tree, ensure_ascii=False, separators=(",", ":")), None, False
        return call.plain(body), None, truncated

    # -- redis_key_info ------------------------------------------------------------------------

    async def key_info(self, *, key: str, db: int = 0) -> ToolOutcome:
        self._key(key)
        self._db(db)
        call = self._state()
        if self._client.is_key_denied(key):
            return self._denied_outcome(key, db, call)
        meta = await self._key_meta(key, db)
        if meta is None:
            return self._missing_outcome(key, db, call)
        key_type, ttl_s, encoding, memory = meta
        length = None
        if key_type in _LENGTH_COMMAND:
            length = int(await self._client.execute(_LENGTH_COMMAND[key_type], key, db=db))
        item, citation = mappers.map_key_info(
            key=key, key_type=key_type, ttl_s=ttl_s, db=db, encoding=encoding,
            memory_usage_bytes=memory, length=length, citation_ref=0,
        )  # fmt: skip
        result = build_result(
            SourceType.REDIS,
            [item],
            [citation],
            started=call.started,
            query_echo={"key": key, "db": db},
        )
        return ToolOutcome(result, identifier=f"Key '{key}'")

    # -- redis_server_info ------------------------------------------------------------------------

    async def server_info(self, *, sections: list[str] | None = None) -> ToolOutcome:
        requested = (
            list(sections) if sections is not None else ["server", "memory", "clients", "keyspace"]
        )
        bad = [s for s in requested if s not in _INFO_SECTIONS]
        if bad:
            raise invalid_input("sections", f"section không hợp lệ: {bad}", SOURCE)
        call = self._state()
        replies = await self._client.execute_many([("INFO", s) for s in requested] + [("DBSIZE",)])
        info = {s: mappers.info_section_to_map(replies[i]) for i, s in enumerate(requested)}
        dbsize = int(replies[-1])
        acl_user: str | None = None
        readonly: bool | None = None
        try:
            acl_user = text(await self._client.execute("ACL", "WHOAMI"))
            acl_info = await self._client.execute("ACL", "GETUSER", acl_user)
            readonly = not acl_write_grants(acl_info)
        except ToolError:
            call.warn("không đọc được ACL của user hiện tại; readonly_confirmed=null")
        item, citation = mappers.map_server_info(
            sections=info, requested=requested, dbsize=dbsize,
            acl_user=acl_user, readonly_confirmed=readonly,
        )  # fmt: skip
        result = build_result(
            SourceType.REDIS,
            [item],
            [citation],
            started=call.started,
            query_echo={"sections": requested},
            warnings=call.warnings,
        )
        return ToolOutcome(result, query_description="thông tin server")
