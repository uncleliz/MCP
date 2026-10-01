"""Reconcile / tombstone with the safety valve (T-078, ADR-0012 A3, ADR-0011 A1/A2).

`mode=full` only; one statement per source, scoped by `last_seen_run_id`, and the chunks of the
tombstoned documents are physically deleted **in the same transaction** (a chunk of deleted
content would still occupy ANN candidate slots and bloat the HNSW index). Without the valve a crawl
that died at 30% would tombstone the other 70% of the corpus and `kb_semantic_search` would answer
"nothing indexed" for everything.
"""

from __future__ import annotations

from dataclasses import dataclass

import psycopg

__all__ = ["VALVE_RATIO", "ReconcileOutcome", "live_document_count", "reconcile"]

VALVE_RATIO = 0.8  # THRESHOLD TBD (ADR-0012 A3 value; PO has not confirmed it)


@dataclass(frozen=True)
class ReconcileOutcome:
    tombstoned: int
    blocked_reason: str | None = None


def live_document_count(conn: psycopg.Connection, source_type: str) -> int:
    row = conn.execute(
        "SELECT count(*) FROM kb.documents WHERE source_type = %s AND deleted_at IS NULL",
        (source_type,),
    ).fetchone()
    return int(row[0]) if row else 0


def reconcile(
    conn: psycopg.Connection,
    *,
    source_type: str,
    run_id: str,
    run_succeeded: bool,
    documents_seen: int,
    documents_before: int,
) -> ReconcileOutcome:
    if not run_succeeded:
        return ReconcileOutcome(0, "reconcile skipped: the crawl did not complete without errors")
    if documents_before and documents_seen < VALVE_RATIO * documents_before:
        return ReconcileOutcome(
            0,
            f"safety valve: crawl saw {documents_seen} documents but {documents_before} are "
            f"indexed (< {int(VALVE_RATIO * 100)}%); nothing was tombstoned",
        )
    with conn.transaction():
        rows = conn.execute(
            "UPDATE kb.documents SET deleted_at = now() WHERE source_type = %s "
            "AND last_seen_run_id IS DISTINCT FROM %s AND deleted_at IS NULL RETURNING id",
            (source_type, run_id),
        ).fetchall()
        ids = [r[0] for r in rows]
        if ids:
            conn.execute("DELETE FROM kb.chunks WHERE document_id = ANY(%s)", (ids,))
    return ReconcileOutcome(len(ids))
