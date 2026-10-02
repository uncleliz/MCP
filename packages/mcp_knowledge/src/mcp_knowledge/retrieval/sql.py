"""The closed, named set of read-only SQL for Hybrid-RAG retrieval (ADR-0020, ADR-0011).

Mirrors the discipline of :mod:`mcp_pgvector.sql`: no statement takes SQL text; the client looks a
statement up **by name**; every statement is a parameterised ``SELECT``. Read-only-ness is enforced
three ways (ADR-0003): named statements only, ``BEGIN READ ONLY`` on every transaction, and the
``mcp_query_ro`` role at the database.

Two retrieval legs over the SAME populated store the 9-source platform went live on:

* **vector** — pgvector HNSW cosine over ``kb.chunks.embedding`` (reuses the E1 index);
* **keyword** — the ``kb.chunks.content_tsv`` tsvector + GIN index added in migration 0007/0007b,
  matched with ``websearch_to_tsquery('simple', ...)`` so exact identifiers / error codes match.

Both legs apply the mandatory ``deleted_at IS NULL`` + ``embedding_model = configured`` filters and
the same optional metadata filters, so a tombstone or an interrupted re-embed can never reach a
result and the two legs rank the *same* candidate universe before RRF fuses them.
"""

from __future__ import annotations

__all__ = ["ALLOWED_GUCS", "STATEMENTS"]

# Session settings the query path may change (transaction-local only, via `set_config(.., true)`).
ALLOWED_GUCS = frozenset({"hnsw.ef_search", "hnsw.iterative_scan"})

_SELECT_COLUMNS = """
       c.id AS chunk_id, c.document_id, c.chunk_index, c.content, c.heading_path,
       c.embedding_model, d.source_type, d.source_id, d.source_uri, d.title, d.container,
       d.author, d.source_updated_at, d.ingested_at
"""

# Shared filter clause: mandatory invariants + NULL-coalesced optional metadata filters, so the SQL
# text is constant (no dynamic SQL, no injection surface).
_FILTERS = """
      WHERE d.deleted_at IS NULL
        AND c.embedding_model = %(model)s
        AND (cardinality(%(source_types)s::text[]) = 0
             OR d.source_type = ANY(%(source_types)s::text[]))
        AND (%(container)s::text IS NULL OR d.container = %(container)s::text)
        AND (%(updated_after)s::timestamptz IS NULL
             OR d.source_updated_at >= %(updated_after)s::timestamptz)
"""

STATEMENTS: dict[str, str] = {
    # -- session tuning inside the read-only transaction (ADR-0011 A3) --------------------------
    "set_config": "SELECT set_config(%(name)s, %(value)s, true) AS value",
    # -- capability / consistency probes ---------------------------------------------------------
    "extension_version": "SELECT extversion FROM pg_extension WHERE extname = 'vector'",
    # -- vector leg: pgvector HNSW cosine, best (smallest distance) first ------------------------
    "vector_leg": f"""
        SELECT {_SELECT_COLUMNS},
               1 - (c.embedding <=> %(query)s::vector) AS similarity
        FROM kb.chunks c
        JOIN kb.documents d ON d.id = c.document_id
        {_FILTERS}
        ORDER BY c.embedding <=> %(query)s::vector
        LIMIT %(limit)s
    """,
    # -- keyword leg: tsvector + GIN, websearch_to_tsquery('simple', ...) ------------------------
    # `simple` config (no stemming) so exact identifiers / error codes match (ADR-0020). Rows that
    # do not match the tsquery at all are excluded (the @@ in the WHERE clause). ts_rank orders the
    # leg; its magnitude is NOT comparable with cosine — only the RANK is fed to RRF.
    "keyword_leg": f"""
        SELECT {_SELECT_COLUMNS},
               ts_rank(c.content_tsv, websearch_to_tsquery('simple', %(text)s)) AS ts_rank
        FROM kb.chunks c
        JOIN kb.documents d ON d.id = c.document_id
        {_FILTERS}
          AND c.content_tsv @@ websearch_to_tsquery('simple', %(text)s)
        ORDER BY ts_rank(c.content_tsv, websearch_to_tsquery('simple', %(text)s)) DESC, c.id
        LIMIT %(limit)s
    """,
    # -- permission choke point #1 (E6/T-104, ADR-0016/0021): grants for a candidate doc set ------
    # Returns one row per (document_id, principal) grant that the caller may hold, for the candidate
    # documents only. Default-deny is enforced in Python (`enforce_permission`): a document with NO
    # returned grant is dropped. The visibility column is read so a `restricted` document is only
    # ever reachable through an explicit non-team grant (the pseudo-principal '*team*' is a grant
    # for team-visible content; a team member holds it, so `team` docs resolve, `restricted` do not
    # unless an explicit principal grant exists). ``deleted_at IS NULL`` so a tombstoned document
    # can never be granted. ``principals`` is the caller's principal set (always includes '*team*'
    # for a team member in the TEAM-ONLY corpus, ADR-0016 A1).
    "document_grants": """
        SELECT dp.document_id::text AS document_id, dp.principal, d.visibility
        FROM kb.document_permissions dp
        JOIN kb.documents d ON d.id = dp.document_id
        WHERE dp.document_id = ANY(%(document_ids)s::uuid[])
          AND d.deleted_at IS NULL
          AND dp.grant_type = 'read'
          AND dp.principal = ANY(%(principals)s::text[])
    """,
}
