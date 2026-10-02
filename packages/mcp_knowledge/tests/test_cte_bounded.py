"""T-101 / TC-094 (FR-020/AC-003): the recursive CTE is bounded, cycle-safe, fan-out capped.

No database is needed to prove the three structural guarantees are expressed in the SQL text and in
the parameters the tool passes: depth is bounded by `max_depth` (itself clamped to MAX_HOPS=3), the
walk carries a visited-path array and excludes any destination already on it (cycle detection), and
each hop's expansion is `LIMIT %(fanout)s` (fan-out cap). The DB-side behaviour on a real cyclic +
high-fan-out graph is covered by the integration test (`test_integration.py`, @pytest.mark.live).
"""

from __future__ import annotations

import pytest
from knowledge_helpers import FakeKnowledgeClient, FakeRetriever
from mcp_common.errors import ToolError
from mcp_knowledge.domain_sql import DOMAIN_STATEMENTS, FANOUT_LIMIT, MAX_HOPS
from mcp_knowledge.tools.read_api import KnowledgeReadApi

_SQL = DOMAIN_STATEMENTS["related_knowledge"]


def test_max_hops_is_three() -> None:
    assert MAX_HOPS == 3


def test_cte_is_bounded_by_max_depth() -> None:
    assert "WITH RECURSIVE" in _SQL
    assert "w.depth < %(max_depth)s" in _SQL  # the recursion terminates at max_depth


def test_cte_detects_cycles_with_a_visited_path() -> None:
    # the seed builds a path array; each hop excludes a dst already on the path.
    assert "ARRAY[e.id] AS path" in _SQL
    assert "w.path || nbr.dst_id" in _SQL
    assert "NOT (r.dst_entity_id = ANY(w.path))" in _SQL


def test_cte_caps_fan_out_per_hop() -> None:
    assert "LIMIT %(fanout)s" in _SQL  # per-expansion fan-out cap
    assert "LIMIT %(total_limit)s" in _SQL  # overall result cap


async def test_tool_clamps_depth_and_passes_bounds() -> None:
    captured: dict[str, object] = {}

    class _Tx:
        async def fetch(self, name, params=None):
            if name == "entity_by_name_any_type":
                return [{"id": "11111111-1111-4111-8111-111111111111"}]
            if name == "related_knowledge":
                captured.update(params or {})
            return []

    class _Client:
        def read_tx(self):
            import contextlib

            @contextlib.asynccontextmanager
            async def cm():
                yield _Tx()

            return cm()

    api = KnowledgeReadApi(_Client(), FakeRetriever([]))
    await api.find_related_knowledge(entity="payment-service", max_depth=3)
    assert captured["max_depth"] == 3
    assert captured["fanout"] == FANOUT_LIMIT
    assert captured["total_limit"] == FANOUT_LIMIT * MAX_HOPS


async def test_tool_rejects_depth_above_max() -> None:
    api = KnowledgeReadApi(FakeKnowledgeClient(), FakeRetriever([]))
    with pytest.raises(ToolError):
        await api.find_related_knowledge(entity="x", max_depth=4)


async def test_tool_rejects_unknown_rel_type() -> None:
    api = KnowledgeReadApi(FakeKnowledgeClient(), FakeRetriever([]))
    with pytest.raises(ToolError):
        await api.find_related_knowledge(entity="x", rel_types=["sabotages"])
