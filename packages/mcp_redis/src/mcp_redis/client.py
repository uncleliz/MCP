"""Redis transport layer (ADR-0008): `redis.asyncio` behind a command allowlist.

Read-only guarantees in this file (defense in depth, ADR-0003):

* layer 2 — every command, including the ones in a pipeline, goes through
  :meth:`RedisClient.execute` / :meth:`RedisClient.execute_many`, which check the command
  name against :data:`ALLOWED_COMMANDS` *before* any I/O; anything else raises
  `not_permitted`. The unbounded key-listing command is not in the list (use `SCAN`);
* layer 3 — the startup check (:meth:`RedisClient.verify_credentials`) reads the ACL of
  the user we are connected as (`ACL WHOAMI` + `ACL GETUSER`, ADR-0008 A1) and refuses to
  serve if that user can run any write-capable command.

`connect 2s / read 5s` (ADR-0008 A4) are fixed constants, not env-tunable, to keep the
NFR-002 budget invariant intact.
"""

from __future__ import annotations

import fnmatch
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
import redis.asyncio as aioredis
from mcp_common.config import CommonSettings
from mcp_common.errors import ErrorCode, ToolError, map_exception_to_tool_error
from mcp_common.readonly import enforce
from mcp_common.redact import register_secret

from mcp_redis.settings import Settings

__all__ = [
    "ALLOWED_COMMANDS",
    "CONNECT_TIMEOUT_S",
    "CredentialReport",
    "READ_TIMEOUT_S",
    "RedisClient",
    "SOURCE",
    "acl_write_grants",
    "command_key",
]

SOURCE = "redis"
CONNECT_TIMEOUT_S = 2.0
READ_TIMEOUT_S = 5.0

# What the code actually sends. Deliberately narrower than the ADR-0008 v1 list: the
# unbounded readers (HGETALL, SMEMBERS, MGET) are replaced by HSCAN/SSCAN/GETRANGE.
ALLOWED_COMMANDS: tuple[str, ...] = (
    "SCAN",
    "GET",
    "GETRANGE",
    "STRLEN",
    "TYPE",
    "TTL",
    "HSCAN",
    "HLEN",
    "LRANGE",
    "LLEN",
    "SSCAN",
    "SCARD",
    "ZRANGE",
    "ZCARD",
    "XRANGE",
    "XLEN",
    "OBJECT ENCODING",
    "MEMORY USAGE",
    "DBSIZE",
    "INFO",
    "ACL WHOAMI",
    "ACL GETUSER",
)

_TWO_WORD_COMMANDS = frozenset({"OBJECT", "MEMORY", "ACL", "XINFO", "CONFIG", "CLIENT", "SCRIPT"})

# -- ACL analysis ---------------------------------------------------------------------

# Commands that change data or server state. Used only to judge a user's ACL, never to
# decide what we send (that is the allowlist above).
_WRITE_COMMANDS = frozenset(
    """set setex psetex setnx mset msetnx append incr incrby incrbyfloat decr decrby del unlink
    expire pexpire expireat pexpireat persist rename renamenx copy move restore getset getdel
    getex setrange setbit bitop bitfield lpush rpush lpushx rpushx lpop rpop lset linsert lrem
    ltrim rpoplpush lmove blpop brpop blmove lmpop blmpop sadd srem spop smove sdiffstore
    sinterstore sunionstore hset hsetnx hmset hdel hincrby hincrbyfloat zadd zrem zincrby
    zpopmin zpopmax bzpopmin bzpopmax zunionstore zinterstore zdiffstore zrangestore
    zremrangebyrank zremrangebyscore zremrangebylex xadd xdel xtrim xack xclaim xautoclaim
    xgroup xsetid pfadd pfmerge geoadd georadius georadiusbymember geosearchstore sort
    publish spublish flushall flushdb swapdb eval evalsha eval_ro evalsha_ro fcall fcall_ro
    function script module debug shutdown replicaof slaveof failover bgsave bgrewriteaof save
    monitor sync psync migrate cluster acl config client""".split()
)
# Commands whose sub-commands are mixed: only these are read-only.
_SAFE_SUBCOMMANDS = frozenset(
    {
        ("acl", "whoami"), ("acl", "getuser"), ("config", "get"), ("client", "info"),
        ("client", "getname"), ("client", "id"),
    }
)  # fmt: skip
_READ_ONLY_CATEGORIES = frozenset({"read"})


def _rule_grants_write(rule: str) -> bool:
    """`rule` is a `+...` ACL token with the leading `+` removed."""
    if rule in {"@all", "allcommands"}:
        return True
    if rule.startswith("@"):
        return rule[1:].lower() not in _READ_ONLY_CATEGORIES
    name, _, sub = rule.partition("|")
    name = name.lower()
    if sub:
        if name in {"acl", "config", "client"}:
            return (name, sub.lower()) not in _SAFE_SUBCOMMANDS
        return name in _WRITE_COMMANDS
    return name in _WRITE_COMMANDS


def _as_text(value: Any) -> str:
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value)


def _acl_mapping(reply: Any) -> dict[str, Any]:
    """`ACL GETUSER` reply -> dict (raw RESP2 replies are a flat `[k, v, k, v, ...]` list)."""
    if isinstance(reply, Mapping):
        return {_as_text(k): v for k, v in reply.items()}
    flat = list(reply or [])
    return {_as_text(k): v for k, v in zip(flat[0::2], flat[1::2], strict=False)}


def _command_rule_strings(reply: Any) -> list[str]:
    """`commands` of the user and of each selector, as a flat list of rule strings."""
    info = _acl_mapping(reply)
    rule_sets = [_as_text(info.get("commands") or "")]
    for selector in info.get("selectors") or []:
        if isinstance(selector, Mapping):
            rule_sets.append(_as_text(selector.get("commands") or ""))
        else:  # RESP2 flat [k, v, k, v] or [[k, v], ...]
            flat = list(selector)
            pairs = (
                [(p[0], p[1]) for p in flat]
                if flat and isinstance(flat[0], list | tuple)
                else list(zip(flat[0::2], flat[1::2], strict=False))
            )
            rule_sets.extend(_as_text(v) for k, v in pairs if _as_text(k).lower() == "commands")
    return rule_sets


def acl_write_grants(info: Any) -> list[str]:
    """The `+...` rules of an `ACL GETUSER` reply that can grant write-capable commands.

    Conservative on purpose: only the `@read` category is considered safe, and a later
    `-x` rule cancels an earlier `+x` of the same token (and `-@all`/`nocommands` cancel
    everything before them); `+@all -@write` is *not* treated as safe.
    """
    offenders: list[str] = []
    for rules in _command_rule_strings(info):
        active: list[str] = []
        for token in rules.split():
            low = token.lower()
            if low in {"-@all", "nocommands"}:
                active.clear()
            elif low == "allcommands":
                active.append(token)
            elif token.startswith("+") and _rule_grants_write(token[1:]):
                active.append(token)
            elif token.startswith("-"):
                active = [a for a in active if a.lower() != "+" + low[1:]]
        offenders.extend(active)
    return offenders


def command_key(args: Sequence[Any]) -> str:
    """`("object", "encoding", k)` -> `OBJECT ENCODING`; `("get", k)` -> `GET`."""
    name = _as_text(args[0]).upper()
    if name in _TWO_WORD_COMMANDS and len(args) > 1:
        return f"{name} {_as_text(args[1]).upper()}"
    return name


@dataclass
class CredentialReport:
    ok: bool
    reasons: list[str] = field(default_factory=list)
    user: str | None = None


RedisFactory = Callable[[int], Any]


class RedisClient:
    def __init__(
        self,
        settings: Settings,
        *,
        common: CommonSettings | None = None,
        redis_factory: RedisFactory | None = None,
    ) -> None:
        self._settings = settings
        self._common = common or CommonSettings()
        self._host = httpx.URL(settings.url.replace("redis", "http", 1)).host
        self._deny = [glob.lower() for glob in settings.deny_globs]
        # E-mcp-data-platform-009 (FR-025/NFR-014): register the configured password for
        # value-based scrubbing at the single client-construction seam, so an opaque
        # credential is redacted from any outbound error/result/log. Additive; no-op when
        # no password is configured.
        if settings.password is not None:
            register_secret(settings.password.get_secret_value())
        self._factory = redis_factory or self._default_factory
        self._connections: dict[int, Any] = {}

    def _default_factory(self, db: int) -> aioredis.Redis:
        password = self._settings.password
        parts = urlsplit(self._settings.url)
        return aioredis.Redis.from_url(
            urlunsplit(parts._replace(path=f"/{db}")),
            password=password.get_secret_value() if password else None,
            socket_connect_timeout=CONNECT_TIMEOUT_S,
            socket_timeout=READ_TIMEOUT_S,
            decode_responses=False,
        )

    def _connection(self, db: int) -> Any:
        if db not in self._connections:
            self._connections[db] = self._factory(db)
        return self._connections[db]

    async def aclose(self) -> None:
        for connection in self._connections.values():
            await connection.aclose()
        self._connections.clear()

    # -- key deny-glob (ADR-0015) --------------------------------------------------------

    def is_key_denied(self, key: str) -> bool:
        lowered = key.lower()
        return any(fnmatch.fnmatchcase(lowered, glob) for glob in self._deny)

    # -- the choke points ------------------------------------------------------------------

    async def _guard(self, call: Callable[[], Awaitable[Any]], db: int = 0) -> Any:
        try:
            return await call()
        except ToolError:
            raise
        except Exception as exc:  # noqa: BLE001 - mapped to the contract taxonomy
            error = map_exception_to_tool_error(exc, source=SOURCE, host=self._host)
            if error.code == ErrorCode.FORBIDDEN and db:
                # Connecting to db>0 sends SELECT, which the ADR-0008 ACL does not grant.
                error.details.setdefault("hint", "user ACL cần +select để dùng db khác 0")
            raise error from exc

    async def execute(self, *args: Any, db: int = 0) -> Any:
        enforce(ALLOWED_COMMANDS, command_key(args), source=SOURCE)
        return await self._guard(lambda: self._connection(db).execute_command(*args), db)

    async def execute_many(self, commands: Sequence[Sequence[Any]], db: int = 0) -> list[Any]:
        for command in commands:
            enforce(ALLOWED_COMMANDS, command_key(command), source=SOURCE)

        async def run() -> list[Any]:
            async with self._connection(db).pipeline(transaction=False) as pipe:
                for command in commands:
                    pipe.execute_command(*command)
                return list(await pipe.execute(raise_on_error=False))

        results = await self._guard(run, db)
        for result in results:
            if isinstance(result, Exception):
                raise map_exception_to_tool_error(result, source=SOURCE, host=self._host)
        return results

    # -- startup credential check (ADR-0003 A1, ADR-0008 A1/A3) ------------------------------

    async def verify_credentials(self) -> CredentialReport:
        """`ACL WHOAMI` + `ACL GETUSER`: refuse a user that can run any write command."""
        try:
            user = _as_text(await self.execute("ACL", "WHOAMI"))
        except ToolError as exc:
            return CredentialReport(False, [f"{exc.code.value}: {exc.message}"])
        try:
            info = await self.execute("ACL", "GETUSER", user)
        except ToolError as exc:
            return CredentialReport(
                False,
                [
                    f"cannot read ACL of user '{user}' ({exc.code.value}); the user needs "
                    "+acl|getuser so read-only can be verified (ADR-0008 A1)"
                ],
                user,
            )
        reasons: list[str] = []
        flags = {_as_text(flag).lower() for flag in (_acl_mapping(info).get("flags") or [])}
        if "off" in flags or ("on" not in flags and flags):
            reasons.append(f"user '{user}' is disabled (flags: {sorted(flags)})")
        grants = acl_write_grants(info)
        if grants:
            reasons.append(
                f"user '{user}' can run write-capable commands: {', '.join(grants)} "
                "(use an ACL user limited to read commands, see infra/redis/users.acl)"
            )
        return CredentialReport(not reasons, reasons, user)

    async def credential_check(self) -> bool:
        """`mcp_common.runtime.serve(credential_check=...)` hook."""
        return (await self.verify_credentials()).ok
