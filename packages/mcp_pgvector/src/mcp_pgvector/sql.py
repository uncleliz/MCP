"""The complete, closed set of SQL that mcp-pgvector can run (ADR-0008, ADR-0011).

There is deliberately no tool (and no client method) that accepts SQL text: the client looks a
statement up **by name** in :data:`STATEMENTS`, and every statement is a parameterised `SELECT`
(`SHOW`/`set_config` for session tuning). `tests/test_sql_readonly.py` asserts that no statement
contains a write/DDL keyword. Read-only-ness is additionally enforced by the database role
(`mcp_query_ro`, SELECT only) and by `BEGIN READ ONLY` on every transaction.
"""

from __future__ import annotations

__all__ = ["ALLOWED_GUCS", "STATEMENTS"]

# Session settings the query path may change (transaction-local, via `set_config(.., true)`).
ALLOWED_GUCS = frozenset({"hnsw.ef_search", "hnsw.iterative_scan"})

_SEARCH_COLUMNS = """
       c.id AS chunk_id, c.document_id, c.chunk_index, c.content, c.heading_path,
       c.embedding_model, d.source_type, d.source_id, d.source_uri, d.title, d.container,
       d.source_updated_at, d.ingested_at,
       1 - (c.embedding <=> %(query)s::vector) AS similarity
"""

STATEMENTS: dict[str, str] = {
    # -- startup credential/consistency check (ADR-0003 A1, ADR-0008 A3, ADR-0010) --------------
    "show_read_only": "SHOW transaction_read_only",
    "schema_info": (
        "SELECT to_regclass('kb.chunks') IS NOT NULL AS has_chunks, "
        "to_regclass('kb.documents') IS NOT NULL AS has_documents"
    ),
    "role_info": """
        SELECT current_user AS role,
               coalesce((SELECT rolsuper FROM pg_roles WHERE rolname = current_user), false)
                   AS is_superuser,
               has_table_privilege('kb.chunks', 'INSERT') AS chunks_insert,
               has_table_privilege('kb.chunks', 'UPDATE') AS chunks_update,
               has_table_privilege('kb.chunks', 'DELETE') AS chunks_delete,
               has_table_privilege('kb.chunks', 'TRUNCATE') AS chunks_truncate,
               has_table_privilege('kb.documents', 'INSERT') AS documents_insert,
               has_table_privilege('kb.documents', 'UPDATE') AS documents_update,
               has_table_privilege('kb.documents', 'DELETE') AS documents_delete,
               has_table_privilege('kb.documents', 'TRUNCATE') AS documents_truncate
    """,
    "extension_version": "SELECT extversion FROM pg_extension WHERE extname = 'vector'",
    "stored_models": "SELECT DISTINCT embedding_model FROM kb.chunks",
    "embedding_dimensions": (
        "SELECT atttypmod AS dimensions FROM pg_attribute "
        "WHERE attrelid = 'kb.chunks'::regclass AND attname = 'embedding'"
    ),
    # -- session tuning inside the read-only transaction (ADR-0011 A3) --------------------------
    "set_config": "SELECT set_config(%(name)s, %(value)s, true) AS value",
    # -- kb_semantic_search ----------------------------------------------------------------------
    # `deleted_at IS NULL` and `embedding_model = configured` are mandatory (tombstones and an
    # interrupted re-embed must never reach a result). Optional filters are NULL-coalesced
    # parameters, so the text is constant (no dynamic SQL).
    "search": f"""
        SELECT {_SEARCH_COLUMNS}
        FROM kb.chunks c
        JOIN kb.documents d ON d.id = c.document_id
        WHERE d.deleted_at IS NULL
          AND c.embedding_model = %(model)s
          AND (cardinality(%(source_types)s::text[]) = 0
               OR d.source_type = ANY(%(source_types)s::text[]))
          AND (%(container)s::text IS NULL OR d.container = %(container)s::text)
          AND (%(updated_after)s::timestamptz IS NULL
               OR d.source_updated_at >= %(updated_after)s::timestamptz)
        ORDER BY c.embedding <=> %(query)s::vector
        LIMIT %(limit)s
    """,
    # The same ranking with NO user filters: tells "nothing is similar" apart from "the filters
    # excluded it" (ADR-0011 A3).
    "search_unfiltered": f"""
        SELECT {_SEARCH_COLUMNS}
        FROM kb.chunks c
        JOIN kb.documents d ON d.id = c.document_id
        WHERE d.deleted_at IS NULL
          AND c.embedding_model = %(model)s
        ORDER BY c.embedding <=> %(query)s::vector
        LIMIT %(limit)s
    """,
    # -- freshness (NFR-004) ---------------------------------------------------------------------
    "freshness": """
        SELECT src.source_type,
               coalesce(st.last_success_at, src.last_doc_ingested_at) AS last_ingested_at
        FROM (SELECT source_type, max(ingested_at) AS last_doc_ingested_at
                FROM kb.documents WHERE deleted_at IS NULL GROUP BY source_type) src
        LEFT JOIN kb.ingest_source_state st ON st.source_type = src.source_type
        WHERE cardinality(%(source_types)s::text[]) = 0
           OR src.source_type = ANY(%(source_types)s::text[])
    """,
    # -- kb_get_document -------------------------------------------------------------------------
    "document_by_key": """
        SELECT d.id AS document_id, d.source_type, d.source_id, d.source_uri, d.title,
               d.container, d.author, d.source_updated_at, d.ingested_at
        FROM kb.documents d
        WHERE d.deleted_at IS NULL
          AND ((%(document_id)s::uuid IS NOT NULL AND d.id = %(document_id)s::uuid)
               OR (%(document_id)s::uuid IS NULL AND d.source_uri = %(source_uri)s::text))
        ORDER BY d.ingested_at DESC
        LIMIT 1
    """,
    "document_chunks": """
        SELECT c.chunk_index, c.content, c.heading_path, count(*) OVER () AS chunk_count
        FROM kb.chunks c
        WHERE c.document_id = %(document_id)s::uuid
        ORDER BY c.chunk_index
    """,
    # -- kb_list_sources -------------------------------------------------------------------------
    "list_sources": """
        WITH wanted AS (
            SELECT source_type FROM kb.documents WHERE deleted_at IS NULL
            UNION SELECT source_type FROM kb.ingest_source_state
            UNION SELECT source_type FROM kb.ingest_runs
        ), docs AS (
            SELECT source_type, count(*) AS document_count, max(ingested_at) AS last_ingested_at
            FROM kb.documents WHERE deleted_at IS NULL GROUP BY source_type
        ), chunk_stats AS (
            SELECT d.source_type, count(*) AS chunk_count,
                   string_agg(DISTINCT c.embedding_model, ',' ORDER BY c.embedding_model)
                       AS embedding_models
            FROM kb.chunks c JOIN kb.documents d ON d.id = c.document_id
            WHERE d.deleted_at IS NULL GROUP BY d.source_type
        )
        SELECT w.source_type,
               coalesce(docs.document_count, 0) AS document_count,
               coalesce(chunk_stats.chunk_count, 0) AS chunk_count,
               docs.last_ingested_at,
               st.last_success_at,
               run.status AS last_run_status,
               chunk_stats.embedding_models,
               -- Any live document's URL: its origin is the citable address of the source system.
               (SELECT d2.source_uri FROM kb.documents d2
                 WHERE d2.source_type = w.source_type AND d2.deleted_at IS NULL
                 ORDER BY d2.ingested_at DESC LIMIT 1) AS sample_uri
        FROM wanted w
        LEFT JOIN docs ON docs.source_type = w.source_type
        LEFT JOIN chunk_stats ON chunk_stats.source_type = w.source_type
        LEFT JOIN kb.ingest_source_state st ON st.source_type = w.source_type
        LEFT JOIN LATERAL (
            SELECT r.status FROM kb.ingest_runs r
            WHERE r.source_type = w.source_type ORDER BY r.started_at DESC LIMIT 1
        ) run ON true
        WHERE cardinality(%(source_types)s::text[]) = 0
           OR w.source_type = ANY(%(source_types)s::text[])
        ORDER BY w.source_type
    """,
}
