"""Context-compression + context-pack assembler (ADR-0020, ADR-0018).

The assembler is the single structural seam that becomes the grounding gate (choke point #2, E8)
and sits AFTER the permission choke point (#1, E6). At E3 it only gathers claims + provenance.
"""

from __future__ import annotations

from mcp_knowledge.pack.assembler import (
    ContextPack,
    ContextPackAssembler,
    RawClaim,
    identity_permission_filter,
)
from mcp_knowledge.pack.compress import (
    CompressedChunk,
    compress_candidates,
    estimate_tokens,
)

__all__ = [
    "CompressedChunk",
    "ContextPack",
    "ContextPackAssembler",
    "RawClaim",
    "compress_candidates",
    "estimate_tokens",
    "identity_permission_filter",
]
