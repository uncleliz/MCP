"""T-095 — HybridRetriever: vector ∪ keyword legs fused by RRF, read-only, deterministic.

Uses a scripted fake client (no database) to exercise the fusion, the leg provenance, the top_k /
per_leg clamps and the read-only invariant that only named statements are ever run. The SQL itself
(deleted_at IS NULL, tsvector @@, model filter) is exercised against a real pgvector in
`test_integration.py`.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from mcp_ingest.embedding.fake import DeterministicFakeProvider
from mcp_knowledge.retrieval.client import ALLOWED_STATEMENTS
from mcp_knowledge.retrieval.hybrid import HybridQuery, HybridRetriever

NOW = datetime(2026, 10, 1, 6, 0, tzinfo=UTC)


def _row(chunk_id: int, source_type: str = "confluence", content: str = "retry backoff") -> dict:
    return {
        "chunk_id": chunk_id,
        "document_id": uuid.UUID("3f2504e0-4f89-11d3-9a0c-0305e82c3301"),
        "chunk_index": 0,
        "content": content,
        "heading_path": "Retry > Backoff",
        "embedding_model": "fake/hashed-bow",
        "source_type": source_type,
        "source_id": str(chunk_id),
        "source_uri": f"https://wiki.example.com/p/{chunk_id}",
        "title": "Payment retry",
        "container": "PAY",
        "author": "an.nguyen",
        "source_updated_at": NOW,
        "ingested_at": NOW,
    }


class FakeTx:
    def __init__(self, legs: dict[str, list[dict]], calls: list[str]) -> None:
        self._legs = legs
        self.calls = calls

    async def fetch(self, name: str, params: dict[str, Any] | None = None) -> list[dict]:
        assert name in ALLOWED_STATEMENTS  # read-only: only named statements
        self.calls.append(name)
        return [dict(r) for r in self._legs.get(name, [])]

    async def set_local(self, name: str, value: str) -> None:  # pragma: no cover - unused here
        self.calls.append("set_local")


class FakeClient:
    def __init__(self, legs: dict[str, list[dict]]) -> None:
        self._legs = legs
        self.calls: list[str] = []
        self.tx_count = 0

    @asynccontextmanager
    async def read_tx(self) -> AsyncIterator[FakeTx]:
        self.tx_count += 1
        yield FakeTx(self._legs, self.calls)


def _retriever(legs: dict[str, list[dict]]) -> tuple[HybridRetriever, FakeClient]:
    client = FakeClient(legs)
    provider = DeterministicFakeProvider(dimensions=8)
    return HybridRetriever(client, provider), client


async def test_fuses_both_legs_and_records_leg_provenance() -> None:
    legs = {
        "vector_leg": [_row(10), _row(20), _row(30)],
        "keyword_leg": [_row(30), _row(40)],
    }
    retriever, client = _retriever(legs)
    out = await retriever.retrieve(HybridQuery(text="payment retry backoff", top_k=10))
    by_id = {c.chunk_id: c for c in out}
    # chunk 30 is in both legs -> top of the fused list and carries both leg ranks.
    assert out[0].chunk_id == 30
    assert by_id[30].legs == {"vector": 3, "keyword": 1}
    assert by_id[10].legs == {"vector": 1}
    assert by_id[40].legs == {"keyword": 2}
    # one read-only transaction, exactly the two named leg statements.
    assert client.tx_count == 1
    assert client.calls == ["vector_leg", "keyword_leg"]


async def test_keyword_only_hit_still_returned() -> None:
    # The exact-identifier leg contributes even when the vector leg misses it entirely.
    legs = {"vector_leg": [], "keyword_leg": [_row(99, content="ERR_PAYMENT_TIMEOUT")]}
    retriever, _ = _retriever(legs)
    out = await retriever.retrieve(HybridQuery(text="ERR_PAYMENT_TIMEOUT"))
    assert [c.chunk_id for c in out] == [99]
    assert out[0].legs == {"keyword": 1}


async def test_top_k_clamped_and_output_cut() -> None:
    legs = {"vector_leg": [_row(i) for i in range(60)], "keyword_leg": []}
    retriever, _ = _retriever(legs)
    out = await retriever.retrieve(HybridQuery(text="x", top_k=999))
    assert len(out) == HybridRetriever.MAX_TOP_K  # top_k clamped to 50


async def test_per_leg_limit_passed_to_sql() -> None:
    captured: dict[str, Any] = {}

    class CapturingTx(FakeTx):
        async def fetch(self, name: str, params: dict[str, Any] | None = None) -> list[dict]:
            captured.setdefault(name, params)
            return await super().fetch(name, params)

    class CapturingClient(FakeClient):
        @asynccontextmanager
        async def read_tx(self) -> AsyncIterator[FakeTx]:
            self.tx_count += 1
            yield CapturingTx(self._legs, self.calls)

    client = CapturingClient({"vector_leg": [], "keyword_leg": []})
    retriever = HybridRetriever(client, DeterministicFakeProvider(dimensions=8))
    await retriever.retrieve(HybridQuery(text="q", top_k=5, per_leg_limit=25))
    assert captured["vector_leg"]["limit"] == 25
    assert captured["vector_leg"]["model"] == "fake/hashed-bow"


async def test_deterministic_across_runs() -> None:
    legs = {
        "vector_leg": [_row(1), _row(2), _row(3)],
        "keyword_leg": [_row(2), _row(3), _row(1)],
    }
    retriever, _ = _retriever(legs)
    first = [c.chunk_id for c in await retriever.retrieve(HybridQuery(text="q"))]
    for _ in range(10):
        again = [c.chunk_id for c in await retriever.retrieve(HybridQuery(text="q"))]
        assert again == first
