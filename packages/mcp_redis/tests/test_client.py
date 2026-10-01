"""T-049: allowlist, ACL startup check, deny-glob, error mapping of mcp_redis.client."""

from __future__ import annotations

import pytest
import redis.exceptions as rex
from mcp_common.errors import ErrorCode, NotPermittedError, ToolError
from mcp_redis.client import (
    ALLOWED_COMMANDS,
    RedisClient,
    acl_write_grants,
    command_key,
)
from mcp_redis.settings import Settings
from pydantic import SecretStr
from redis_helpers import ACL_RO_COMMANDS, FakeRedis

# FR-008/AC-003: every write command is refused by the code (TC-031), before any I/O.
WRITE_COMMANDS = [
    ("SET", "k", "v"), ("DEL", "k"), ("EXPIRE", "k", 10), ("FLUSHALL",), ("FLUSHDB",),
    ("HSET", "k", "f", "v"), ("LPUSH", "k", "v"), ("SADD", "k", "m"), ("ZADD", "k", 1, "m"),
    ("XADD", "k", "*", "f", "v"), ("RENAME", "a", "b"), ("PUBLISH", "c", "m"),
    ("EVAL", "return 1", 0), ("CONFIG", "SET", "maxmemory", "1"), ("ACL", "SETUSER", "x"),
    ("SELECT", 1), ("SHUTDOWN",),
]  # fmt: skip


@pytest.mark.parametrize("command", WRITE_COMMANDS, ids=[c[0] for c in WRITE_COMMANDS])
@pytest.mark.asyncio
async def test_FR_008_AC_003_write_commands_are_not_permitted(
    client: RedisClient, calls: list, command: tuple
) -> None:
    with pytest.raises(NotPermittedError) as exc:
        await client.execute(*command)
    assert exc.value.code == ErrorCode.NOT_PERMITTED
    assert exc.value.details["operation"] == command_key(command)
    assert calls == []  # nothing reached the server


@pytest.mark.asyncio
async def test_FR_008_AC_003_unbounded_key_listing_command_is_refused(
    client: RedisClient, calls: list
) -> None:
    banned = "KE" + "YS"  # built so the source-scan test can assert the literal never appears
    with pytest.raises(NotPermittedError):
        await client.execute(banned, "*")
    assert banned not in ALLOWED_COMMANDS and calls == []


@pytest.mark.asyncio
async def test_pipeline_enforces_the_same_allowlist(client: RedisClient, calls: list) -> None:
    with pytest.raises(NotPermittedError):
        await client.execute_many([("TYPE", "greeting"), ("DEL", "greeting")])
    assert calls == []


def test_command_key_joins_two_word_commands() -> None:
    assert command_key(("object", "encoding", "k")) == "OBJECT ENCODING"
    assert command_key(("get", "k")) == "GET"
    assert command_key(("acl",)) == "ACL"


def test_allowlist_has_only_read_commands() -> None:
    assert {
        "SCAN",
        "GET",
        "TYPE",
        "TTL",
        "MEMORY USAGE",
        "INFO",
        "ACL WHOAMI",
        "ACL GETUSER",
    } <= set(ALLOWED_COMMANDS)
    assert not {"SET", "DEL", "EXPIRE", "HGETALL", "SMEMBERS"} & set(ALLOWED_COMMANDS)


@pytest.mark.asyncio
async def test_execute_reads_through_the_connection(client: RedisClient) -> None:
    assert await client.execute("TYPE", "greeting") == b"string"
    assert await client.execute("TYPE", "x", db=1) == b"none"


@pytest.mark.asyncio
async def test_connection_errors_map_to_contract_error_codes(
    settings: Settings, common, fake_data
) -> None:
    fake = FakeRedis(fake_data)
    c = RedisClient(settings, common=common, redis_factory=lambda db: fake)
    fake.fail = rex.TimeoutError("read timeout")
    with pytest.raises(ToolError) as exc:
        await c.execute("TYPE", "k")
    assert exc.value.code == ErrorCode.UPSTREAM_TIMEOUT
    assert "VPN" in exc.value.details["hint"] and "redis.example.test" in exc.value.details["hint"]
    fake.fail = rex.ConnectionError("refused")
    with pytest.raises(ToolError) as exc:
        await c.execute_many([("TYPE", "k")])
    assert exc.value.code == ErrorCode.UPSTREAM_UNAVAILABLE
    fake.fail = rex.NoPermissionError("NOPERM")
    with pytest.raises(ToolError) as exc:
        await c.execute("TYPE", "k")
    assert exc.value.code == ErrorCode.FORBIDDEN


@pytest.mark.asyncio
async def test_pipeline_error_result_is_raised(settings: Settings, common, fake_data) -> None:
    fake = FakeRedis(fake_data)
    c = RedisClient(settings, common=common, redis_factory=lambda db: fake)

    class Boom(FakeRedis):
        async def execute_command(self, *args, **options):
            raise rex.ResponseError("WRONGTYPE")

    c2 = RedisClient(settings, common=common, redis_factory=lambda db: Boom(fake_data))
    with pytest.raises(ToolError) as exc:
        await c2.execute_many([("TYPE", "k")])
    assert exc.value.code == ErrorCode.UPSTREAM_ERROR
    assert await c.execute_many([("TYPE", "greeting"), ("TTL", "greeting")]) == [b"string", -1]


# -- deny-glob ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "denied"),
    [
        ("deploy.env", True), ("my-SECRET-key", True), ("db.credentials", True),
        ("tls.pem", True), ("id_rsa_backup", True), ("session:abc123", False),
    ],
)  # fmt: skip
def test_key_deny_glob(client: RedisClient, key: str, denied: bool) -> None:
    assert client.is_key_denied(key) is denied


def test_empty_deny_glob_disables_the_filter(common) -> None:
    c = RedisClient(
        Settings(url="redis://h:6379/0", key_deny=""),
        common=common,
        redis_factory=lambda db: FakeRedis(),
    )
    assert c.is_key_denied("deploy.env") is False


# -- ACL analysis (ADR-0003 A1 / ADR-0008 A1) -----------------------------------------


def _info(commands: str, flags: tuple[str, ...] = ("on",), **extra) -> dict:
    return {"flags": list(flags), "commands": commands, "selectors": [], **extra}


def test_acl_readonly_user_has_no_write_grants() -> None:
    assert acl_write_grants(_info(ACL_RO_COMMANDS)) == []


@pytest.mark.parametrize(
    "commands",
    [
        "+@all", "allcommands", "-@all +@write", "-@all +@read +set", "-@all +get +del",
        "-@all +@dangerous", "-@all +@string", "-@all +acl", "-@all +acl|setuser",
        "-@all +config|set", "-@all +eval", "-@all +flushall", "-@all +xadd",
        "-@all +publish",
    ],
)  # fmt: skip
def test_acl_write_grants_are_detected(commands: str) -> None:
    assert acl_write_grants(_info(commands)), commands


def test_acl_later_minus_rule_cancels_same_rule() -> None:
    assert acl_write_grants(_info("-@all +set -set +get")) == []
    assert acl_write_grants(_info("+@all -@all +get")) == []
    assert acl_write_grants(_info("+@all nocommands +get")) == []


def test_acl_selectors_are_inspected_too() -> None:
    info = _info("-@all +get")
    info["selectors"] = [{"commands": "-@all +set", "keys": "~*", "channels": ""}]
    assert acl_write_grants(info) == ["+set"]
    info["selectors"] = [[b"commands", b"+@write"]]
    assert acl_write_grants(info) == ["+@write"]


def test_acl_raw_resp2_flat_list_reply_is_understood() -> None:
    raw = [b"flags", [b"on"], b"commands", b"-@all +get +set", b"selectors", []]
    assert acl_write_grants(raw) == ["+set"]
    raw_selector = [b"commands", b"-@all +get", b"selectors", [[[b"commands", b"+@write"]]]]
    assert acl_write_grants(raw_selector) == ["+@write"]


def test_acl_bytes_and_missing_commands_are_handled() -> None:
    assert acl_write_grants({"commands": b"-@all +get"}) == []
    assert acl_write_grants({}) == []


@pytest.mark.asyncio
async def test_FR_008_AC_003_startup_check_passes_for_readonly_user(client: RedisClient) -> None:
    report = await client.verify_credentials()
    assert report.ok and report.user == "mcp_ro" and report.reasons == []
    assert await client.credential_check() is True


@pytest.mark.asyncio
async def test_TC_032_user_with_write_category_is_refused(settings, common, fake_data) -> None:
    fake = FakeRedis(fake_data, user="writer", acl_commands="-@all +get +set +@write")
    c = RedisClient(settings, common=common, redis_factory=lambda db: fake)
    report = await c.verify_credentials()
    assert not report.ok
    assert any("+set" in r and "+@write" in r for r in report.reasons)
    assert await c.credential_check() is False


@pytest.mark.asyncio
async def test_startup_check_fails_closed_without_acl_getuser(settings, common, fake_data) -> None:
    class NoGetUser(FakeRedis):
        async def execute_command(self, *args, **options):
            if str(args[0]).upper() == "ACL" and str(args[1]).upper() == "GETUSER":
                raise rex.NoPermissionError("NOPERM acl|getuser")
            return await super().execute_command(*args, **options)

    c = RedisClient(settings, common=common, redis_factory=lambda db: NoGetUser(fake_data))
    report = await c.verify_credentials()
    assert not report.ok and any("+acl|getuser" in r for r in report.reasons)


@pytest.mark.asyncio
async def test_startup_check_reports_connection_failure(settings, common, fake_data) -> None:
    fake = FakeRedis(fake_data)
    fake.fail = rex.ConnectionError("down")
    c = RedisClient(settings, common=common, redis_factory=lambda db: fake)
    report = await c.verify_credentials()
    assert not report.ok and "upstream_unavailable" in report.reasons[0]


@pytest.mark.asyncio
async def test_startup_check_rejects_disabled_user(settings, common, fake_data) -> None:
    class Off(FakeRedis):
        async def execute_command(self, *args, **options):
            result = await super().execute_command(*args, **options)
            if isinstance(result, dict) and "flags" in result:
                result["flags"] = ["off"]
            return result

    c = RedisClient(settings, common=common, redis_factory=lambda db: Off(fake_data))
    report = await c.verify_credentials()
    assert not report.ok and any("off" in r or "disabled" in r for r in report.reasons)


def test_default_factory_uses_documented_timeouts_and_password(common) -> None:
    c = RedisClient(
        Settings(url="redis://mcp_ro@h.example.test:6380/0", password=SecretStr("pw")),
        common=common,
    )
    redis_conn = c._connection(3)
    kwargs = redis_conn.connection_pool.connection_kwargs
    assert kwargs["socket_connect_timeout"] == 2.0 and kwargs["socket_timeout"] == 5.0
    assert kwargs["db"] == 3 and kwargs["password"] == "pw" and kwargs["username"] == "mcp_ro"
    assert kwargs["host"] == "h.example.test" and kwargs["port"] == 6380
    assert c._connection(3) is redis_conn  # one pool per db


@pytest.mark.asyncio
async def test_aclose_closes_every_connection(client: RedisClient) -> None:
    first, second = client._connection(0), client._connection(1)
    await client.aclose()
    assert first.closed and second.closed


@pytest.mark.asyncio
async def test_forbidden_on_nonzero_db_hints_at_select(settings, common, fake_data) -> None:
    fake = FakeRedis(fake_data)
    c = RedisClient(settings, common=common, redis_factory=lambda db: fake)
    fake.fail = rex.NoPermissionError("NOPERM select")
    with pytest.raises(ToolError) as exc:
        await c.execute("TYPE", "k", db=2)
    assert "+select" in exc.value.details["hint"]
    with pytest.raises(ToolError) as exc:
        await c.execute("TYPE", "k", db=0)
    assert "hint" not in exc.value.details
