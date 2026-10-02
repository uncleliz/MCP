"""The 8 read-only knowledge tools (FR-016/FR-018/FR-020, ADR-0020/0022).

Names, input schemas and outputs are dictated by ``api-contract.yaml``; ``tools.snapshot.json`` +
``tests/test_contract.py`` keep them in sync. Every tool is read-only (``x-readonly: true``,
``x-side-effects: none``). There is deliberately NO create/update/delete tool.
"""

from __future__ import annotations

from mcp_knowledge.tools.read_api import KnowledgeReadApi
from mcp_knowledge.tools.register import TOOL_DESCRIPTIONS, make_tools, register_tools

__all__ = ["KnowledgeReadApi", "TOOL_DESCRIPTIONS", "make_tools", "register_tools"]
