"""Hybrid-RAG retrieval engine (T-095, ADR-0020).

Combines three signals over the single Postgres store:

1. **vector** — pgvector HNSW cosine (reuses the E1 HNSW index);
2. **keyword** — ``kb.chunks.content_tsv`` + GIN, ``websearch_to_tsquery('simple', ...)`` so exact
   identifiers / error codes match;
3. **metadata filter** — ``source_types`` / ``container`` / ``updated_after`` applied *inside* both
   legs' SQL so the two legs rank the same candidate universe.

The two ranked legs are fused by **deterministic RRF (k=60)** (:mod:`mcp_knowledge.retrieval.rrf`)
— no cross-space score comparison. The mandatory ``deleted_at IS NULL`` and
``embedding_model = configured`` filters live in the SQL (:mod:`mcp_knowledge.retrieval.sql`), so a
tombstone or an interrupted re-embed can never surface.

This module is read-only: it only runs the named statements through a ``read_tx`` whose transaction
is ``BEGIN READ ONLY`` and whose role is ``mcp_query_ro``. It never embeds a write path.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from mcp_ingest.ports import EmbeddingProvider

from mcp_knowledge.retrieval.rrf import DEFAULT_RRF_K, RankedLeg, rrf_fuse

__all__ = [
    "Candidate",
    "HybridRetriever",
    "HybridQuery",
    "ReadTxProtocol",
    "RetrievalClient",
]


class ReadTxProtocol(Protocol):
    """The minimal read surface the engine needs — a named, parameterised statement runner."""

    async def fetch(
        self, name: str, params: Mapping[str, Any] | None = None
    ) -> list[dict[str, Any]]: ...

    async def set_local(self, name: str, value: str) -> None: ...


class RetrievalClient(Protocol):
    """A client that yields a read-only transaction (the pgvector client satisfies this)."""

    def read_tx(self) -> Any:  # async context manager yielding ReadTxProtocol
        ...


@dataclass(frozen=True)
class HybridQuery:
    """A hybrid retrieval request. ``text`` drives both the embedding and the keyword tsquery."""

    text: str
    top_k: int = 10
    source_types: Sequence[str] = ()
    container: str | None = None
    updated_after: datetime | None = None
    # How many rows each leg fetches before fusion. Over-fetching gives RRF more to work with; the
    # fused result is cut to top_k. Clamped to a sane bound.
    per_leg_limit: int = 50


@dataclass(frozen=True)
class Candidate:
    """One fused retrieval candidate carrying everything the pack/grounding stages need.

    ``legs`` records which legs surfaced this candidate and at what 1-based rank — provenance for
    telemetry and for the grounding gate. ``rrf_score`` is the fused score (not a probability).
    """

    chunk_id: int
    document_id: str
    chunk_index: int
    content: str
    heading_path: str | None
    source_type: str
    source_id: str
    source_uri: str
    title: str | None
    container: str | None
    author: str | None
    source_updated_at: datetime | None
    ingested_at: datetime | None
    rrf_score: float
    legs: dict[str, int] = field(default_factory=dict)


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(value, high))


class HybridRetriever:
    """Runs the vector + keyword legs and fuses them with RRF (k=60)."""

    MAX_TOP_K = 50
    MAX_PER_LEG = 200

    def __init__(
        self,
        client: RetrievalClient,
        provider: EmbeddingProvider,
        *,
        rrf_k: int = DEFAULT_RRF_K,
    ) -> None:
        self._client = client
        self._provider = provider
        self._rrf_k = rrf_k

    async def retrieve(self, query: HybridQuery) -> list[Candidate]:
        """Return fused candidates, best-first, cut to ``top_k`` (deterministic order)."""
        top_k = _clamp(query.top_k, 1, self.MAX_TOP_K)
        per_leg = _clamp(query.per_leg_limit, top_k, self.MAX_PER_LEG)
        query_vector = self._provider.embed_query(query.text)
        params = {
            "query": query_vector,
            "text": query.text,
            "model": self._provider.model_id,
            "source_types": list(query.source_types),
            "container": query.container,
            "updated_after": query.updated_after,
            "limit": per_leg,
        }

        async with self._client.read_tx() as tx:
            vector_rows = await tx.fetch("vector_leg", params)
            keyword_rows = await tx.fetch("keyword_leg", params)

        return self._fuse(vector_rows, keyword_rows, top_k=top_k)

    def _fuse(
        self,
        vector_rows: list[dict[str, Any]],
        keyword_rows: list[dict[str, Any]],
        *,
        top_k: int,
    ) -> list[Candidate]:
        by_id: dict[int, dict[str, Any]] = {}
        vector_leg: list[int] = []
        keyword_leg: list[int] = []
        for row in vector_rows:
            cid = int(row["chunk_id"])
            by_id.setdefault(cid, row)
            vector_leg.append(cid)
        for row in keyword_rows:
            cid = int(row["chunk_id"])
            by_id.setdefault(cid, row)
            keyword_leg.append(cid)

        fused = rrf_fuse(
            [RankedLeg("vector", vector_leg), RankedLeg("keyword", keyword_leg)],
            k=self._rrf_k,
            limit=top_k,
        )
        vector_rank = {cid: i + 1 for i, cid in enumerate(vector_leg)}
        keyword_rank = {cid: i + 1 for i, cid in enumerate(keyword_leg)}

        candidates: list[Candidate] = []
        for identity, score in fused:
            cid = int(identity)  # type: ignore[call-overload]  # ids originate as int chunk_ids
            row = by_id[cid]
            legs: dict[str, int] = {}
            if cid in vector_rank:
                legs["vector"] = vector_rank[cid]
            if cid in keyword_rank:
                legs["keyword"] = keyword_rank[cid]
            candidates.append(_candidate_from_row(row, rrf_score=score, legs=legs))
        return candidates


def _candidate_from_row(
    row: Mapping[str, Any], *, rrf_score: float, legs: dict[str, int]
) -> Candidate:
    return Candidate(
        chunk_id=int(row["chunk_id"]),
        document_id=str(row["document_id"]),
        chunk_index=int(row["chunk_index"]),
        content=row["content"],
        heading_path=row.get("heading_path"),
        source_type=row["source_type"],
        source_id=row["source_id"],
        source_uri=row["source_uri"],
        title=row.get("title"),
        container=row.get("container"),
        author=row.get("author"),
        source_updated_at=row.get("source_updated_at"),
        ingested_at=row.get("ingested_at"),
        rrf_score=rrf_score,
        legs=legs,
    )
