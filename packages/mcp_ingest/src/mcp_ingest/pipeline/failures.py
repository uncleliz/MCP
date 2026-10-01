"""`kb.ingest_failures` (T-077): documents that failed for good or were blocked by policy become
visible and retryable (`run --retry-failed`) instead of silently vanishing behind the checkpoint
(ADR-0012 A2/A5, ADR-0011 A4)."""

from __future__ import annotations

import psycopg

__all__ = ["clear_failure", "list_failure_ids", "upsert_failure"]


def upsert_failure(
    conn: psycopg.Connection,
    *,
    source_type: str,
    source_id: str,
    stage: str,
    code: str,
    error: str,
    attempts: int,
) -> None:
    """`attempts` is how many tries this call represents; policy blocks pass 0 (they are a decision,
    not a failed attempt, so repeated runs do not inflate the counter)."""
    conn.execute(
        "INSERT INTO kb.ingest_failures (source_type, source_id, attempts, stage, code, "
        "last_error) VALUES (%s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (source_type, source_id) DO UPDATE SET "
        "attempts = kb.ingest_failures.attempts + %s, last_attempt_at = now(), "
        "stage = EXCLUDED.stage, code = EXCLUDED.code, last_error = EXCLUDED.last_error",
        (source_type, source_id, max(attempts, 1) if attempts else 1, stage, code, error[:1000],
         attempts),
    )  # fmt: skip


def clear_failure(conn: psycopg.Connection, source_type: str, source_id: str) -> None:
    conn.execute(
        "DELETE FROM kb.ingest_failures WHERE source_type = %s AND source_id = %s",
        (source_type, source_id),
    )


def list_failure_ids(conn: psycopg.Connection, source_type: str) -> list[str]:
    rows = conn.execute(
        "SELECT source_id FROM kb.ingest_failures WHERE source_type = %s ORDER BY first_seen_at",
        (source_type,),
    ).fetchall()
    return [r[0] for r in rows]
