"""Hybrid-RAG retrieval (ADR-0020): vector + keyword legs fused by deterministic RRF."""

from __future__ import annotations

from mcp_knowledge.retrieval.hybrid import Candidate, HybridQuery, HybridRetriever
from mcp_knowledge.retrieval.rrf import DEFAULT_RRF_K, RankedLeg, rrf_fuse

__all__ = [
    "Candidate",
    "DEFAULT_RRF_K",
    "HybridQuery",
    "HybridRetriever",
    "RankedLeg",
    "rrf_fuse",
]
