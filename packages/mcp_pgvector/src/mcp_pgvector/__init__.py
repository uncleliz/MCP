"""mcp-pgvector: read-only MCP server for semantic search over the kb embedding store (FR-011).

Connects as the read-only role `mcp_query_ro`, runs every query in `BEGIN READ ONLY` and exposes
no tool that accepts SQL. Refuses to start with a write-capable DSN or an embedding model that
differs from the stored data.
"""

__version__ = "0.1.0"
