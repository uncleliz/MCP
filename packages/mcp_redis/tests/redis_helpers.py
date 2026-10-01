"""An in-memory stand-in for `redis.asyncio.Redis` that mimics redis-py's RESP2 response
shapes (after its response callbacks) for the read commands mcp-redis is allowed to send.

`FakeRedis.calls` records every command so tests can assert what (never) went out.
"""

from __future__ import annotations

import fnmatch
from typing import Any

import redis.exceptions as rex

ACL_RO_COMMANDS = (
    "-@all +get +mget +strlen +getrange +type +ttl +pttl +exists +scan +hget +hmget +hgetall "
    "+hscan +hlen +lrange +llen +smembers +sscan +scard +zrange +zcard +xrange +xlen +xinfo "
    "+object|encoding +memory|usage +dbsize +info +acl|whoami +acl|getuser"
)


def b(value: Any) -> bytes:
    return value if isinstance(value, bytes) else str(value).encode()


class FakeRedis:
    def __init__(
        self,
        data: dict[int, dict[str, dict[str, Any]]] | None = None,
        *,
        user: str = "mcp_ro",
        acl_commands: str = ACL_RO_COMMANDS,
        scan_batch: int = 1000,
        db: int = 0,
        calls: list[tuple[Any, ...]] | None = None,
    ) -> None:
        self.db_index = db
        # data[db][key] = {"type": "string", "value": ..., "ttl": int | -1, "encoding": ...}
        self.data = data if data is not None else {0: {}}
        self.user = user
        self.acl_commands = acl_commands
        self.scan_batch = scan_batch
        self.calls: list[tuple[Any, ...]] = calls if calls is not None else []
        self.closed = False
        self.fail: Exception | None = None
        self.delay: Any = None  # awaitable factory for hang tests

    def _db(self, db: int) -> dict[str, dict[str, Any]]:
        return self.data.setdefault(db, {})

    async def execute_command(self, *args: Any, **_options: Any) -> Any:
        if self.fail is not None:
            raise self.fail
        if self.delay is not None:
            await self.delay()
        self.calls.append(args)
        return self._dispatch(args)

    def pipeline(self, transaction: bool = True) -> FakePipeline:
        return FakePipeline(self)

    async def aclose(self) -> None:
        self.closed = True

    # -- command implementations -----------------------------------------------------

    def _dispatch(self, args: tuple[Any, ...]) -> Any:
        name = str(args[0]).upper()
        rest = [a.decode() if isinstance(a, bytes) else a for a in args[1:]]
        db = self._db(self.db_index)
        if name == "SCAN":
            return self._scan(db, rest)
        if name == "ACL":
            sub = str(rest[0]).upper()
            if sub == "WHOAMI":
                return self.user.encode()
            return {"flags": ["on"], "passwords": ["#x"], "commands": self.acl_commands,
                    "keys": "~*", "channels": "", "selectors": []}  # fmt: skip
        if name == "INFO":
            return self._info(rest[0] if rest else "default", db)
        if name == "DBSIZE":
            return len(db)
        key = (rest[1] if name in {"OBJECT", "MEMORY"} else rest[0]) if rest else ""
        entry = db.get(key)
        if name == "TYPE":
            return (entry["type"] if entry else "none").encode()
        if name == "TTL":
            return -2 if entry is None else entry.get("ttl", -1)
        if name == "OBJECT":
            return None if entry is None else entry.get("encoding", "raw").encode()
        if name == "MEMORY":
            return None if entry is None else entry.get("memory", 64)
        if entry is None:
            return None if name in {"GETRANGE", "STRLEN"} else {"HSCAN": (0, {})}.get(name, [])
        return self._typed(name, entry, rest)

    def _scan(self, db: dict[str, Any], rest: list[Any]) -> tuple[int, list[bytes]]:
        cursor = int(rest[0])
        pattern, type_filter = "*", None
        for index, token in enumerate(rest):
            if str(token).upper() == "MATCH":
                pattern = rest[index + 1]
            if str(token).upper() == "TYPE":
                type_filter = rest[index + 1]
        names = sorted(db)
        batch = names[cursor : cursor + self.scan_batch]
        next_cursor = cursor + self.scan_batch
        matched = [
            k.encode()
            for k in batch
            if fnmatch.fnmatchcase(k, pattern) and (not type_filter or db[k]["type"] == type_filter)
        ]
        return (0 if next_cursor >= len(names) else next_cursor), matched

    def _typed(self, name: str, entry: dict[str, Any], rest: list[Any]) -> Any:
        value = entry["value"]
        if name == "STRLEN":
            return len(b(value))
        if name == "GETRANGE":
            return b(value)[int(rest[1]) : int(rest[2]) + 1]
        if name == "GET":
            return b(value)
        if name in {"HLEN", "LLEN", "SCARD", "ZCARD", "XLEN"}:
            return len(value)
        if name == "HSCAN":
            return 0, {b(k): b(v) for k, v in value.items()}
        if name == "SSCAN":
            return 0, [b(m) for m in sorted(value)]
        if name == "LRANGE":
            start, stop = int(rest[1]), int(rest[2])
            return [b(v) for v in value[start : stop + 1]]
        if name == "ZRANGE":
            start, stop = int(rest[1]), int(rest[2])
            flat: list[Any] = []
            for member, score in sorted(value.items(), key=lambda kv: kv[1])[start : stop + 1]:
                flat += [b(member), float(score)]
            return flat
        if name == "XRANGE":
            count = int(rest[rest.index("COUNT") + 1]) if "COUNT" in rest else len(value)
            return [(b(i), {b(k): b(v) for k, v in f.items()}) for i, f in value[:count]]
        raise rex.ResponseError(f"unknown command {name}")

    def _info(self, section: str, db: dict[str, Any]) -> dict[str, Any]:
        sections = {
            "memory": {"used_memory_human": "1.00M", "maxmemory_human": "0B"},
            "clients": {"connected_clients": 3},
            "server": {"redis_version": "7.0.15"},
            "keyspace": {"db0": {"keys": len(db), "expires": 0, "avg_ttl": 0}},
        }
        return sections.get(section.lower(), {})


class FakePipeline:
    def __init__(self, redis: FakeRedis) -> None:
        self._redis = redis
        self._queued: list[tuple[Any, ...]] = []

    async def __aenter__(self) -> FakePipeline:
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        return None

    def execute_command(self, *args: Any, **_options: Any) -> FakePipeline:
        self._queued.append(args)
        return self

    async def execute(self, raise_on_error: bool = True) -> list[Any]:
        results: list[Any] = []
        for args in self._queued:
            try:
                results.append(await self._redis.execute_command(*args))
            except Exception as exc:  # noqa: BLE001 - mirrors raise_on_error=False
                if raise_on_error:
                    raise
                results.append(exc)
        return results


OK_CALLS: list[tuple[str, dict[str, Any]]] = [
    ("redis_scan_keys", {"pattern": "session:*"}),
    ("redis_get_key", {"key": "session:abc123", "element_limit": 50}),
    ("redis_get_key", {"key": "leaders"}),
    ("redis_key_info", {"key": "session:abc123"}),
    ("redis_server_info", {"sections": ["memory", "keyspace"]}),
    ("redis_server_info", {}),
]
