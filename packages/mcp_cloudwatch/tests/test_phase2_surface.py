"""T-053 (automated part): the Phase 2 surface as Claude would see it, and its NFR-002 budgets.

* 25 tools across the 5 servers (6 + 3 + 7 + 5 + 4) and 40 after Phase 1 (15 + 25), 0 write tools.
  `opensearch_search_dsl` is a feature-flagged escape hatch (contract `x-feature-flag`,
  default OFF), so the 25th tool is only listed with `MCP_OPENSEARCH_ALLOW_DSL=true`; by default
  Claude sees 24 Phase 2 tools / 39 in total.
* Every source's worst-case time to fail on a dead endpoint stays below the 25s tool deadline.

The manual half of sign-off (live `doctor`, Claude Desktop registration) is tracked in
docs/signoff/phase-2.md; it needs real credentials/VPN and cannot run in CI.
"""

from __future__ import annotations

import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from mcp_cloudwatch.client import (
    CONNECT_TIMEOUT_S as AWS_CONNECT,
)
from mcp_cloudwatch.client import (
    MAX_ATTEMPTS as AWS_ATTEMPTS,
)
from mcp_cloudwatch.client import (
    READ_TIMEOUT_S as AWS_READ,
)
from mcp_cloudwatch.server import build_server as build_cloudwatch
from mcp_common.config import CommonSettings
from mcp_common.contract_testing import load_contract, operations_by_id
from mcp_common.tooling import registered_tool_functions
from mcp_confluence.server import build_server as build_confluence
from mcp_gitlab.server import build_server as build_gitlab
from mcp_kafka.client import TIMEOUT_S as KAFKA_CALL_TIMEOUT
from mcp_kafka.server import build_server as build_kafka
from mcp_kibana.server import build_server as build_kibana
from mcp_opensearch.client import CHEAP_REQUEST_TIMEOUT_S as OS_CHEAP
from mcp_opensearch.server import build_server as build_opensearch
from mcp_redis.client import CONNECT_TIMEOUT_S as REDIS_CONNECT
from mcp_redis.client import READ_TIMEOUT_S as REDIS_READ
from mcp_redis.server import build_server as build_redis

PHASE2 = {
    "opensearch": (lambda: build_opensearch(allow_dsl=True), 6),
    "kibana": (build_kibana, 3),
    "cloudwatch": (build_cloudwatch, 7),
    "kafka": (build_kafka, 5),
    "redis": (build_redis, 4),
}
WRITE_VERBS = ("create", "update", "delete", "put", "write", "set", "produce", "publish", "send")


async def _names(build) -> list[str]:
    async with create_connected_server_and_client_session(build()) as session:
        return [tool.name for tool in (await session.list_tools()).tools]


@pytest.mark.asyncio
async def test_NFR_005_phase2_servers_list_25_tools_and_40_with_phase1() -> None:
    names: list[str] = []
    for source, (build, expected) in PHASE2.items():
        listed = await _names(build)
        assert len(listed) == expected and all(n.startswith(f"{source}_") for n in listed)
        names += listed
    assert len(names) == 25 == len(set(names)) == 6 + 3 + 7 + 5 + 4
    phase1 = await _names(build_confluence) + await _names(build_gitlab)
    assert len(phase1) == 15 and len(set(phase1 + names)) == 40


@pytest.mark.asyncio
async def test_default_surface_hides_the_dsl_escape_hatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MCP_OPENSEARCH_ALLOW_DSL", raising=False)
    names = await _names(build_opensearch)
    assert len(names) == 5 and "opensearch_search_dsl" not in names


def test_NFR_001_every_phase2_tool_is_a_readonly_contract_operation_and_none_is_a_write() -> None:
    operations = operations_by_id(load_contract())
    for source, (build, _count) in PHASE2.items():
        for name in registered_tool_functions(build()):
            assert operations[name]["x-readonly"] is True, name
            assert operations[name]["x-side-effects"] == "none", name
            verbs = name.removeprefix(f"{source}_").split("_")
            assert not any(v in WRITE_VERBS for v in verbs), name


def test_NFR_002_every_source_fails_fast_below_the_25s_tool_deadline() -> None:
    """Worst-case wall time to give up on a dead endpoint, per source (THRESHOLD TBD: Open
    question 1 — the numbers follow ADR-0006 A2 / ADR-0008 A4 / ADR-0009 A3)."""
    deadline = CommonSettings().tool_deadline
    budget = {
        "kibana (httpx 2 x (3+7) + 1s backoff)": 2 * (3 + 7) + 1,
        "cloudwatch (boto3 2 x (connect+read))": AWS_ATTEMPTS * (AWS_CONNECT + AWS_READ),
        "opensearch (search/count: timeout_s 20 + 1)": 20 + 1,
        "opensearch (cheap calls)": OS_CHEAP,
        "kafka (per-call timeout)": KAFKA_CALL_TIMEOUT,
        "redis (connect + read)": REDIS_CONNECT + REDIS_READ,
    }
    for source, seconds in budget.items():
        assert seconds < deadline, source
    assert max(budget.values()) == 21  # the "≈21s" the sign-off checklist refers to
