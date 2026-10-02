"""mcp-knowledge: read-only Company Knowledge MCP server (CHG-001 Option C).

E3 scope implemented here: Hybrid-RAG retrieval (:mod:`mcp_knowledge.retrieval`), the local
offline reranker with an RRF-only fallback (:mod:`mcp_knowledge.rerank`), and the
context-compression + context-pack assembler skeleton (:mod:`mcp_knowledge.pack`). The grounding
*verdict* (choke point #2) and the permission choke point (#1) are completed in later epics
(E6/E8); this package provides the clearly-marked seams for them.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
