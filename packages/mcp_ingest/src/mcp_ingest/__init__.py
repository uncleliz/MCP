"""mcp-ingest: crawl -> redact -> chunk -> embed -> persist pipeline for the `kb` schema (FR-012).

This `__init__` must stay import-light: `mcp-pgvector` (a read-only server) imports
`mcp_ingest.ports` and `mcp_ingest.embedding`, and importing them must never pull in `psycopg`
or any write path (ADR-0010, R19; enforced by `tests/test_import_boundary.py`).
"""

__version__ = "0.1.0"
