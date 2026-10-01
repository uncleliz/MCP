"""Shared fixtures for mcp-redis tests (fakes mimic redis-py; see redis_helpers.py)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from mcp_common.config import CommonSettings
from mcp_redis.client import RedisClient
from mcp_redis.read_api import RedisReadApi
from mcp_redis.settings import Settings
from pydantic import SecretStr

sys.path.insert(0, str(Path(__file__).parent))

from redis_helpers import FakeRedis  # noqa: E402

URL = "redis://mcp_ro@redis.example.test:6379/0"

SEED: dict[str, dict[str, Any]] = {
    "session:abc123": {
        "type": "hash",
        "value": {
            "user_id": "8891",
            "token": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjMifQ.c2lnbmF0dXJlMTIz",
            "created_at": "2026-09-30T10:00:00Z",
        },
        "ttl": 1800,
        "encoding": "listpack",
        "memory": 312,
    },
    "session:def456": {"type": "hash", "value": {"user_id": "1"}, "ttl": -1},
    "greeting": {"type": "string", "value": "xin chào", "ttl": -1, "encoding": "embstr"},
    "config:json": {
        "type": "string",
        "value": '{"name":"svc","password":"hunter2hunter2","n":1}',
        "ttl": 60,
    },
    "queue:jobs": {"type": "list", "value": ["j1", "j2", "j3"], "ttl": -1, "encoding": "quicklist"},
    "tags": {"type": "set", "value": ["a", "b"], "ttl": -1},
    "leaders": {"type": "zset", "value": {"alice": 10, "bob": 5}, "ttl": -1},
    "events": {
        "type": "stream",
        "value": [("1-0", {"kind": "login", "api_key": "abcdefghijklmnop"}), ("2-0", {"k": "v"})],
        "ttl": -1,
    },
    "deploy.env": {"type": "string", "value": "SECRET=1", "ttl": -1},
    "binary": {"type": "string", "value": b"\xff\xfe\x00bin", "ttl": -1},
}


@pytest.fixture
def settings() -> Settings:
    return Settings(url=URL, password=SecretStr("not-a-real-password"))


@pytest.fixture
def common() -> CommonSettings:
    return CommonSettings()


@pytest.fixture
def calls() -> list[tuple[Any, ...]]:
    return []


@pytest.fixture
def fake_data() -> dict[int, dict[str, dict[str, Any]]]:
    return {0: {k: dict(v) for k, v in SEED.items()}, 1: {}}


@pytest.fixture
def client(
    settings: Settings,
    common: CommonSettings,
    fake_data: dict[int, dict[str, dict[str, Any]]],
    calls: list[tuple[Any, ...]],
) -> RedisClient:
    return RedisClient(
        settings,
        common=common,
        redis_factory=lambda db: FakeRedis(fake_data, db=db, calls=calls),
    )


@pytest.fixture
def read_api(client: RedisClient, common: CommonSettings) -> RedisReadApi:
    return RedisReadApi(client, common)
