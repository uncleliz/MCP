"""T-051: the real redis-py reply shapes and the real ACL, against a throw-away local
`redis-server` started with the repo's `infra/redis/users.acl`.

This is NOT the docker-compose integration test (that one is `@pytest.mark.live` in
test_integration.py); it only needs the `redis-server` binary. Skipped, with a reason, when
the binary is not on PATH.
"""

from __future__ import annotations

import shutil
import socket
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
import redis.asyncio as aioredis
import redis.exceptions as rex
from mcp_common.config import CommonSettings
from mcp_common.errors import NotPermittedError
from mcp_redis.client import ALLOWED_COMMANDS, RedisClient
from mcp_redis.read_api import RedisReadApi
from mcp_redis.settings import Settings
from pydantic import SecretStr

USERS_ACL = Path(__file__).resolve().parents[3] / "infra" / "redis" / "users.acl"
REDIS_SERVER = shutil.which("redis-server")

pytestmark = pytest.mark.skipif(
    REDIS_SERVER is None,
    reason="local `redis-server` binary not found on PATH (install redis or use the live tests)",
)


@pytest.fixture(scope="module")
def local_redis(tmp_path_factory: pytest.TempPathFactory) -> Iterator[int]:
    assert REDIS_SERVER is not None
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    workdir = tmp_path_factory.mktemp("redis")
    proc = subprocess.Popen(
        [
            REDIS_SERVER,
            "--port",
            str(port),
            "--bind",
            "127.0.0.1",
            "--aclfile",
            str(USERS_ACL),
            "--save",
            "",
            "--appendonly",
            "no",
            "--dir",
            str(workdir),
        ],  # fmt: skip
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.05)
        else:  # pragma: no cover - environment problem
            pytest.skip("local redis-server did not start")
        yield port
    finally:
        proc.terminate()
        proc.wait(timeout=10)


async def _seed(port: int) -> None:
    admin = aioredis.Redis(port=port)  # `default` user, nopass +@all (dev only)
    await admin.flushall()
    await admin.hset(
        "session:abc123",
        mapping={
            "user_id": "8891",
            "token": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjMifQ.c2lnbmF0dXJlMTIz",
        },
    )
    await admin.expire("session:abc123", 1800)
    await admin.set("greeting", "xin chào")
    await admin.rpush("queue:jobs", "j1", "j2", "j3")
    await admin.sadd("tags", "a", "b")
    await admin.zadd("leaders", {"alice": 10, "bob": 5})
    await admin.xadd("events", {"kind": "login"}, id="1-0")
    await admin.set("deploy.env", "SECRET=1")
    await admin.set("binary", b"\xff\xfe\x00bin")
    await admin.aclose()


def _client(port: int, *, user: str = "mcp_ro", password: str | None = "mcp_ro_dev_password"):
    settings = Settings(
        url=f"redis://{user}@127.0.0.1:{port}/0", password=SecretStr(password) if password else None
    )
    return RedisClient(settings, common=CommonSettings())


@pytest.mark.asyncio
async def test_FR_008_AC_003_acl_startup_check_passes_for_mcp_ro_and_fails_for_default(
    local_redis: int,
) -> None:
    ro = _client(local_redis)
    try:
        report = await ro.verify_credentials()
        assert report.ok and report.user == "mcp_ro", report.reasons
    finally:
        await ro.aclose()
    default = _client(local_redis, user="default", password=None)
    try:
        report = await default.verify_credentials()
        assert not report.ok and any("@all" in reason for reason in report.reasons)
    finally:
        await default.aclose()


@pytest.mark.asyncio
async def test_FR_008_AC_001_real_shapes_for_every_type(local_redis: int) -> None:
    await _seed(local_redis)
    client = _client(local_redis)
    api = RedisReadApi(client, CommonSettings())
    try:
        scan = await api.scan_keys(pattern="*", limit=100)
        keys = {i["key"] for i in scan.result.items}
        assert "deploy.env" not in keys and {"greeting", "session:abc123", "events"} <= keys
        assert any("MCP_REDIS_KEY_DENY" in w for w in scan.result.meta.warnings)

        session = (await api.get_key(key="session:abc123")).result.items[0]
        assert session["ttl_s"] is not None and 0 < session["ttl_s"] <= 1800
        assert session["value"]["user_id"] == "8891" and "eyJ" not in str(session["value"])
        assert session["redactions"] >= 1 and session["element_count"] == 2

        assert (await api.get_key(key="greeting")).result.items[0]["value"] == "xin chào"
        assert (await api.get_key(key="queue:jobs")).result.items[0]["value"] == ["j1", "j2", "j3"]
        assert sorted((await api.get_key(key="tags")).result.items[0]["value"]) == ["a", "b"]
        zset = (await api.get_key(key="leaders")).result.items[0]["value"]
        assert zset == [{"member": "bob", "score": 5.0}, {"member": "alice", "score": 10.0}]
        stream = (await api.get_key(key="events")).result.items[0]["value"]
        assert stream == [{"id": "1-0", "fields": {"kind": "login"}}]
        assert (await api.get_key(key="binary")).result.items[0]["value"].startswith("base64:")

        info = (await api.key_info(key="queue:jobs")).result.items[0]
        assert info["type"] == "list" and info["length"] == 3 and info["encoding"]
        assert (await api.key_info(key="absent")).result.status.value == "not_found"
        assert (await api.get_key(key="absent")).result.status.value == "not_found"

        server = (await api.server_info(sections=["memory", "keyspace"])).result.items[0]
        assert server["acl_user"] == "mcp_ro" and server["readonly_confirmed"] is True
        assert server["dbsize"] >= 8 and "used_memory_human" in server["sections"]["memory"]
        assert server["sections"]["keyspace"]["db0"].startswith("keys=")
    finally:
        await client.aclose()


@pytest.mark.parametrize(
    "command",
    [("SET", "k", "v"), ("DEL", "greeting"), ("EXPIRE", "greeting", 1), ("FLUSHALL",)],
)
@pytest.mark.asyncio
async def test_FR_008_AC_003_writes_are_refused_by_code_and_by_the_server_acl(
    local_redis: int, command: tuple
) -> None:
    await _seed(local_redis)
    client = _client(local_redis)
    try:
        with pytest.raises(NotPermittedError):  # refused by mcp-redis itself
            await client.execute(*command)
        raw = aioredis.Redis(port=local_redis, username="mcp_ro", password="mcp_ro_dev_password")
        with pytest.raises(rex.NoPermissionError):  # and by the Redis ACL, bypassing the code
            await raw.execute_command(*command)
        await raw.aclose()
        assert await client.execute("TYPE", "greeting") == b"string"  # data untouched
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_non_default_db_works_with_select_granted_and_stays_read_only(
    local_redis: int,
) -> None:
    """ADR-0008 A5: the ACL grants `+select` so `db>0` works (SA reconcile, 2026-10-01).
    SELECT is never sent by a tool (it is not in ALLOWED_COMMANDS); the connection to db 1 sends
    it at connect time. It must not turn the startup check red, and writes stay refused."""
    admin = aioredis.Redis(port=local_redis, db=1)
    await admin.flushall()
    await admin.set("only-in-db1", "hello")
    await admin.aclose()
    client = _client(local_redis)
    try:
        assert "SELECT" not in ALLOWED_COMMANDS
        assert await client.execute("GET", "only-in-db1", db=1) == b"hello"
        assert await client.execute("GET", "only-in-db1", db=0) is None
        with pytest.raises(NotPermittedError):
            await client.execute("SET", "k", "v", db=1)
        with pytest.raises(NotPermittedError):  # SELECT as a command is not a tool command
            await client.execute("SELECT", 1)
        report = await client.verify_credentials()
        assert report.ok, report.reasons  # +select is not a write grant
    finally:
        await client.aclose()


def test_the_acl_file_grants_select_and_nothing_that_writes() -> None:
    from mcp_redis.client import acl_write_grants

    rules = next(line for line in USERS_ACL.read_text().splitlines() if "user mcp_ro" in line)
    assert "+select" in rules.split()
    assert (
        acl_write_grants(
            {"commands": " ".join(t for t in rules.split() if t.startswith(("+", "-")))}
        )
        == []
    )


@pytest.mark.asyncio
async def test_T_051_the_three_key_tools_work_against_db_1_with_the_real_acl(
    local_redis: int,
) -> None:
    """ADR-0008 A5 / SA reconcile #6: `db=1` returns ok/empty/not_found, never `forbidden`."""
    admin = aioredis.Redis(port=local_redis, db=1)
    await admin.flushall()
    await admin.set("only-in-db1", "hello")
    await admin.rpush("queue:db1", "a", "b")
    await admin.aclose()
    client = _client(local_redis)
    api = RedisReadApi(client, CommonSettings())
    try:
        scan = await api.scan_keys(pattern="*", db=1)
        assert scan.result.status.value == "ok"
        assert {i["key"] for i in scan.result.items} == {"only-in-db1", "queue:db1"}
        assert (await api.get_key(key="only-in-db1", db=1)).result.items[0]["value"] == "hello"
        info = (await api.key_info(key="queue:db1", db=1)).result.items[0]
        assert info["type"] == "list" and info["length"] == 2
        assert (await api.get_key(key="only-in-db1", db=0)).result.status.value == "not_found"
        assert (await api.scan_keys(pattern="zzz*", db=1)).result.status.value == "empty"
    finally:
        await client.aclose()
