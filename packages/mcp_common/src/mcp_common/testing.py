"""T-014: `mcp_common.testing` — the shared verification fixtures BE's unit tests and
QA's harness both reuse (NFR-001, ADR-0003, ADR-0004, ADR-0013).

Four pieces:

* :func:`assert_readonly_tool_surface` — scans the *real* registered tool surface
  (not just what a snapshot file claims) against `api-contract.yaml`: every tool must
  be `@readonly_tool`-marked, every matching contract operation must carry
  `x-readonly: true` / `x-side-effects: none`, and `tools.snapshot.json` must be a
  subset of the contract's non-CLI operations. Also covers operations `mcp-ingest`'s
  connector calls directly on `client.py` (ADR-0012 A4), via `additional_client_operations`.
* :func:`assert_envelope_invariants` — the `ToolResult` invariants the contract
  itself calls out as *not* JSON-Schema-encodable (citation_ref bounds,
  `meta.returned == len(items)`, per-source-type `uri` requirements).
* :func:`assert_unknown_tool_rejected_at_protocol_layer` — FR-014 AC-002 lives at the
  MCP protocol layer, not in `ErrorEnvelope`: calling a tool name that was never
  registered must never reach our own tool code or envelope-building at all.
* :func:`warn_if_tool_name_matches_deny_regex` — the old deny-regex-by-name check is
  now only ever a *warning* (ADR-0003 A2: it false-positived on
  `gitlab_list_merge_requests`/`gitlab_get_merge_request`), never a failure.
* :func:`readonly_respx_router` — a pytest fixture bundling a respx router with an
  automatic check, at teardown, that every recorded call was GET/HEAD or explicitly
  allowlisted — independent of whether the code under test actually went through
  `mcp_common.http.build_client`.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from types import SimpleNamespace
from typing import Any

import pytest
import respx
from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session

from mcp_common.envelope import SourceType, ToolResult
from mcp_common.errors import NotPermittedError
from mcp_common.http import (
    DEFAULT_TRANSPORT_ALLOWLIST,
    TransportAllowlistEntry,
    assert_transport_allowed,
)
from mcp_common.readonly import is_readonly_tool

__all__ = [
    "assert_readonly_tool_surface",
    "assert_envelope_invariants",
    "assert_unknown_tool_rejected_at_protocol_layer",
    "warn_if_tool_name_matches_deny_regex",
    "assert_all_calls_readonly",
    "readonly_respx_router",
    "VirtualClock",
    "virtual_http_clock",
]

# Demoted to a warning-only heuristic by ADR-0003 A2 — it false-positived on
# `gitlab_list_merge_requests`/`gitlab_get_merge_request`. Kept only as a non-blocking
# signal a human can look at; never fails a test by itself.
_DENY_NAME_REGEX = re.compile(
    r"create|update|delete|put|post|write|set|del|publish|send|produce|purge|"
    r"merge|push|expire|flush|drop|truncate",
    re.IGNORECASE,
)

# Contract-source-types that always resolve to a real URL (contract invariant 6).
_URL_REQUIRED_SOURCE_TYPES = frozenset(
    {SourceType.CONFLUENCE, SourceType.GITLAB, SourceType.KIBANA, SourceType.PGVECTOR}
)


def warn_if_tool_name_matches_deny_regex(tool_names: Iterable[str]) -> list[str]:
    """Return tool names matching the legacy write-verb-in-name heuristic.

    Purely informational (ADR-0003 A2) — callers should log/warn on a non-empty
    result, never fail a test on it.
    """
    return [name for name in tool_names if _DENY_NAME_REGEX.search(name)]


def assert_readonly_tool_surface(
    *,
    registered_tools: Mapping[str, Callable[..., Any]],
    contract_operations: Mapping[str, Mapping[str, Any]],
    snapshot: Mapping[str, Any],
    client_allowlist: Sequence[str] = (),
    additional_client_operations: Sequence[str] = (),
) -> None:
    """Assert the full read-only tool surface (NFR-001, ADR-0003 layer 1/5).

    `contract_operations` is `api-contract.yaml`'s `paths.*.<method>` operations
    keyed by `operationId`. `snapshot` is a loaded `tools.snapshot.json`.
    `additional_client_operations` covers methods `mcp-ingest`'s connector calls
    directly on `client.py`, bypassing the tool surface entirely (ADR-0012 A4) — they
    must already be present in `client_allowlist`.

    Raises `AssertionError` listing *every* violation found, not just the first.
    """
    violations: list[str] = []

    for name, func in registered_tools.items():
        if not is_readonly_tool(func):
            violations.append(f"tool '{name}' is not marked @readonly_tool")

        operation = contract_operations.get(name)
        if operation is None:
            violations.append(f"tool '{name}' has no matching operation in api-contract.yaml")
            continue
        if operation.get("x-readonly") is not True:
            violations.append(f"operation '{name}' is missing x-readonly: true")
        if operation.get("x-side-effects") != "none":
            violations.append(f"operation '{name}' is missing x-side-effects: none")

    non_cli_operation_ids = {
        op_id
        for op_id, operation in contract_operations.items()
        if operation.get("x-interface") != "cli"
    }
    extra_in_snapshot = set(snapshot) - non_cli_operation_ids
    if extra_in_snapshot:
        violations.append(
            "tools.snapshot.json has entries outside the contract's non-CLI operations: "
            f"{sorted(extra_in_snapshot)}"
        )

    for operation_name in additional_client_operations:
        if operation_name not in client_allowlist:
            violations.append(
                f"mcp-ingest-reused operation '{operation_name}' is not in client.py's allowlist"
            )

    if violations:
        raise AssertionError(
            "Read-only tool surface violations:\n" + "\n".join(f"- {v}" for v in violations)
        )


def assert_envelope_invariants(result: ToolResult) -> None:
    """Check the `ToolResultBase` invariants the contract calls out as not
    JSON-Schema-encodable (invariants 4-6 in `components.schemas.ToolResultBase`)."""
    violations: list[str] = []
    citation_count = len(result.citations)

    for index, item in enumerate(result.items):
        ref = item.get("citation_ref")
        if not isinstance(ref, int) or not (0 <= ref < citation_count):
            violations.append(
                f"items[{index}].citation_ref={ref!r} is out of bounds "
                f"(citations has {citation_count} entries)"
            )

    if result.meta.returned != len(result.items):
        violations.append(
            f"meta.returned={result.meta.returned} != len(items)={len(result.items)}"
        )

    for index, citation in enumerate(result.citations):
        if citation.source_type in _URL_REQUIRED_SOURCE_TYPES and citation.uri is None:
            violations.append(
                f"citations[{index}].uri is None but source_type="
                f"{citation.source_type.value} always resolves to a URL"
            )

    if violations:
        raise AssertionError(
            "Envelope invariant violations:\n" + "\n".join(f"- {v}" for v in violations)
        )


async def assert_unknown_tool_rejected_at_protocol_layer(
    server: FastMCP, tool_name: str, arguments: dict[str, Any] | None = None
) -> None:
    """FR-014 AC-002: calling a tool name that was never registered must be rejected
    by the MCP SDK itself, before our code or `ErrorEnvelope` is ever involved.

    Uses the SDK's in-memory client/server session (no subprocess needed, per
    ADR-0002). With the installed SDK version, an unknown tool comes back as a
    `CallToolResult(isError=True, structuredContent=None)` rather than a raised
    exception — `structuredContent is None` is exactly the signal that our own
    envelope-building code was never reached.
    """
    async with create_connected_server_and_client_session(server) as session:
        result = await session.call_tool(tool_name, arguments=arguments or {})

    if not result.isError:
        raise AssertionError(
            f"expected calling unregistered tool '{tool_name}' to be rejected, but it succeeded"
        )
    if result.structuredContent is not None:
        raise AssertionError(
            "expected the SDK's own protocol-layer rejection (structuredContent=None) "
            f"for unregistered tool '{tool_name}', but got structuredContent="
            f"{result.structuredContent!r} — looks like an ErrorEnvelope leaked through instead"
        )


def assert_all_calls_readonly(
    router: respx.Router,
    allowlist: Sequence[TransportAllowlistEntry] = DEFAULT_TRANSPORT_ALLOWLIST,
) -> None:
    """Assert every request respx recorded was GET/HEAD or explicitly allowlisted.

    Independent of whether the code under test went through
    `mcp_common.http.build_client` — this inspects what actually went out.
    """
    violations: list[str] = []
    for call in router.calls:
        request = call.request
        try:
            assert_transport_allowed(request.method, request.url, allowlist)
        except NotPermittedError as exc:
            violations.append(str(exc))

    if violations:
        raise AssertionError(
            "Disallowed requests observed by respx:\n" + "\n".join(f"- {v}" for v in violations)
        )


@pytest.fixture
def readonly_respx_router() -> Iterator[respx.MockRouter]:
    """A respx router that asserts, at teardown, every recorded call was read-only."""
    with respx.mock(assert_all_called=False) as router:
        yield router
        assert_all_calls_readonly(router)


class VirtualClock:
    """A fake monotonic clock for `mcp_common.http`'s retry budget.

    Lets NFR-002 tests assert the real ADR-0006 A2 arithmetic (`2 * (3 + 7) + 1 = 21s < 25s`)
    without sleeping 21 real seconds: the simulated endpoint calls :meth:`advance` by the
    time a real attempt would burn, and backoff sleeps advance the clock instead of waiting.
    """

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def virtual_http_clock(monkeypatch: pytest.MonkeyPatch) -> VirtualClock:
    """Swap `mcp_common.http`'s `time`/`asyncio.sleep` for a :class:`VirtualClock`."""
    import mcp_common.http as http_module

    clock = VirtualClock()
    monkeypatch.setattr(http_module, "time", SimpleNamespace(monotonic=clock.monotonic))
    monkeypatch.setattr(http_module, "asyncio", SimpleNamespace(sleep=clock.sleep))
    return clock
