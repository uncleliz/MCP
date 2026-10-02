"""Permission choke point #1 (E6/T-104, ADR-0016/0021).

``enforce_permission`` is the single, server-side, default-deny filter that runs **before** context
assembly (choke point #2, the grounding gate, E8). A caller without a grant on a document never
sees that document as a candidate, in the context-pack, or as a citation — the restricted content
is dropped before the pipeline ever assembles it.
"""

from __future__ import annotations

from mcp_knowledge.permission.enforce import (
    TEAM_PRINCIPAL,
    CallerContext,
    PermissionGrants,
    build_permission_filter,
    enforce_permission,
    load_grants,
    load_grants_for_document_ids,
)

__all__ = [
    "TEAM_PRINCIPAL",
    "CallerContext",
    "PermissionGrants",
    "build_permission_filter",
    "enforce_permission",
    "load_grants",
    "load_grants_for_document_ids",
]
