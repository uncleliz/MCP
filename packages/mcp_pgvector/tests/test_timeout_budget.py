"""T-062/T-066 / NFR-002 + TC-064 + R16: a dead or slow Postgres fails fast and a hung embedding
call cannot silently queue every later question."""

from __future__ import annotations

import asyncio
import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from mcp_common.config import CommonSettings
from mcp_common.contract_testing import load_contract, validate_structured_content
from mcp_common.errors import ToolError
from mcp_common.runtime import BoundedExecutor
from mcp_pgvector.client import PgVectorClient
from mcp_pgvector.read_api import PgVectorReadApi
from mcp_pgvector.server import build_server
from mcp_pgvector.settings import Settings
from pg_helpers import NOW, FakeClient, make_api, provider, real_api
from pydantic import SecretStr

CONTRACT = load_contract()


@contextmanager
def _silent_server() -> Iterator[int]:
    """Accepts TCP connections (kernel backlog) and never answers: a black-holed Postgres."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(8)
    try:
        yield sock.getsockname()[1]
    finally:
        sock.close()


@pytest.mark.asyncio
async def test_TC_064_a_silent_postgres_times_out_at_connect_timeout_3s(
    common: CommonSettings,
) -> None:
    with _silent_server() as port:
        dsn = f"postgresql://mcp_query_ro:pw@127.0.0.1:{port}/kb"
        client = PgVectorClient(Settings(dsn=SecretStr(dsn)), common=common)
        started = time.monotonic()
        with pytest.raises(ToolError) as exc:
            async with client.read_tx():
                pass  # pragma: no cover - never reached
        elapsed = time.monotonic() - started
    assert 2.5 <= elapsed < 6, elapsed  # connect_timeout=3, far below the 25s tool deadline
    assert exc.value.code.value in {"upstream_timeout", "upstream_unavailable"}
    assert "VPN" in exc.value.details["hint"] and "pw" not in str(exc.value.details)


@pytest.mark.asyncio
async def test_TC_064_a_connection_refused_fails_immediately_with_the_vpn_hint(
    common: CommonSettings,
) -> None:
    client = PgVectorClient(
        Settings(dsn=SecretStr("postgresql://u:p@127.0.0.1:1/kb")), common=common
    )
    started = time.monotonic()
    with pytest.raises(ToolError) as exc:
        async with client.read_tx():
            pass  # pragma: no cover
    assert time.monotonic() - started < 2
    assert exc.value.code.value == "upstream_unavailable" and exc.value.retryable


@pytest.mark.asyncio
async def test_TC_064_statement_timeout_cancels_a_slow_query_as_upstream_timeout(
    ro_dsn: str, seeded_ids, common: CommonSettings
) -> None:
    client = PgVectorClient(Settings(dsn=SecretStr(ro_dsn)), common=common)
    try:
        started = time.monotonic()
        with pytest.raises(ToolError) as exc:
            async with client.read_tx() as tx:
                await tx._conn.execute("SET LOCAL statement_timeout = 200")  # noqa: SLF001
                await tx._conn.execute("SELECT pg_sleep(5)")  # noqa: SLF001
        assert time.monotonic() - started < 3
        assert (
            exc.value.code.value == "upstream_timeout" and "statement_timeout" in exc.value.message
        )
        async with client.read_tx() as tx:  # the connection recovered
            assert (await tx.fetch("schema_info"))[0]["has_chunks"] is True
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_a_cancelled_call_drops_the_connection_and_the_next_one_reconnects(
    ro_dsn: str, seeded_ids, common: CommonSettings
) -> None:
    client = PgVectorClient(Settings(dsn=SecretStr(ro_dsn)), common=common)

    async def slow() -> None:
        async with client.read_tx() as tx:
            await tx._conn.execute("SELECT pg_sleep(5)")  # noqa: SLF001

    try:
        task = asyncio.create_task(slow())
        await asyncio.sleep(0.3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert client._conn is None  # noqa: SLF001 - unknown state was discarded
        async with client.read_tx() as tx:
            assert (await tx.fetch("schema_info"))[0]["has_documents"] is True
    finally:
        await client.aclose()


class _HungProvider:
    model_id = "fake/hashed-bow"
    dimensions = 8
    max_input_tokens = 8
    normalize = True

    def __init__(self) -> None:
        self.release = threading.Event()
        self.started = 0

    def embed_query(self, text: str) -> list[float]:
        self.started += 1
        self.release.wait(15)
        return [0.0] * 8

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return []


async def _call(server, name: str, args: dict):
    async with create_connected_server_and_client_session(server) as session:
        return await session.call_tool(name, arguments=args)


@pytest.mark.asyncio
async def test_R16_hung_embedding_calls_then_the_next_question_is_upstream_unavailable(
    common: CommonSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MCP_TOOL_DEADLINE_KB_SEMANTIC_SEARCH", "0.3")
    hung = _HungProvider()
    api = PgVectorReadApi(
        FakeClient(),
        hung,
        common,
        executor=BoundedExecutor(2),
        now=lambda: NOW,  # type: ignore[arg-type]
    )
    server = build_server(api, common=common)
    try:
        for _ in range(2):
            res = await _call(server, "kb_semantic_search", {"query": "payment retry"})
            assert res.structuredContent["error"]["code"] == "upstream_timeout"
        assert hung.started == 2
        started = time.monotonic()
        third = await _call(server, "kb_semantic_search", {"query": "payment retry"})
        assert time.monotonic() - started < 0.25  # immediate, not another 0.3s wait
        error = third.structuredContent["error"]
        assert error["code"] == "upstream_unavailable" and error["retryable"] is True
        assert hung.started == 2
        validate_structured_content(
            CONTRACT, "kb_semantic_search", third.structuredContent, is_error=True
        )
    finally:
        hung.release.set()
        await asyncio.sleep(0.05)
        api._executor.shutdown(wait=True)  # noqa: SLF001


@pytest.mark.asyncio
async def test_a_tool_call_is_bounded_by_the_tool_deadline_not_by_the_database(
    common: CommonSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    class SlowClient(FakeClient):
        async def capabilities(self):
            await asyncio.sleep(5)
            return await super().capabilities()

    monkeypatch.setenv("MCP_TOOL_DEADLINE_KB_SEMANTIC_SEARCH", "0.3")
    server = build_server(make_api(SlowClient(), common))
    started = time.monotonic()
    res = await _call(server, "kb_semantic_search", {"query": "payment retry"})
    assert time.monotonic() - started < 2
    assert res.structuredContent["error"]["code"] == "upstream_timeout"


@pytest.mark.asyncio
async def test_real_api_helper_is_wired_to_the_fake_provider(
    ro_dsn: str, seeded_ids, common: CommonSettings
) -> None:
    api = real_api(ro_dsn, common)
    assert provider(1024).dimensions == 1024
    assert (await api.semantic_search(query="payment retry")).result.status.value == "ok"
    await api._client.aclose()  # noqa: SLF001
