"""`mcp-ingest prune` (T-081): physically delete tombstones and apply retention.

Without it `kb` only grows: tombstoned documents are never removed and (together with HNSW) make
every query heavier (ADR-0011 A5 / ADR-0012 A5).

There is **no implicit retention**: `--older-than` is mandatory (the default retention is still an
open PO question, Open question 1/4/5; # THRESHOLD TBD). `--tombstoned` limits the operation to
tombstoned documents; without it, it applies to *live* documents that no crawl has seen for that
long (orphans), never to documents merely unchanged at the source. Dry-run is the default.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import psycopg

from mcp_ingest.reports import PruneReport, PruneSourceRow

__all__ = ["REINDEX_RECOMMENDED_CHUNKS", "prune"]

# A bulk delete leaves dead entries in the HNSW graph; recommend REINDEX CONCURRENTLY beyond this.
REINDEX_RECOMMENDED_CHUNKS = 1000  # THRESHOLD TBD (no measurement yet; R12)


def prune(
    conn: psycopg.Connection,
    *,
    older_than_days: int,
    tombstoned: bool,
    sources: list[str] | None = None,
    dry_run: bool = True,
    now: datetime | None = None,
) -> PruneReport:
    if older_than_days < 1:
        raise ValueError("--older-than must be at least 1 day")
    cutoff = (now or datetime.now().astimezone()) - timedelta(days=older_than_days)
    condition = (
        "d.deleted_at IS NOT NULL AND d.deleted_at < %(cutoff)s"
        if tombstoned
        else "d.deleted_at IS NULL AND coalesce(d.last_seen_at, d.ingested_at) < %(cutoff)s"
    )
    params: dict[str, object] = {"cutoff": cutoff}
    scope = ""
    if sources:
        scope = " AND d.source_type = ANY(%(sources)s)"
        params["sources"] = list(sources)
    rows = conn.execute(
        "SELECT d.source_type, count(DISTINCT d.id), count(c.id) FROM kb.documents d "
        f"LEFT JOIN kb.chunks c ON c.document_id = d.id WHERE {condition}{scope} "
        "GROUP BY d.source_type ORDER BY d.source_type",
        params,
    ).fetchall()
    per_source = [
        PruneSourceRow(source_type=r[0], documents_deleted=int(r[1]), chunks_deleted=int(r[2]))
        for r in rows
    ]
    if not dry_run and per_source:
        with conn.transaction():
            deleted = conn.execute(
                "DELETE FROM kb.documents d WHERE "
                f"{condition}{scope} RETURNING d.source_type, d.source_id",
                params,
            ).fetchall()  # chunks go with it: ON DELETE CASCADE
            for source_type, source_id in deleted:
                conn.execute(
                    "DELETE FROM kb.ingest_failures WHERE source_type = %s AND source_id = %s",
                    (source_type, source_id),
                )
    total_chunks = sum(r.chunks_deleted for r in per_source)
    return PruneReport(
        documents_deleted=sum(r.documents_deleted for r in per_source),
        chunks_deleted=total_chunks,
        dry_run=dry_run,
        reindex_recommended=total_chunks >= REINDEX_RECOMMENDED_CHUNKS,
        per_source=per_source,
    )
