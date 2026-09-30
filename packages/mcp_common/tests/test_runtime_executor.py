"""T-012: mcp_common.runtime.BoundedExecutor — R16 regression.

`asyncio.timeout` cancels the *coroutine*, never a thread already running a
synchronous SDK call — so a 5th call arriving while all 4 workers are stuck must
fail immediately with `upstream_unavailable`, not queue and wait.
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest
from mcp_common.errors import ErrorCode, ToolError
from mcp_common.runtime import BoundedExecutor


def _block_until_released(release_event: threading.Event) -> str:
    release_event.wait(timeout=5)
    return "done"


@pytest.mark.asyncio
async def test_nth_plus_one_call_fails_fast_when_capacity_is_saturated() -> None:
    executor = BoundedExecutor(max_workers=4)
    release_event = threading.Event()

    # Saturate all 4 worker slots with calls that block until we release them.
    in_flight = [
        asyncio.ensure_future(
            executor.run(_block_until_released, release_event, source="cloudwatch")
        )
        for _ in range(4)
    ]
    # Give the threads a moment to actually start running.
    await asyncio.sleep(0.05)

    start = time.monotonic()
    with pytest.raises(ToolError) as exc_info:
        await executor.run(_block_until_released, release_event, source="cloudwatch")
    elapsed = time.monotonic() - start

    assert exc_info.value.code == ErrorCode.UPSTREAM_UNAVAILABLE
    assert exc_info.value.retryable is True
    # Must fail near-instantly, not wait for a free worker.
    assert elapsed < 0.5

    release_event.set()
    results = await asyncio.gather(*in_flight)
    assert results == ["done"] * 4
    executor.shutdown()


@pytest.mark.asyncio
async def test_slot_is_freed_after_completion_and_next_call_succeeds() -> None:
    executor = BoundedExecutor(max_workers=2)
    release_event = threading.Event()

    first = asyncio.ensure_future(
        executor.run(_block_until_released, release_event, source="cloudwatch")
    )
    second = asyncio.ensure_future(
        executor.run(_block_until_released, release_event, source="cloudwatch")
    )
    await asyncio.sleep(0.05)
    release_event.set()
    await asyncio.gather(first, second)

    # Both slots are free again now.
    release_event_2 = threading.Event()
    release_event_2.set()
    result = await executor.run(_block_until_released, release_event_2, source="cloudwatch")
    assert result == "done"
    executor.shutdown()


@pytest.mark.asyncio
async def test_host_hint_is_included_in_upstream_unavailable_details() -> None:
    executor = BoundedExecutor(max_workers=1)
    release_event = threading.Event()
    first = asyncio.ensure_future(
        executor.run(
            _block_until_released, release_event, source="kafka", host="kafka.internal:9092"
        )
    )
    await asyncio.sleep(0.05)

    with pytest.raises(ToolError) as exc_info:
        await executor.run(
            _block_until_released, release_event, source="kafka", host="kafka.internal:9092"
        )

    assert "kafka.internal:9092" in exc_info.value.details["hint"]

    release_event.set()
    await first
    executor.shutdown()
