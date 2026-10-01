"""Live integration tests against the `redis:7` container of infra/docker-compose.yml
(user `mcp_ro`, ACL file infra/redis/users.acl).

Skipped by default (see packages/conftest.py): the Docker daemon is not available in CI or
the dev container. Run manually:

    docker compose -f infra/docker-compose.yml up -d redis
    MCP_LIVE_TESTS=1 MCP_REDIS_URL=redis://mcp_ro@localhost:6379/0 \\
        MCP_REDIS_PASSWORD=mcp_ro_dev_password uv run pytest packages/mcp_redis -m live

Seeding uses the compose `default` user with a *separate* admin connection inside the test
only (never through mcp-redis).
"""

from __future__ import annotations

import pytest
import redis.asyncio as aioredis
from mcp_common.config import CommonSettings, load_settings
from mcp_redis.client import RedisClient
from mcp_redis.read_api import RedisReadApi
from mcp_redis.settings import Settings

pytestmark = pytest.mark.live


@pytest.mark.asyncio
async def test_live_acl_startup_check_and_read_paths() -> None:
    settings = load_settings(Settings, source="redis")
    common = CommonSettings()
    admin = aioredis.from_url("redis://localhost:6379/0")
    await admin.hset("mcp-it:session", mapping={"user": "1", "token": "abcdefgh12345678"})
    client = RedisClient(settings, common=common)
    try:
        report = await client.verify_credentials()
        assert report.ok, report.reasons
        api = RedisReadApi(client, common)
        got = await api.get_key(key="mcp-it:session")
        assert got.result.status.value == "ok" and got.result.meta.redactions >= 1
        assert (await api.get_key(key="mcp-it:missing")).result.status.value == "not_found"
    finally:
        await admin.delete("mcp-it:session")
        await admin.aclose()
        await client.aclose()
