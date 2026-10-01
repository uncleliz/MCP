"""Stage `persist` (T-075): idempotent writes into `kb`.

* `UNIQUE (source_type, source_id)` makes a re-ingest an UPDATE, never a duplicate (AC-003).
* Two skip keys: `content_hash` AND `chunk_config_hash`. When both match, chunk + embed are
  skipped but the **citation metadata is always UPDATEd** (title, source_uri, container, author,
  source_updated_at, visibility, last_seen_*): a renamed or moved page keeps its hash and would
  otherwise keep a stale citation (ADR-0012 A4, BR-005).
* A changed document is replaced in ONE transaction: upsert document, delete its chunks, insert the
  new ones.
* A document that the policy gate rejects but that is already in the corpus (label changed
  team -> restricted, path newly denied...) is purged: chunks deleted, document tombstoned and the
  `ingest_failures` row written in the same transaction (ADR-0016 A2, spike S5 section 3).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import psycopg

from mcp_ingest.connectors.base import SourceDocument
from mcp_ingest.pipeline.chunk import Chunk
from mcp_ingest.pipeline.embed import vector_literal
from mcp_ingest.pipeline.failures import upsert_failure

__all__ = ["Existing", "get_existing", "persist_changed", "purge_blocked", "touch_metadata"]


@dataclass(frozen=True)
class Existing:
    id: str
    content_hash: str
    chunk_config_hash: str | None
    deleted_at: datetime | None

    @property
    def live(self) -> bool:
        return self.deleted_at is None


def get_existing(conn: psycopg.Connection, source_type: str, source_id: str) -> Existing | None:
    row = conn.execute(
        "SELECT id::text, content_hash, chunk_config_hash, deleted_at FROM kb.documents "
        "WHERE source_type = %s AND source_id = %s",
        (source_type, source_id),
    ).fetchone()
    return Existing(*row) if row else None


def touch_metadata(
    conn: psycopg.Connection,
    existing_id: str,
    document: SourceDocument,
    title: str | None,
    run_id: str,
) -> None:
    """Hash-skip path: nothing is re-chunked, the citation metadata is refreshed."""
    conn.execute(
        "UPDATE kb.documents SET title = %s, source_uri = %s, container = %s, author = %s, "
        "source_updated_at = %s, visibility = %s, last_seen_run_id = %s, last_seen_at = now() "
        "WHERE id = %s",
        (
            title, document.source_uri, document.container, document.author,
            document.source_updated_at, document.visibility, run_id, existing_id,
        ),
    )  # fmt: skip


def persist_changed(
    conn: psycopg.Connection,
    *,
    document: SourceDocument,
    title: str | None,
    run_id: str,
    content_hash: str,
    chunk_config_hash: str,
    redactions: int,
    chunks: list[Chunk],
    vectors: list[list[float]],
    model_id: str,
) -> int:
    """Upsert the document and replace its chunks atomically. Returns the number of chunks."""
    metadata: dict[str, Any] = {**document.metadata, "redactions": redactions}
    with conn.transaction():
        row = conn.execute(
            "INSERT INTO kb.documents (source_type, source_id, source_uri, title, container, "
            "author, content_hash, source_updated_at, ingested_at, deleted_at, metadata, "
            "last_seen_run_id, last_seen_at, chunk_config_hash, visibility) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s, now(), NULL, %s::jsonb, %s, now(), %s, %s) "
            "ON CONFLICT (source_type, source_id) DO UPDATE SET "
            "source_uri = EXCLUDED.source_uri, title = EXCLUDED.title, "
            "container = EXCLUDED.container, author = EXCLUDED.author, "
            "content_hash = EXCLUDED.content_hash, "
            "source_updated_at = EXCLUDED.source_updated_at, ingested_at = now(), "
            "deleted_at = NULL, metadata = EXCLUDED.metadata, "
            "last_seen_run_id = EXCLUDED.last_seen_run_id, last_seen_at = now(), "
            "chunk_config_hash = EXCLUDED.chunk_config_hash, visibility = EXCLUDED.visibility "
            "RETURNING id",
            (
                document.source_type, document.source_id, document.source_uri, title,
                document.container, document.author, content_hash, document.source_updated_at,
                json.dumps(metadata), run_id, chunk_config_hash, document.visibility,
            ),
        ).fetchone()  # fmt: skip
        assert row is not None
        document_id = row[0]
        conn.execute("DELETE FROM kb.chunks WHERE document_id = %s", (document_id,))
        for chunk, vector in zip(chunks, vectors, strict=True):
            conn.execute(
                "INSERT INTO kb.chunks (document_id, chunk_index, content, token_count, "
                "heading_path, embedding, embedding_model) VALUES (%s,%s,%s,%s,%s,%s::vector,%s)",
                (
                    document_id, chunk.index, chunk.content, chunk.token_count,
                    chunk.heading_path, vector_literal(vector), model_id,
                ),
            )  # fmt: skip
    return len(chunks)


def purge_blocked(
    conn: psycopg.Connection,
    *,
    existing: Existing | None,
    document: SourceDocument,
    run_id: str | None,
    reason: str,
    code: str,
) -> bool:
    """Record a policy rejection; purge the document if it is already in the corpus.

    Returns True when a live document was purged (chunks deleted + tombstoned).
    """
    purged = False
    with conn.transaction():
        if existing is not None and existing.live:
            conn.execute("DELETE FROM kb.chunks WHERE document_id = %s", (existing.id,))
            conn.execute(
                "UPDATE kb.documents SET deleted_at = now(), visibility = %s, "
                "last_seen_run_id = %s, last_seen_at = now() WHERE id = %s",
                (document.visibility, run_id, existing.id),
            )
            purged = True
        upsert_failure(
            conn,
            source_type=document.source_type,
            source_id=document.source_id,
            stage="redact",
            code=code,
            error=reason,
            attempts=0,
        )
    return purged
