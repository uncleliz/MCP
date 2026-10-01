"""`mcp-ingest status` (T-079): per-source freshness for NFR-004.

Reports numbers, passes no judgement: the staleness bound is still an open PO question
(Open question 5), so nothing here compares `staleness_hours` with a limit.
# THRESHOLD TBD (Open question 5): freshness bound for `staleness_hours`.
"""

from __future__ import annotations

from datetime import UTC, datetime

import psycopg

from mcp_ingest.reports import IngestStatusReport, IngestStatusRow

__all__ = ["failure_counts", "gather_status"]


def failure_counts(conn: psycopg.Connection) -> dict[str, int]:
    rows = conn.execute(
        "SELECT source_type, count(*) FROM kb.ingest_failures GROUP BY source_type"
    ).fetchall()
    return {r[0]: int(r[1]) for r in rows}


def gather_status(
    conn: psycopg.Connection,
    *,
    source: str | None = None,
    known: list[str] | None = None,
    now: datetime | None = None,
) -> IngestStatusReport:
    as_of = now or datetime.now(UTC)
    names: set[str] = set(known or [])
    for table in ("kb.documents", "kb.ingest_source_state", "kb.ingest_runs"):
        names.update(r[0] for r in conn.execute(f"SELECT DISTINCT source_type FROM {table}"))
    if source and source != "all":
        names = {source}
    rows: list[IngestStatusRow] = []
    for name in sorted(names):
        state = conn.execute(
            "SELECT last_success_at FROM kb.ingest_source_state WHERE source_type = %s", (name,)
        ).fetchone()
        docs = conn.execute(
            "SELECT count(*) FROM kb.documents WHERE source_type = %s AND deleted_at IS NULL",
            (name,),
        ).fetchone()
        chunks = conn.execute(
            "SELECT count(*) FROM kb.chunks c JOIN kb.documents d ON d.id = c.document_id "
            "WHERE d.source_type = %s AND d.deleted_at IS NULL",
            (name,),
        ).fetchone()
        run = conn.execute(
            "SELECT status, coalesce(finished_at, started_at) FROM kb.ingest_runs "
            "WHERE source_type = %s ORDER BY started_at DESC LIMIT 1",
            (name,),
        ).fetchone()
        last_success = state[0] if state else None
        staleness = (
            round(max((as_of - last_success).total_seconds(), 0.0) / 3600, 2)
            if last_success
            else None
        )
        rows.append(
            IngestStatusRow(
                source_type=name,
                last_success_at=last_success,
                staleness_hours=staleness,
                document_count=int(docs[0]) if docs else 0,
                chunk_count=int(chunks[0]) if chunks else 0,
                last_run_status=run[0] if run else None,
                last_run_at=run[1] if run else None,
            )
        )
    return IngestStatusReport(as_of=as_of, sources=rows)
