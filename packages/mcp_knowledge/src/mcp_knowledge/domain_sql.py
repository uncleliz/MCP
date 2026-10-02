"""Closed, named read-only SQL for the knowledge domain tools (T-099..T-101, ADR-0022).

Discipline identical to :mod:`mcp_knowledge.retrieval.sql` and :mod:`mcp_pgvector.sql`: no statement
takes SQL text, the client looks a statement up **by name**, every statement is a parameterised
``SELECT`` (plus the startup ``SHOW``/privilege probes). Read-only-ness is enforced three ways
(ADR-0003): named statements only, ``BEGIN READ ONLY`` on every transaction, and the
``mcp_query_ro``
role at the database.

Covers the four knowledge domains added in migration 0007 (``kb.entities``, ``kb.relationships``,
``kb.knowledge_summaries``, ``kb.document_versions``) and the startup credential/consistency probes
that mirror :mod:`mcp_pgvector.sql` so pasting the ``mcp_ingest_rw`` DSN is refused (ADR-0003 A1).

The graph traversal (:data:`DOMAIN_STATEMENTS` ``related_knowledge``) is a bounded
``WITH RECURSIVE``:
``depth <= %(max_depth)s`` with ``max_depth`` clamped to 3 by the caller, cycle-detection via a
visited-path array, and a per-hop fan-out ``LIMIT`` so a high fan-out node cannot blow up the result
(FR-020/AC-003, ADR-0022 DP4).
"""

from __future__ import annotations

__all__ = ["DOMAIN_STATEMENTS", "FANOUT_LIMIT", "MAX_HOPS"]

#: Hard ceiling on recursion depth (ADR-0022 DP4). The tool clamps ``max_depth`` to this.
MAX_HOPS = 3
#: Per-hop fan-out cap so one high-degree node cannot produce an unbounded edge set.
FANOUT_LIMIT = 200

DOMAIN_STATEMENTS: dict[str, str] = {
    # -- startup credential + consistency probes (mirror mcp_pgvector.sql) -----------------------
    "show_read_only": "SHOW transaction_read_only",
    "schema_info": (
        "SELECT to_regclass('kb.chunks') IS NOT NULL AS has_chunks, "
        "to_regclass('kb.documents') IS NOT NULL AS has_documents, "
        "to_regclass('kb.entities') IS NOT NULL AS has_entities, "
        "to_regclass('kb.relationships') IS NOT NULL AS has_relationships"
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
               has_table_privilege('kb.documents', 'TRUNCATE') AS documents_truncate,
               has_table_privilege('kb.entities', 'INSERT') AS entities_insert
    """,
    "extension_version": "SELECT extversion FROM pg_extension WHERE extname = 'vector'",
    "stored_models": "SELECT DISTINCT embedding_model FROM kb.chunks",
    "embedding_dimensions": (
        "SELECT atttypmod AS dimensions FROM pg_attribute "
        "WHERE attrelid = 'kb.chunks'::regclass AND attname = 'embedding'"
    ),
    "set_config": "SELECT set_config(%(name)s, %(value)s, true) AS value",
    # -- get_service / get_repository: one entity by (type, name) + 1-hop related ----------------
    "entity_by_type_name": """
        SELECT e.id, e.entity_type, e.name, e.display_name, e.document_id, e.metadata,
               d.source_type, d.source_uri, d.title, d.container, d.deleted_at
        FROM kb.entities e
        LEFT JOIN kb.documents d ON d.id = e.document_id
        WHERE e.entity_type = %(entity_type)s AND e.name = %(name)s
        LIMIT 1
    """,
    # Direct (depth 1) neighbours of an entity; used to fill KnowledgeEntity.related.
    "entity_neighbours": """
        SELECT r.rel_type, n.entity_type, n.name
        FROM kb.relationships r
        JOIN kb.entities n ON n.id = r.dst_entity_id
        WHERE r.src_entity_id = %(entity_id)s::uuid
          AND (cardinality(%(rel_types)s::text[]) = 0
               OR r.rel_type = ANY(%(rel_types)s::text[]))
        ORDER BY r.rel_type, n.name
        LIMIT %(limit)s
    """,
    # -- find_related_knowledge: bounded recursive CTE (depth<=3, cycle-detect, fan-out LIMIT) ---
    "entity_by_name_any_type": """
        SELECT e.id, e.entity_type, e.name, e.document_id,
               d.source_type, d.source_uri, d.deleted_at
        FROM kb.entities e
        LEFT JOIN kb.documents d ON d.id = e.document_id
        WHERE e.name = %(name)s
        ORDER BY e.entity_type
        LIMIT 1
    """,
    "related_knowledge": """
        WITH RECURSIVE walk AS (
            -- seed: the root entity at depth 0, visited-path = [root]
            SELECT e.id AS node_id, e.name AS node_name, e.entity_type,
                   NULL::text  AS src_name,
                   NULL::text  AS rel_type,
                   0           AS depth,
                   ARRAY[e.id] AS path,
                   NULL::uuid  AS doc_id
            FROM kb.entities e
            WHERE e.id = %(root_id)s::uuid

            UNION ALL

            -- one more hop, bounded: depth < max_depth, no cycle (dst not already on the path),
            -- fan-out capped per expansion via the LATERAL LIMIT.
            SELECT nbr.dst_id, nbr.dst_name, nbr.dst_type,
                   w.node_name, nbr.rel_type,
                   w.depth + 1,
                   w.path || nbr.dst_id,
                   nbr.doc_id
            FROM walk w
            CROSS JOIN LATERAL (
                SELECT r.dst_entity_id AS dst_id, n.name AS dst_name, n.entity_type AS dst_type,
                       r.rel_type AS rel_type, n.document_id AS doc_id
                FROM kb.relationships r
                JOIN kb.entities n ON n.id = r.dst_entity_id
                WHERE r.src_entity_id = w.node_id
                  AND NOT (r.dst_entity_id = ANY(w.path))
                  AND (cardinality(%(rel_types)s::text[]) = 0
                       OR r.rel_type = ANY(%(rel_types)s::text[]))
                ORDER BY r.rel_type, n.name
                LIMIT %(fanout)s
            ) nbr
            WHERE w.depth < %(max_depth)s
        )
        SELECT w.src_name, w.node_name AS dst_name, w.rel_type, w.depth,
               w.doc_id, d.source_type, d.source_uri
        FROM walk w
        LEFT JOIN kb.documents d ON d.id = w.doc_id
        WHERE w.depth >= 1
        ORDER BY w.depth, w.src_name, w.rel_type, w.node_name
        LIMIT %(total_limit)s
    """,
    # -- get_knowledge_summary -------------------------------------------------------------------
    "knowledge_summary": """
        SELECT ks.subject_type, ks.subject_id, ks.summary, ks.provenance, ks.generated_at
        FROM kb.knowledge_summaries ks
        WHERE ks.subject_type = %(subject_type)s AND ks.subject_id = %(subject_id)s
        LIMIT 1
    """,
    # Resolve an entity name -> its id (as text), so a summary stored by entity id can be found by
    # a human-friendly name as well.
    "entity_id_by_name": """
        SELECT e.id::text AS subject_id
        FROM kb.entities e
        WHERE e.name = %(name)s
        ORDER BY e.entity_type
        LIMIT 1
    """,
    # A document whose origin URL / source_type is the citable address for a summary/entity.
    "document_citation": """
        SELECT d.source_type, d.source_uri, d.title
        FROM kb.documents d
        WHERE d.id = %(document_id)s::uuid AND d.deleted_at IS NULL
        LIMIT 1
    """,
    # -- Live-vs-Knowledge config (E5, T-105): source authority + freshness horizon --------------
    # Read-only SELECTs over the config tables seeded by migration 0008 (ADR-0018 §4, spec §42).
    # The reconciler reads these to build the CONFLICT authority_note and pick a fact type's
    # freshness horizon — the authority is configured in the DB, never hardcoded in a prompt.
    "source_authority_all": """
        SELECT fact_type, authoritative_source, rationale
        FROM kb.source_authority
        ORDER BY fact_type
    """,
    "freshness_horizon_all": """
        SELECT fact_type, horizon_hours
        FROM kb.freshness_horizon
        ORDER BY fact_type
    """,
    # -- get_document_version --------------------------------------------------------------------
    # The document must exist and not be tombstoned (tombstone => not_found).
    "live_document": """
        SELECT d.id AS document_id, d.source_type, d.source_uri, d.title
        FROM kb.documents d
        WHERE d.id = %(document_id)s::uuid AND d.deleted_at IS NULL
        LIMIT 1
    """,
    "document_versions": """
        SELECT dv.document_id, dv.version, dv.content_hash, dv.source_version, dv.author,
               dv.source_updated_at, dv.created_at, dv.status
        FROM kb.document_versions dv
        WHERE dv.document_id = %(document_id)s::uuid
          AND (%(version)s::integer IS NULL OR dv.version = %(version)s::integer)
        ORDER BY dv.version DESC
        LIMIT %(limit)s
    """,
    # -- permission choke point #1 (E6/T-104, ADR-0016/0021): grants for a candidate doc set ------
    # Returns one row per (document_id, principal) grant the caller may hold, for the candidate
    # documents only. Default-deny is decided in Python (`enforce_permission`): a document with NO
    # returned grant row is dropped. ``visibility`` is surfaced so a `restricted` document is only
    # reachable through an explicit non-team grant (the pseudo-principal '*team*' grants team-
    # visible content; a team member holds it). ``deleted_at IS NULL`` so a tombstoned document can
    # never be granted. ``principals`` is the caller's effective principal set (ADR-0016 A1).
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
