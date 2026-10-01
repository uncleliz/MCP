"""T-050/T-051: `tools.snapshot.json` + `api-contract.yaml` (ADR-0013), 4 result branches.

Every tool is called through the real MCP protocol (in-memory client session) and its
`structuredContent` is validated against the contract's response schema.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import redis.exceptions as rex
from mcp.shared.memory import create_connected_server_and_client_session
from mcp_common.config import CommonSettings
from mcp_common.contract_testing import (
    assert_snapshot_matches_contract,
    build_snapshot,
    load_contract,
    validate_structured_content,
)
from mcp_redis.client import RedisClient
from mcp_redis.read_api import RedisReadApi
from mcp_redis.server import build_server
from redis_helpers import OK_CALLS, FakeRedis

import mcp_redis

SNAPSHOT_PATH = Path(mcp_redis.__file__).parent / "tools.snapshot.json"
CONTRACT = load_contract()
EXPECTED_TOOLS = ["redis_get_key", "redis_key_info", "redis_scan_keys", "redis_server_info"]


def _snapshot() -> dict[str, Any]:
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def server(read_api: RedisReadApi):
    return build_server(read_api)


async def _call(server, name: str, args: dict[str, Any]):
    async with create_connected_server_and_client_session(server) as session:
        return await session.call_tool(name, arguments=args)


@pytest.mark.asyncio
async def test_snapshot_file_matches_live_tool_surface(read_api: RedisReadApi) -> None:
    live = await build_snapshot(build_server(read_api))
    assert _snapshot() == json.loads(json.dumps(live)), (
        "tools.snapshot.json is stale; regenerate with `uv run mcp-redis tools-dump`"
    )


def test_ADR_0013_snapshot_matches_contract_operations() -> None:
    assert_snapshot_matches_contract(_snapshot(), CONTRACT, tag="redis")


def test_snapshot_has_exactly_four_tools_within_r5_budget() -> None:
    assert sorted(_snapshot()) == EXPECTED_TOOLS and len(_snapshot()) <= 12


def test_tool_descriptions_are_at_most_three_sentences() -> None:
    for name, tool in _snapshot().items():
        sentences = [s for s in tool["description"].replace("...", "").split(". ") if s.strip()]
        assert len(sentences) <= 3, name


@pytest.mark.parametrize(
    ("tool", "args"), OK_CALLS, ids=[f"{c[0]}-{i}" for i, c in enumerate(OK_CALLS)]
)
@pytest.mark.asyncio
async def test_FR_008_AC_001_ok_branch_validates_against_contract(
    server, tool: str, args: dict[str, Any]
) -> None:
    result = await _call(server, tool, args)
    assert not result.isError, result.content
    validate_structured_content(CONTRACT, tool, result.structuredContent)
    assert result.structuredContent["status"] in {"ok", "partial"}
    assert result.structuredContent["meta"]["source"] == "redis"
    assert "Nguồn:" in result.content[0].text


@pytest.mark.asyncio
async def test_FR_008_AC_002_empty_branch_validates_against_contract(server) -> None:
    result = await _call(server, "redis_scan_keys", {"pattern": "nomatch:*"})
    validate_structured_content(CONTRACT, "redis_scan_keys", result.structuredContent)
    payload = result.structuredContent
    assert payload["status"] == "empty" and payload["items"] == [] and payload["citations"] == []
    assert payload["meta"]["query_echo"]["pattern"] == "nomatch:*"
    assert result.content[0].text.startswith("Không tìm thấy")


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("redis_get_key", {"key": "missing"}),
        ("redis_key_info", {"key": "missing"}),
        ("redis_get_key", {"key": "deploy.env"}),
    ],
)
@pytest.mark.asyncio
async def test_TC_030_not_found_branch_validates_against_contract(
    server, tool: str, args: dict[str, Any]
) -> None:
    result = await _call(server, tool, args)
    assert not result.isError
    validate_structured_content(CONTRACT, tool, result.structuredContent)
    assert result.structuredContent["status"] == "not_found"
    assert "không tồn tại" in result.content[0].text


@pytest.mark.asyncio
async def test_error_branch_timeout_unavailable_forbidden_invalid(
    settings, common, fake_data
) -> None:
    fake = FakeRedis(fake_data)
    api = RedisReadApi(RedisClient(settings, common=common, redis_factory=lambda db: fake), common)
    server = build_server(api)
    cases = [
        (rex.TimeoutError("t"), "upstream_timeout"),
        (rex.ConnectionError("c"), "upstream_unavailable"),
        (rex.NoPermissionError("NOPERM"), "forbidden"),
        (rex.AuthenticationError("bad"), "unauthorized"),
    ]
    for exc, code in cases:
        fake.fail = exc
        result = await _call(server, "redis_key_info", {"key": "k"})
        assert result.isError
        validate_structured_content(
            CONTRACT, "redis_key_info", result.structuredContent, is_error=True
        )
        assert result.structuredContent["error"]["code"] == code
        assert result.structuredContent["error"]["source"] == "redis"
    fake.fail = None
    invalid = await _call(server, "redis_scan_keys", {"cursor": "###"})
    assert invalid.structuredContent["error"]["code"] == "invalid_input"
    assert invalid.structuredContent["error"]["details"]["field"] == "cursor"


@pytest.mark.asyncio
async def test_unconfigured_server_reports_source_misconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MCP_REDIS_URL", raising=False)
    result = await _call(build_server(common=CommonSettings()), "redis_scan_keys", {})
    assert result.isError
    error = result.structuredContent["error"]
    assert error["code"] == "source_misconfigured"
    assert "MCP_REDIS_URL" in error["details"]["missing_env"]
