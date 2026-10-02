"""T-109 (E7, ADR-0021) — token-bucket rate-limit (FR-022/AC-002, TC-102).

Uses a VirtualClock so the token-bucket refill arithmetic is asserted without sleeping.
"""

from __future__ import annotations

import json

import pytest
from mcp_common.errors import ErrorCode, ToolError
from mcp_common.gateway import Gateway, RateLimiter, TokenBucket
from mcp_common.testing import VirtualClock


async def _ok_handler(args, auth):  # type: ignore[no-untyped-def]
    return {"ok": True}


@pytest.mark.asyncio
async def test_AC_002_excess_calls_rejected_with_rate_limited_and_retry_after(
    capsys: pytest.CaptureFixture[str],
) -> None:
    clock = VirtualClock()
    # Capacity 2, refill 1 token/sec: the 3rd rapid call (no time advance) is refused.
    gw = Gateway(rate_limit_capacity=2, rate_limit_refill_per_s=1.0, clock=clock.monotonic)
    gw.register("mcp-knowledge", "search_company_knowledge", _ok_handler)

    assert await gw.dispatch("search_company_knowledge", {}) == {"ok": True}
    assert await gw.dispatch("search_company_knowledge", {}) == {"ok": True}

    with pytest.raises(ToolError) as exc:
        await gw.dispatch("search_company_knowledge", {})

    err = exc.value
    assert err.code == ErrorCode.RATE_LIMITED
    assert err.source == "mcp-gateway"
    # Carries retry_after_s and is itself retryable — not an upstream overload.
    assert err.retry_after_s is not None and err.retry_after_s >= 1
    assert err.retryable is True


@pytest.mark.asyncio
async def test_AC_002_rate_limit_rejection_is_audited_to_stderr(
    capsys: pytest.CaptureFixture[str],
) -> None:
    clock = VirtualClock()
    gw = Gateway(rate_limit_capacity=1, rate_limit_refill_per_s=1.0, clock=clock.monotonic)
    gw.register("mcp-knowledge", "get_service", _ok_handler)

    await gw.dispatch("get_service", {})
    with pytest.raises(ToolError):
        await gw.dispatch("get_service", {})

    captured = capsys.readouterr()
    assert captured.out == ""  # audit never on stdout
    record = json.loads(captured.err.strip().splitlines()[-1])
    assert record["status"] == "rate_limited"
    assert record["error_code"] == ErrorCode.RATE_LIMITED.value


@pytest.mark.asyncio
async def test_AC_002_tokens_refill_over_time_and_call_is_admitted_again() -> None:
    clock = VirtualClock()
    gw = Gateway(rate_limit_capacity=1, rate_limit_refill_per_s=1.0, clock=clock.monotonic)
    gw.register("mcp-knowledge", "get_repository", _ok_handler)

    await gw.dispatch("get_repository", {})  # consumes the only token
    with pytest.raises(ToolError):
        await gw.dispatch("get_repository", {})  # empty

    clock.advance(1.0)  # one token refilled
    assert await gw.dispatch("get_repository", {}) == {"ok": True}


@pytest.mark.asyncio
async def test_rate_limit_is_per_routed_key_so_one_tool_does_not_starve_another() -> None:
    clock = VirtualClock()
    gw = Gateway(rate_limit_capacity=1, rate_limit_refill_per_s=1.0, clock=clock.monotonic)
    gw.register("mcp-knowledge", "search_company_knowledge", _ok_handler)
    gw.register("mcp-jira", "jira_search_issues", _ok_handler)

    # Exhaust the knowledge tool's bucket...
    await gw.dispatch("search_company_knowledge", {})
    with pytest.raises(ToolError):
        await gw.dispatch("search_company_knowledge", {})

    # ...the Jira tool's bucket is independent and still has its token.
    assert await gw.dispatch("jira_search_issues", {}) == {"ok": True}


def test_token_bucket_retry_after_rounds_up_to_whole_seconds() -> None:
    clock = VirtualClock()
    bucket = TokenBucket(capacity=1, refill_per_s=0.5, clock=clock.monotonic)
    assert bucket.try_consume() is True  # take the one token
    assert bucket.try_consume() is False
    # 1 token / 0.5 per sec = 2s to refill one token.
    assert bucket.retry_after_s() == 2


def test_token_bucket_rejects_non_positive_config() -> None:
    with pytest.raises(ValueError):
        TokenBucket(capacity=0, refill_per_s=1.0)
    with pytest.raises(ValueError):
        TokenBucket(capacity=1, refill_per_s=0)


def test_rate_limiter_check_reports_allowed_then_retry_after() -> None:
    clock = VirtualClock()
    limiter = RateLimiter(capacity=1, refill_per_s=1.0, clock=clock.monotonic)
    allowed, retry = limiter.check("mcp-knowledge:get_service")
    assert allowed is True and retry == 0
    allowed, retry = limiter.check("mcp-knowledge:get_service")
    assert allowed is False and retry >= 1
