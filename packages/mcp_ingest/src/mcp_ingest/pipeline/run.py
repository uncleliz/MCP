"""`mcp-ingest run`: orchestrates the stages for each source, isolating failures per source
(FR-012/AC-002). See `docs/squad/mcp-data-platform/architecture.md` "FR-012 — Ingest/embedding
pipeline" for the sequence this implements.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

import psycopg
from mcp_common.config import SourceMisconfiguredError
from mcp_common.errors import ToolError

from mcp_ingest.connectors.base import ConnectorStatus, Cursor, SourceConnector, SourceDocument
from mcp_ingest.embedding.errors import EmbeddingModelMismatchError
from mcp_ingest.embedding.validate import validate_stored_embeddings
from mcp_ingest.pipeline import checkpoint, persist
from mcp_ingest.pipeline.chunk import ChunkConfig, chunk_text
from mcp_ingest.pipeline.embed import embed_chunks
from mcp_ingest.pipeline.failures import clear_failure, list_failure_ids, upsert_failure
from mcp_ingest.pipeline.reconcile import live_document_count, reconcile
from mcp_ingest.pipeline.redact import Blocked, apply_policy
from mcp_ingest.ports import EmbeddingProvider
from mcp_ingest.reports import (
    IngestError,
    IngestRunReport,
    IngestSourceResult,
    overall,
)

__all__ = [
    "ConfigurationError",
    "RunOptions",
    "RunContext",
    "check_store_compatible",
    "run_ingest",
    "run_source",
]

Outcome = Literal["upserted", "skipped", "blocked", "failed"]


class ConfigurationError(RuntimeError):
    """The run cannot start (e.g. the configured embedding model differs from the stored data)."""


@dataclass(frozen=True)
class RunOptions:
    sources: list[str]
    mode: Literal["incremental", "full"] = "incremental"
    since: datetime | None = None
    limit: int | None = None
    retry_failed: bool = False
    dry_run: bool = False


@dataclass
class RunContext:
    dsn: str
    provider: EmbeddingProvider
    chunk_config: ChunkConfig
    deny_globs: list[str]
    max_retries: int = 2
    batch_size: int = 16
    backoff_s: float = 0.5
    sleep: Callable[[float], None] = time.sleep
    now: Callable[[], datetime] = lambda: datetime.now(UTC)


class _StageFailure(Exception):
    def __init__(self, stage: str, code: str, message: str, retryable: bool) -> None:
        super().__init__(message)
        self.stage, self.code, self.retryable = stage, code, retryable


@dataclass
class _Counters:
    seen: int = 0
    upserted: int = 0
    skipped: int = 0
    failed: int = 0
    chunks: int = 0
    committed: list[datetime] = field(default_factory=list)
    failed_marks: list[datetime | None] = field(default_factory=list)
    errors: list[IngestError] = field(default_factory=list)
    processed: set[str] = field(default_factory=set)


# -- store compatibility (T-058: model/dimension must match the stored data) ------------------


def check_store_compatible(conn: psycopg.Connection, provider: EmbeddingProvider) -> None:
    models = {
        r[0] for r in conn.execute("SELECT DISTINCT embedding_model FROM kb.chunks").fetchall()
    }
    row = conn.execute(
        "SELECT a.atttypmod FROM pg_attribute a WHERE a.attrelid = 'kb.chunks'::regclass "
        "AND a.attname = 'embedding'"
    ).fetchone()
    dimensions = int(row[0]) if row and row[0] and row[0] > 0 else None
    try:
        validate_stored_embeddings(provider, stored_models=models, stored_dimensions=dimensions)
    except EmbeddingModelMismatchError as exc:
        raise ConfigurationError(str(exc)) from exc


# -- one document ------------------------------------------------------------------------------


def _process_once(
    conn: psycopg.Connection,
    ctx: RunContext,
    document: SourceDocument,
    run_id: str | None,
    dry_run: bool,
) -> tuple[Outcome, int]:
    try:
        decision = apply_policy(document, ctx.deny_globs)
    except Exception as exc:  # noqa: BLE001 - reported with its stage, never fatal to the run
        raise _StageFailure("normalize", "internal", f"{type(exc).__name__}: {exc}", False) from exc
    existing = persist.get_existing(conn, document.source_type, document.source_id)
    if isinstance(decision, Blocked):
        if not dry_run:
            persist.purge_blocked(
                conn, existing=existing, document=document, run_id=run_id,
                reason=decision.reason, code=decision.code,
            )  # fmt: skip
        return "blocked", 0
    config_hash = ctx.chunk_config.hash
    unchanged = (
        existing is not None
        and existing.live
        and existing.content_hash == decision.content_hash
        and existing.chunk_config_hash == config_hash
    )
    if unchanged:
        if not dry_run and existing is not None and run_id is not None:
            persist.touch_metadata(conn, existing.id, document, decision.title, run_id)
            clear_failure(conn, document.source_type, document.source_id)
        return "skipped", 0
    try:
        chunks = chunk_text(decision.text, ctx.chunk_config)
    except Exception as exc:  # noqa: BLE001
        raise _StageFailure("chunk", "internal", f"{type(exc).__name__}: {exc}", False) from exc
    if dry_run:
        return "upserted", len(chunks)
    try:
        vectors = embed_chunks(ctx.provider, chunks, ctx.batch_size)
    except Exception as exc:  # noqa: BLE001
        raise _StageFailure(
            "embed", "upstream_error", f"{type(exc).__name__}: {exc}", True
        ) from exc
    assert run_id is not None
    try:
        written = persist.persist_changed(
            conn, document=document, title=decision.title, run_id=run_id,
            content_hash=decision.content_hash, chunk_config_hash=config_hash,
            redactions=decision.redactions, chunks=chunks, vectors=vectors,
            model_id=ctx.provider.model_id,
        )  # fmt: skip
        clear_failure(conn, document.source_type, document.source_id)
    except psycopg.Error as exc:
        raise _StageFailure("persist", "internal", f"{type(exc).__name__}: {exc}", True) from exc
    return "upserted", written


def _process_document(
    conn: psycopg.Connection,
    ctx: RunContext,
    document: SourceDocument,
    run_id: str | None,
    counters: _Counters,
    dry_run: bool,
) -> None:
    if document.source_id in counters.processed:
        return  # already handled in this run (retry-failed + crawl overlap)
    counters.processed.add(document.source_id)
    counters.seen += 1
    attempts = 0
    while True:
        attempts += 1
        try:
            outcome, written = _process_once(conn, ctx, document, run_id, dry_run)
        except _StageFailure as failure:
            if failure.retryable and attempts <= ctx.max_retries:
                ctx.sleep(ctx.backoff_s * attempts)
                continue
            counters.failed += 1
            counters.failed_marks.append(document.source_updated_at)
            counters.errors.append(
                IngestError(
                    stage=failure.stage,  # type: ignore[arg-type]
                    source_id=document.source_id,
                    code=failure.code,
                    message=str(failure)[:1000],
                    retryable=failure.retryable,
                )
            )
            if not dry_run:
                upsert_failure(
                    conn, source_type=document.source_type, source_id=document.source_id,
                    stage=failure.stage, code=failure.code, error=str(failure), attempts=attempts,
                )  # fmt: skip
            return
        counters.chunks += written
        if outcome == "upserted":
            counters.upserted += 1
        else:  # skipped, or blocked by policy (a decision, not a failure)
            counters.skipped += 1
        if document.source_updated_at is not None:
            counters.committed.append(document.source_updated_at)
        return


# -- one source --------------------------------------------------------------------------------


def _tool_error(exc: Exception) -> IngestError:
    if isinstance(exc, ToolError):
        return IngestError(
            stage="crawl", code=exc.code.value, message=str(exc.message)[:1000],
            retryable=bool(exc.retryable),
        )  # fmt: skip
    return IngestError(
        stage="crawl",
        code="internal",
        message=f"{type(exc).__name__}: {exc}"[:1000],
        retryable=True,
    )


def _iterate(
    connector: SourceConnector, cursor: Cursor | None, options: RunOptions
) -> Iterator[SourceDocument]:
    since = Cursor(options.since) if options.since else cursor
    return connector.iter_documents(since, options.mode, limit=options.limit)


def run_source(
    ctx: RunContext,
    connector: SourceConnector,
    options: RunOptions,
    *,
    conn: psycopg.Connection,
) -> IngestSourceResult:
    """Crawl one source on `conn` (a normal autocommit connection of role `mcp_ingest_rw`)."""
    source = connector.source_type
    started = time.monotonic()
    dry = options.dry_run
    counters = _Counters()
    run_id = None if dry else checkpoint.start_run(conn, source)
    old_cursor = checkpoint.load_cursor(conn, source)
    documents_before = live_document_count(conn, source)
    crawl_error: IngestError | None = None
    try:
        if options.retry_failed:
            for document in connector.fetch_documents(list_failure_ids(conn, source)):
                _process_document(conn, ctx, document, run_id, counters, dry)
        for document in _iterate(connector, old_cursor, options):
            _process_document(conn, ctx, document, run_id, counters, dry)
    except Exception as exc:  # noqa: BLE001 - a dead source must not stop the other sources
        crawl_error = _tool_error(exc)
        counters.errors.append(crawl_error)

    completed = crawl_error is None
    tombstoned: int | None = None
    cursor_advanced = False
    if completed and not dry and options.since is None:
        new_cursor = checkpoint.next_cursor(old_cursor, counters.committed, counters.failed_marks)
        cursor_advanced = new_cursor != old_cursor
        assert run_id is not None
        checkpoint.save_state(conn, source, new_cursor, run_id)
    if options.mode == "full" and not dry and options.limit is None and options.since is None:
        assert run_id is not None
        outcome = reconcile(
            conn, source_type=source, run_id=run_id,
            run_succeeded=completed and counters.failed == 0,
            documents_seen=counters.seen, documents_before=documents_before,
        )  # fmt: skip
        tombstoned = outcome.tombstoned
        if outcome.blocked_reason:
            counters.errors.append(
                IngestError(
                    stage="reconcile", code="upstream_error",
                    message=outcome.blocked_reason, retryable=False,
                )
            )  # fmt: skip

    if not completed:
        status = "partial" if counters.upserted or counters.skipped else "failed"
    elif counters.failed or counters.errors:
        status = "partial"
    else:
        status = "success"
    if run_id is not None:
        checkpoint.finish_run(
            conn, run_id, status=status, seen=counters.seen, upserted=counters.upserted,
            skipped=counters.skipped, failed=counters.failed, chunks=counters.chunks,
            errors=[e.model_dump() for e in counters.errors],
        )  # fmt: skip
    return IngestSourceResult(
        source_type=source,
        run_id=run_id,
        status="failed"
        if status == "failed"
        else ("success" if status == "success" else "partial"),
        documents_seen=counters.seen,
        documents_upserted=counters.upserted,
        documents_skipped=counters.skipped,
        documents_failed=counters.failed,
        documents_tombstoned=tombstoned,
        chunks_written=counters.chunks,
        duration_s=round(time.monotonic() - started, 3),
        cursor_advanced=cursor_advanced,
        errors=counters.errors,
    )


# -- the whole run -----------------------------------------------------------------------------


def _skipped(source: str, *errors: IngestError) -> IngestSourceResult:
    return IngestSourceResult(source_type=source, status="skipped", errors=list(errors))


def run_ingest(
    ctx: RunContext,
    options: RunOptions,
    *,
    describe: Callable[[str], ConnectorStatus],
    build: Callable[[str], SourceConnector],
    explicit_source: bool,
) -> IngestRunReport:
    started = ctx.now()
    results: list[IngestSourceResult] = []
    locked: set[str] = set()
    with psycopg.connect(ctx.dsn, autocommit=True, connect_timeout=10) as conn:
        if not options.dry_run:
            check_store_compatible(conn, ctx.provider)
        for source in options.sources:
            status = describe(source)
            if not status.enabled:
                results.append(_skipped(source))  # disabled (e.g. opensearch default): no crawl
                continue
            if not status.configured:
                message = "missing configuration: " + ", ".join(status.missing_env)
                error = IngestError(
                    stage="config", code="source_misconfigured", message=message, retryable=False
                )
                results.append(
                    IngestSourceResult(source_type=source, status="failed", errors=[error])
                    if explicit_source
                    else _skipped(source, error)
                )
                continue
            lock_conn = None if options.dry_run else checkpoint.open_lock_session(ctx.dsn)
            try:
                if lock_conn is not None and not checkpoint.try_lock(lock_conn, source):
                    locked.add(source)
                    results.append(
                        _skipped(
                            source,
                            IngestError(
                                stage="crawl", code="internal", retryable=True,
                                message="another mcp-ingest process holds the lock for this source",
                            ),
                        )
                    )  # fmt: skip
                    continue
                connector: SourceConnector | None = None
                try:
                    connector = build(source)
                    results.append(run_source(ctx, connector, options, conn=conn))
                except Exception as exc:  # noqa: BLE001 - e.g. credentials missing at build time
                    if isinstance(exc, SourceMisconfiguredError):
                        error = IngestError(
                            stage="config", code="source_misconfigured", retryable=False,
                            message=str(exc)[:1000],
                        )  # fmt: skip
                    else:
                        error = _tool_error(exc).model_copy(update={"stage": "connect"})
                    results.append(
                        IngestSourceResult(source_type=source, status="failed", errors=[error])
                    )
                finally:
                    if connector is not None:
                        connector.close()
            finally:
                if lock_conn is not None:
                    lock_conn.close()
    status_name, exit_code = overall(results, locked=locked)
    return IngestRunReport(
        mode=options.mode,
        started_at=started,
        finished_at=ctx.now(),
        status=status_name,  # type: ignore[arg-type]
        exit_code=exit_code,  # type: ignore[arg-type]
        embedding_model=ctx.provider.model_id,
        dry_run=options.dry_run,
        sources=results,
    )
