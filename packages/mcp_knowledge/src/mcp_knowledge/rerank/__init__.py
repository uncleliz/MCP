"""Local offline reranker (ADR-0020) with a transparent RRF-only fallback (L-002)."""

from __future__ import annotations

from mcp_knowledge.rerank.local import (
    DEFAULT_RERANKER_MODEL,
    Reranker,
    RerankOutcome,
)

__all__ = ["DEFAULT_RERANKER_MODEL", "Reranker", "RerankOutcome"]
