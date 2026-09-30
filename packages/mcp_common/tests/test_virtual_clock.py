"""mcp_common.testing.VirtualClock — proves the ADR-0006 A2 budget arithmetic without waiting."""

from __future__ import annotations

import httpx
import pytest
import respx
from mcp_common.errors import ErrorCode, ToolError
from mcp_common.http import build_client, request_with_retry
from mcp_common.testing import VirtualClock, virtual_http_clock

__all__ = ["virtual_http_clock"]


@pytest.mark.asyncio
async def test_production_budget_is_21_virtual_seconds_and_two_attempts(
    virtual_http_clock: VirtualClock,
) -> None:
    async def dead(request: httpx.Request) -> httpx.Response:
        virtual_http_clock.advance(3 + 7)  # connect + read timeout of one attempt
        raise httpx.ReadTimeout("dead endpoint", request=request)

    with respx.mock(assert_all_called=False) as router:
        route = router.get("https://dead.example.test/x").mock(side_effect=dead)
        async with build_client() as client:
            with pytest.raises(ToolError) as exc:
                await request_with_retry(client, "GET", "https://dead.example.test/x", source="x")
    assert exc.value.code == ErrorCode.UPSTREAM_TIMEOUT
    assert route.call_count == 2
    assert virtual_http_clock.now == pytest.approx(21.0)
    assert virtual_http_clock.now < 25.0
    assert virtual_http_clock.sleeps == [1.0]


def test_clock_primitives() -> None:
    clock = VirtualClock()
    clock.advance(2.5)
    assert clock.monotonic() == 2.5
