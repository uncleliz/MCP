"""`mcp-ingest reembed --model ...` (T-080, ADR-0010): re-embed stored chunks with a new model
without crawling the sources again.

Resumable by construction: the batch selector is "chunks whose `embedding_model` is not the target
model", and each batch is committed on its own, so an interrupted run simply continues where it
stopped on the next invocation. `documents` are never touched. While a store is half-migrated,
`mcp-pgvector` refuses to serve (mixed models) rather than compare vectors from two spaces.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime

import psycopg

from mcp_ingest.pipeline.chunk import embed_input
from mcp_ingest.pipeline.embed import embed_texts, vector_literal
from mcp_ingest.pipeline.run import ConfigurationError
from mcp_ingest.ports import EmbeddingProvider
from mcp_ingest.reports import IngestError, IngestRunReport, IngestSourceResult, overall

__all__ = ["reembed"]


def _column_dimensions(conn: psycopg.Connection) -> int | None:
    row = conn.execute(
        "SELECT a.atttypmod FROM pg_attribute a WHERE a.attrelid = 'kb.chunks'::regclass "
        "AND a.attname = 'embedding'"
    ).fetchone()
    return int(row[0]) if row and row[0] and row[0] > 0 else None


def reembed(
    conn: psycopg.Connection,
    provider: EmbeddingProvider,
    *,
    sources: list[str],
    batch_size: int = 64,
    max_batches: int | None = None,
) -> IngestRunReport:
    """`max_batches` exists for tests that simulate an interrupted run."""
    started = datetime.now(UTC)
    dimensions = _column_dimensions(conn)
    if dimensions is not None and dimensions != provider.dimensions:
        raise ConfigurationError(
            f"model '{provider.model_id}' produces {provider.dimensions}-d vectors but "
            f"kb.chunks.embedding is vector({dimensions}); add a migration for the new "
            "dimension before re-embedding"
        )
    results: list[IngestSourceResult] = []
    batches = 0
    for source in sources:
        t0 = time.monotonic()
        run = conn.execute(
            "INSERT INTO kb.ingest_runs (source_type, status) VALUES (%s, 'running') "
            "RETURNING id::text",
            (source,),
        ).fetchone()
        assert run is not None
        run_id = str(run[0])
        chunks_done = 0
        docs: set[str] = set()
        errors: list[IngestError] = []
        try:
            while max_batches is None or batches < max_batches:
                rows = conn.execute(
                    "SELECT c.id, c.document_id::text, c.heading_path, c.content FROM kb.chunks c "
                    "JOIN kb.documents d ON d.id = c.document_id "
                    "WHERE d.source_type = %s AND d.deleted_at IS NULL "
                    "AND c.embedding_model IS DISTINCT FROM %s ORDER BY c.id LIMIT %s",
                    (source, provider.model_id, batch_size),
                ).fetchall()
                if not rows:
                    break
                vectors = embed_texts(provider, [embed_input(r[2], r[3]) for r in rows], batch_size)
                with conn.transaction():
                    for (chunk_id, _doc, _heading, _content), vector in zip(
                        rows, vectors, strict=True
                    ):
                        conn.execute(
                            "UPDATE kb.chunks SET embedding = %s::vector, embedding_model = %s, "
                            "embedded_at = now() WHERE id = %s",
                            (vector_literal(vector), provider.model_id, chunk_id),
                        )
                chunks_done += len(rows)
                docs.update(r[1] for r in rows)
                batches += 1
        except Exception as exc:  # noqa: BLE001 - reported, the next source still runs
            errors.append(
                IngestError(
                    stage="embed", code="upstream_error", retryable=True,
                    message=f"{type(exc).__name__}: {exc}"[:1000],
                )
            )  # fmt: skip
        status = "failed" if errors and not chunks_done else ("partial" if errors else "success")
        conn.execute(
            "UPDATE kb.ingest_runs SET finished_at = now(), status = %s, documents_seen = %s, "
            "chunks_written = %s, error_summary = %s::jsonb WHERE id = %s",
            (
                status, len(docs), chunks_done,
                json.dumps([e.model_dump() for e in errors]) if errors else None,
                run_id,
            ),
        )  # fmt: skip
        results.append(
            IngestSourceResult(
                source_type=source, run_id=run_id, status=status,  # type: ignore[arg-type]
                documents_seen=len(docs), chunks_written=chunks_done,
                duration_s=round(time.monotonic() - t0, 3), cursor_advanced=False, errors=errors,
            )
        )  # fmt: skip
    status_name, exit_code = overall(results, locked=set())
    return IngestRunReport(
        mode="reembed", started_at=started, finished_at=datetime.now(UTC),
        status=status_name, exit_code=exit_code,  # type: ignore[arg-type]
        embedding_model=provider.model_id, sources=results,
    )  # fmt: skip
