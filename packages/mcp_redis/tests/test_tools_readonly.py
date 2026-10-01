"""T-051 / NFR-001: the read-only surface of mcp-redis.

AC: FR-014/AC-001 (mutating commands refused), FR-014/AC-002 (unknown write tool rejected at
the MCP protocol layer), FR-008/AC-003 (no code path can write; KEYS never appears).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from mcp_common.contract_testing import load_contract, operations_by_id
from mcp_common.testing import (
    assert_readonly_tool_surface,
    assert_unknown_tool_rejected_at_protocol_layer,
)
from mcp_common.tooling import registered_tool_functions
from mcp_redis.client import ALLOWED_COMMANDS
from mcp_redis.read_api import RedisReadApi
from mcp_redis.server import build_server
from redis_helpers import OK_CALLS

import mcp_redis

PACKAGE_DIR = Path(mcp_redis.__file__).parent
CONTRACT = load_contract()
USERS_ACL = Path(__file__).resolve().parents[3] / "infra" / "redis" / "users.acl"


@pytest.fixture
def server(read_api: RedisReadApi):
    return build_server(read_api)


def test_FR_014_AC_001_tool_surface_is_readonly_and_in_contract(server) -> None:
    snapshot = json.loads((PACKAGE_DIR / "tools.snapshot.json").read_text(encoding="utf-8"))
    assert_readonly_tool_surface(
        registered_tools=registered_tool_functions(server),
        contract_operations=operations_by_id(CONTRACT),
        snapshot=snapshot,
        client_allowlist=ALLOWED_COMMANDS,
    )


@pytest.mark.parametrize(
    "write_tool",
    ["redis_set_key", "redis_delete_key", "redis_expire_key", "redis_flushall", "redis_publish"],
)
@pytest.mark.asyncio
async def test_FR_014_AC_002_unknown_write_tool_rejected_at_protocol_layer(
    server, write_tool: str
) -> None:
    await assert_unknown_tool_rejected_at_protocol_layer(server, write_tool, {"key": "x"})


@pytest.mark.asyncio
async def test_every_tool_call_only_sends_allowlisted_commands(
    server, calls: list, fake_data
) -> None:
    async with create_connected_server_and_client_session(server) as session:
        for name, args in OK_CALLS:
            result = await session.call_tool(name, arguments=args)
            assert not result.isError, (name, result.content)
    sent = {" ".join(str(p).upper() for p in call[:2]) for call in calls}
    sent_keys = {
        f"{str(c[0]).upper()} {str(c[1]).upper()}"
        if str(c[0]).upper() in {"OBJECT", "MEMORY", "ACL"}
        else str(c[0]).upper()
        for c in calls
    }
    assert sent_keys <= set(ALLOWED_COMMANDS), sent_keys - set(ALLOWED_COMMANDS)
    assert len(calls) > len(OK_CALLS) and sent
    assert fake_data[0]["greeting"]["value"] == "xin chào"  # data untouched


def test_FR_008_AC_003_no_write_or_key_listing_command_in_any_source_file() -> None:
    write = re.compile(
        r"\.(set|setex|delete|unlink|expire|pexpire|flushall|flushdb|hset|lpush|rpush|sadd|zadd|"
        r"xadd|publish|eval|config_set|rename|incr|keys|hgetall|smembers)\(",
        re.IGNORECASE,
    )
    banned_words = re.compile(r"\bKEYS\b")
    offenders: list[str] = []
    for path in PACKAGE_DIR.glob("*.py"):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if write.search(line) or banned_words.search(line):
                offenders.append(f"{path.name}:{number}: {line.strip()}")
    assert offenders == []


def test_allowlist_contains_no_write_verbs_and_no_key_listing() -> None:
    forbidden = {"SET", "DEL", "EXPIRE", "FLUSHALL", "HSET", "LPUSH", "SADD", "ZADD", "XADD"}
    assert not forbidden & set(ALLOWED_COMMANDS)
    assert "KE" + "YS" not in ALLOWED_COMMANDS


def test_FR_008_AC_003_dev_acl_file_grants_no_write_command() -> None:
    from mcp_redis.client import acl_write_grants

    line = next(ln for ln in USERS_ACL.read_text().splitlines() if ln.startswith("user mcp_ro "))
    commands = " ".join(tok for tok in line.split() if tok[0] in "+-")
    assert acl_write_grants({"commands": commands}) == []
    assert "+acl|getuser" in line  # ADR-0008 A1
