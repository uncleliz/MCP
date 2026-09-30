"""T-010: mcp_common.http — timeout budget + retry (ADR-0006).

Network delays are scaled down (e.g. connect=0.03s/read=0.07s instead of 3s/7s) so
this suite stays fast in CI while proving the exact same shape as the production
budget; a separate, non-timing test pins the literal ADR-0006 A2 production
constants (3s/7s/1 retry/25s deadline) so the real numbers are still covered.
"""

from __future__ import annotations

import asyncio
import time

import httpx
import pytest
import respx
from mcp_common.config import CommonSettings
from mcp_common.errors import ErrorCode, ToolError
from mcp_common.http import build_client, request_with_retry


def test_default_settings_match_adr_0006_a2_production_constants() -> None:
    settings = CommonSettings()
    assert settings.http_connect_timeout == 3.0
    assert settings.http_read_timeout == 7.0
    assert settings.http_max_retries == 1
    assert settings.http_backoff_base == 1.0
    assert settings.tool_deadline == 25.0

    attempts = settings.http_max_retries + 1
    worst_case = attempts * (settings.http_connect_timeout + settings.http_read_timeout)
    worst_case += settings.http_backoff_base
    assert attempts == 2
    assert worst_case == 21.0
    assert worst_case < settings.tool_deadline


@pytest.mark.respx(base_url="https://dead.example.test")
@pytest.mark.asyncio
async def test_read_timeout_retries_exactly_once_then_upstream_timeout(
    respx_mock: respx.MockRouter,
) -> None:
    async def _slow_then_timeout(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.05)
        raise httpx.ReadTimeout("simulated slow endpoint", request=request)

    route = respx_mock.get("/x").mock(side_effect=_slow_then_timeout)

    async with build_client() as client:
        start = time.monotonic()
        with pytest.raises(ToolError) as exc_info:
            await request_with_retry(
                client,
                "GET",
                "https://dead.example.test/x",
                source="confluence",
                max_retries=1,
                backoff_base=0.05,
                deadline_s=0.5,
            )
        elapsed = time.monotonic() - start

    assert exc_info.value.code == ErrorCode.UPSTREAM_TIMEOUT
    assert exc_info.value.retryable is True
    assert route.call_count == 2  # exactly 2 attempts (1 retry), ADR-0006 A2
    # 2 * 0.05 (simulated delay) + 0.05 (backoff) ~= 0.15s, well under the 0.5s deadline.
    assert elapsed < 0.5


@pytest.mark.respx(base_url="https://dead.example.test")
@pytest.mark.asyncio
async def test_connect_error_maps_to_upstream_unavailable(
    respx_mock: respx.MockRouter,
) -> None:
    route = respx_mock.get("/y").mock(side_effect=httpx.ConnectError("connection refused"))

    async with build_client() as client:
        with pytest.raises(ToolError) as exc_info:
            await request_with_retry(
                client,
                "GET",
                "https://dead.example.test/y",
                source="gitlab",
                max_retries=1,
                backoff_base=0.01,
                deadline_s=0.5,
            )

    assert exc_info.value.code == ErrorCode.UPSTREAM_UNAVAILABLE
    assert route.call_count == 2


@pytest.mark.respx(base_url="https://rate-limited.example.test")
@pytest.mark.asyncio
async def test_retry_after_beyond_budget_returns_rate_limited_without_sleeping(
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.get("/z").mock(
        return_value=httpx.Response(429, headers={"Retry-After": "120"})
    )

    async with build_client() as client:
        start = time.monotonic()
        with pytest.raises(ToolError) as exc_info:
            await request_with_retry(
                client,
                "GET",
                "https://rate-limited.example.test/z",
                source="gitlab",
                max_retries=1,
                backoff_base=0.01,
                deadline_s=1.0,
            )
        elapsed = time.monotonic() - start

    assert exc_info.value.code == ErrorCode.RATE_LIMITED
    assert exc_info.value.retry_after_s == 120
    # Must NOT have slept anywhere near 120s — it should fail fast.
    assert elapsed < 1.0


@pytest.mark.respx(base_url="https://rate-limited.example.test")
@pytest.mark.asyncio
async def test_retry_after_within_budget_is_honored(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.get("/w")
    route.side_effect = [
        httpx.Response(429, headers={"Retry-After": "0"}),
        httpx.Response(200, json={"ok": True}),
    ]

    async with build_client() as client:
        response = await request_with_retry(
            client,
            "GET",
            "https://rate-limited.example.test/w",
            source="gitlab",
            max_retries=1,
            backoff_base=0.01,
            deadline_s=2.0,
        )

    assert response.status_code == 200
    assert route.call_count == 2


@pytest.mark.respx(base_url="https://upstream.example.test")
@pytest.mark.asyncio
async def test_5xx_is_retried_then_raises_upstream_unavailable(
    respx_mock: respx.MockRouter,
) -> None:
    route = respx_mock.get("/svc").mock(return_value=httpx.Response(503))

    async with build_client() as client:
        with pytest.raises(ToolError) as exc_info:
            await request_with_retry(
                client,
                "GET",
                "https://upstream.example.test/svc",
                source="opensearch",
                max_retries=1,
                backoff_base=0.01,
                deadline_s=1.0,
            )

    assert exc_info.value.code == ErrorCode.UPSTREAM_UNAVAILABLE
    assert route.call_count == 2


@pytest.mark.respx(base_url="https://upstream.example.test")
@pytest.mark.asyncio
async def test_non_idempotent_method_is_never_retried(respx_mock: respx.MockRouter) -> None:
    # POST is not GET/HEAD; even if allowlisted at the transport layer, the retry
    # policy only ever retries idempotent methods (ADR-0006 Decision).
    route = respx_mock.post("/_search").mock(return_value=httpx.Response(503))

    async with build_client() as client:
        with pytest.raises(ToolError):
            await request_with_retry(
                client,
                "POST",
                "https://upstream.example.test/_search",
                source="opensearch",
                max_retries=1,
                backoff_base=0.01,
                deadline_s=1.0,
            )

    assert route.call_count == 1


@pytest.mark.respx(base_url="https://ok.example.test")
@pytest.mark.asyncio
async def test_successful_response_is_returned_as_is(respx_mock: respx.MockRouter) -> None:
    respx_mock.get("/ok").mock(return_value=httpx.Response(200, json={"hello": "world"}))

    async with build_client() as client:
        response = await request_with_retry(
            client, "GET", "https://ok.example.test/ok", source="confluence"
        )

    assert response.status_code == 200
    assert response.json() == {"hello": "world"}
