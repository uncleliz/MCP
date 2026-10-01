"""Checkpoint, per-source locking and `ingest_runs` bookkeeping (T-076, ADR-0012 A2/A3).

* One advisory lock per source, taken on a **dedicated, non-pooled** session: a session-level
  lock on a pooled connection could be handed to another borrower and released early.
* One `kb.ingest_runs` row per source per run.
* `new_cursor = min(watermark of failed docs) - EPSILON`; with no failed doc it is the max watermark
  of the committed ones; the boundary is inclusive (`>=`) on the next crawl. A source that errors
  keeps its old cursor, so the next run retries exactly the same range.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Any

import psycopg

from mcp_ingest.connectors.base import Cursor

__all__ = [
    "EPSILON",
    "finish_run",
    "load_cursor",
    "next_cursor",
    "open_lock_session",
    "save_state",
    "start_run",
    "try_lock",
]

EPSILON = timedelta(seconds=1)


def open_lock_session(dsn: str) -> psycopg.Connection:
    return psycopg.connect(dsn, autocommit=True, connect_timeout=10)


def try_lock(conn: psycopg.Connection, source_type: str) -> bool:
    row = conn.execute(
        "SELECT pg_try_advisory_lock(hashtext(%s))", (f"mcp-ingest:{source_type}",)
    ).fetchone()
    return bool(row and row[0])


def load_cursor(conn: psycopg.Connection, source_type: str) -> Cursor | None:
    row = conn.execute(
        "SELECT cursor FROM kb.ingest_source_state WHERE source_type = %s", (source_type,)
    ).fetchone()
    return Cursor.from_json(row[0]) if row else None


def next_cursor(
    old: Cursor | None,
    committed: Iterable[datetime],
    failed: Iterable[datetime | None],
) -> Cursor | None:
    """The cursor to store after a crawl that ran to the end (see module docstring)."""
    failed_list = list(failed)
    if failed_list:
        known = [w for w in failed_list if w is not None]
        if len(known) != len(failed_list):
            return old  # a failed doc without a watermark: we cannot place the cursor safely
        candidate = min(known) - EPSILON
    else:
        committed_list = list(committed)
        if not committed_list:
            return old
        candidate = max(committed_list)
    if old is not None and old.watermark is not None and candidate < old.watermark:
        return old  # never move backwards past what an earlier run already settled
    return Cursor(candidate)


def start_run(conn: psycopg.Connection, source_type: str) -> str:
    row = conn.execute(
        "INSERT INTO kb.ingest_runs (source_type, status) VALUES (%s, 'running') "
        "RETURNING id::text",
        (source_type,),
    ).fetchone()
    assert row is not None
    return str(row[0])


def finish_run(
    conn: psycopg.Connection,
    run_id: str,
    *,
    status: str,
    seen: int,
    upserted: int,
    skipped: int,
    failed: int,
    chunks: int,
    errors: list[dict[str, Any]],
) -> None:
    conn.execute(
        "UPDATE kb.ingest_runs SET finished_at = now(), status = %s, documents_seen = %s, "
        "documents_upserted = %s, documents_skipped = %s, documents_failed = %s, "
        "chunks_written = %s, error_summary = %s::jsonb WHERE id = %s",
        (status, seen, upserted, skipped, failed, chunks, json.dumps(errors) if errors else None,
         run_id),
    )  # fmt: skip


def save_state(
    conn: psycopg.Connection, source_type: str, cursor: Cursor | None, run_id: str
) -> None:
    """Crawl ran to the end: store the cursor and the freshness timestamp."""
    conn.execute(
        "INSERT INTO kb.ingest_source_state (source_type, cursor, last_success_at, last_run_id) "
        "VALUES (%s, %s::jsonb, now(), %s) ON CONFLICT (source_type) DO UPDATE SET "
        "cursor = EXCLUDED.cursor, last_success_at = now(), last_run_id = EXCLUDED.last_run_id",
        (source_type, json.dumps(cursor.to_json() if cursor else None), run_id),
    )
