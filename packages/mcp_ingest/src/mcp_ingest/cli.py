"""`mcp-ingest` command line (Typer): `db upgrade`, `run`, `status`, `sources`, `reembed`, `prune`.

This is a CLI, not an MCP server: there is no stdio JSON-RPC channel to protect, so reports go to
stdout (`--json` = the contract's report schemas); structured JSON logs and diagnostics go to
stderr. Exit codes: 0 success, 1 partial, 2 failed, 3 lock held (`run`); every other command
exits 0 or 2.

DSN discipline (ADR-0011 A6): only `db upgrade` reads `MCP_INGEST_ADMIN_DSN`; every other command
uses `Settings.runtime_dsn()` (`mcp_ingest_rw`).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime
from typing import Annotated

import psycopg
import typer
from mcp_common.config import SourceMisconfiguredError, load_settings
from mcp_common.logging import setup_logging
from pydantic import BaseModel

from mcp_ingest import db
from mcp_ingest.connectors import registry
from mcp_ingest.connectors.base import ConnectorStatus, SourceConnector
from mcp_ingest.embedding import EmbeddingSettings, build_provider
from mcp_ingest.pipeline.chunk import ChunkConfig
from mcp_ingest.pipeline.redact import deny_globs_from_env
from mcp_ingest.pipeline.run import ConfigurationError, RunContext, RunOptions, run_ingest
from mcp_ingest.ports import EmbeddingProvider
from mcp_ingest.reports import EXIT_FAILED, IngestRunReport
from mcp_ingest.settings import Settings

__all__ = ["app", "main"]

SOURCE_CHOICES = ("all", "confluence", "gitlab", "opensearch")

app = typer.Typer(
    name="mcp-ingest",
    help="Ingest pipeline for the kb embedding store (the only component that writes to it).",
    no_args_is_help=True,
    add_completion=False,
)
db_app = typer.Typer(name="db", help="Database schema management.", no_args_is_help=True)
app.add_typer(db_app)

# Seams for tests: the embedding provider and the connectors are injected, never faked in prod.
provider_factory: Callable[[EmbeddingSettings], EmbeddingProvider] = build_provider
connector_builder: Callable[[str, Settings], SourceConnector] = registry.build


def _describe_connector(name: str, settings: Settings) -> ConnectorStatus:
    return registry.REGISTRY()[name].status(settings)


connector_describer: Callable[[str, Settings], ConnectorStatus] = _describe_connector


def _fail(message: str) -> typer.Exit:
    typer.echo(f"error: {message}", err=True)
    return typer.Exit(EXIT_FAILED)


def _settings() -> Settings:
    try:
        return load_settings(Settings, source="ingest")
    except SourceMisconfiguredError as exc:
        raise _fail(f"config error: {exc}") from exc


def _runtime_dsn(settings: Settings) -> str:
    try:
        return settings.runtime_dsn()
    except SourceMisconfiguredError as exc:
        raise _fail(f"config error: {exc}") from exc


def _connect(dsn: str) -> psycopg.Connection:
    try:
        return psycopg.connect(dsn, autocommit=True, connect_timeout=10)
    except psycopg.OperationalError as exc:
        # The message of a connection error can echo connection parameters; keep it generic.
        raise _fail(
            f"cannot connect to the database ({type(exc).__name__}); check MCP_INGEST_PGVECTOR_DSN"
        ) from exc


def _emit(model: BaseModel, as_json: bool, human: Callable[[], list[str]]) -> None:
    if as_json:
        typer.echo(model.model_dump_json(indent=2))
    else:
        for line in human():
            typer.echo(line)


def _sources(choice: str) -> list[str]:
    if choice not in SOURCE_CHOICES:
        raise _fail(f"--source must be one of: {', '.join(SOURCE_CHOICES)}")
    return registry.source_names() if choice == "all" else [choice]


def _older_than(value: str) -> int:
    match = re.fullmatch(r"(\d+)d?", value.strip())
    if not match or int(match.group(1)) < 1:
        raise _fail("--older-than must look like 30d (a number of days >= 1)")
    return int(match.group(1))


# -- db upgrade --------------------------------------------------------------------------------


@db_app.command("upgrade")
def db_upgrade(
    dry_run: bool = typer.Option(False, "--dry-run", help="Report pending migrations only."),
    as_json: bool = typer.Option(False, "--json", help="Print a MigrationResult JSON document."),
) -> None:
    """Apply the numbered SQL migrations that have not run yet (idempotent)."""
    try:
        dsn = load_settings(Settings, source="ingest").migration_dsn()
    except SourceMisconfiguredError as exc:
        raise _fail(f"config error: {exc}") from exc
    try:
        with db.connect_for_migration(dsn) as conn:
            result = db.upgrade(conn, dry_run=dry_run)
    except db.MigrationError as exc:
        raise _fail(str(exc)) from exc
    except psycopg.OperationalError as exc:
        raise _fail(
            f"cannot connect to the database ({type(exc).__name__}); check MCP_INGEST_ADMIN_DSN"
        ) from exc
    if as_json:
        typer.echo(result.model_dump_json(indent=2))
        return
    verb = "would apply" if dry_run else "applied"
    typer.echo(f"{verb}: {', '.join(result.applied) or '(nothing)'}")
    typer.echo(f"current version: {result.current_version}")


# -- run ---------------------------------------------------------------------------------------


def _human_run(report: IngestRunReport) -> list[str]:
    lines = [
        f"{report.mode} run: {report.status} (exit {report.exit_code})"
        + (" [dry-run]" if report.dry_run else "")
    ]
    for source in report.sources:
        lines.append(
            f"  {source.source_type}: {source.status} seen={source.documents_seen} "
            f"upserted={source.documents_upserted} skipped={source.documents_skipped} "
            f"failed={source.documents_failed} chunks={source.chunks_written}"
        )
        lines.extend(f"    [{e.stage}] {e.code}: {e.message}" for e in source.errors)
    return lines


@app.command("run")
def run_command(
    source: Annotated[
        str, typer.Option("--source", help="all|confluence|gitlab|opensearch")
    ] = "all",
    mode: Annotated[str, typer.Option("--mode", help="incremental|full")] = "incremental",
    since: Annotated[
        datetime | None, typer.Option("--since", help="Ignore the checkpoint, crawl from here.")
    ] = None,
    limit: Annotated[
        int | None, typer.Option("--limit", min=1, help="Max documents/source.")
    ] = None,
    retry_failed: Annotated[
        bool, typer.Option("--retry-failed", help="Re-fetch documents in kb.ingest_failures first.")
    ] = False,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Crawl+chunk+hash; no embedding, no DB writes.")
    ] = False,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Crawl, redact, chunk, embed and upsert one or all sources."""
    if mode not in ("incremental", "full"):
        raise _fail("--mode must be incremental or full")
    settings = _settings()
    sources = _sources(source)
    dsn = _runtime_dsn(settings)
    log = setup_logging("ingest")
    try:
        provider = provider_factory(EmbeddingSettings())
        chunk_config = ChunkConfig(settings.chunk_tokens, settings.chunk_overlap)
    except ValueError as exc:
        raise _fail(f"config error: {exc}") from exc
    if chunk_config.tokens + chunk_config.overlap > provider.max_input_tokens:
        raise _fail(
            "config error: MCP_INGEST_CHUNK_TOKENS + MCP_INGEST_CHUNK_OVERLAP exceeds the "
            f"embedding model's max input ({provider.max_input_tokens} tokens)"
        )
    ctx = RunContext(
        dsn=dsn, provider=provider, chunk_config=chunk_config, deny_globs=deny_globs_from_env(),
        max_retries=settings.max_doc_retries,
    )  # fmt: skip
    options = RunOptions(
        sources=sources, mode=mode, since=since, limit=limit,  # type: ignore[arg-type]
        retry_failed=retry_failed, dry_run=dry_run,
    )  # fmt: skip
    try:
        report = run_ingest(
            ctx, options,
            describe=lambda name: connector_describer(name, settings),
            build=lambda name: connector_builder(name, settings),
            explicit_source=source != "all",
        )  # fmt: skip
    except ConfigurationError as exc:
        raise _fail(str(exc)) from exc
    except psycopg.OperationalError as exc:
        raise _fail(
            f"cannot connect to the database ({type(exc).__name__}); check MCP_INGEST_PGVECTOR_DSN"
        ) from exc
    for result in report.sources:
        log.info(
            f"source {result.source_type}: {result.status}",
            extra={
                "status": result.status,
                "items_returned": result.documents_upserted,
                "duration_ms": int((result.duration_s or 0) * 1000),
                "error_code": result.errors[0].code if result.errors else None,
            },
        )
    _emit(report, as_json, lambda: _human_run(report))
    raise typer.Exit(report.exit_code)


# -- status / sources --------------------------------------------------------------------------


@app.command("status")
def status_command(
    source: Annotated[str, typer.Option("--source")] = "all",
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Freshness per source: last success, staleness, document/chunk counts."""
    from mcp_ingest.commands.status import failure_counts, gather_status

    settings = _settings()
    if source not in SOURCE_CHOICES:
        raise _fail(f"--source must be one of: {', '.join(SOURCE_CHOICES)}")
    with _connect(_runtime_dsn(settings)) as conn:
        report = gather_status(conn, source=source, known=registry.source_names())
        failures = failure_counts(conn)

    def human() -> list[str]:
        lines = [f"as of {report.as_of.isoformat()}"]
        for row in report.sources:
            stale = "never" if row.staleness_hours is None else f"{row.staleness_hours}h"
            lines.append(
                f"  {row.source_type}: last_success={row.last_success_at or '-'} staleness={stale} "
                f"documents={row.document_count} chunks={row.chunk_count} "
                f"failures={failures.get(row.source_type, 0)} last_run={row.last_run_status or '-'}"
            )
        return lines

    _emit(report, as_json, human)


@app.command("sources")
def sources_command(as_json: Annotated[bool, typer.Option("--json")] = False) -> None:
    """The registered connectors and what is missing to enable each."""
    from mcp_ingest.commands.sources import list_sources

    settings = _settings()
    report = list_sources(settings)

    def human() -> list[str]:
        return [
            f"  {row.source_type}: {row.connector} enabled={row.enabled} "
            f"configured={row.configured}"
            + (f" missing={','.join(row.missing_env)}" if row.missing_env else "")
            for row in report.connectors
        ]

    _emit(report, as_json, human)


# -- reembed / prune ---------------------------------------------------------------------------


@app.command("reembed")
def reembed_command(
    model: Annotated[str, typer.Option("--model", help="Target embedding model id.")],
    source: Annotated[str, typer.Option("--source")] = "all",
    batch_size: Annotated[int, typer.Option("--batch-size", min=1, max=512)] = 64,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Re-embed stored chunks with MODEL (no crawl); safe to interrupt and re-run."""
    from mcp_ingest.commands.reembed import reembed

    settings = _settings()
    sources = _sources(source)
    dsn = _runtime_dsn(settings)
    if not model.strip() or len(model) > 128:
        raise _fail("--model must be 1..128 characters")
    provider = provider_factory(EmbeddingSettings().model_copy(update={"model": model}))
    try:
        with _connect(dsn) as conn:
            report = reembed(conn, provider, sources=sources, batch_size=batch_size)
    except ConfigurationError as exc:
        raise _fail(str(exc)) from exc
    _emit(report, as_json, lambda: _human_run(report))
    raise typer.Exit(report.exit_code)


@app.command("prune")
def prune_command(
    older_than: Annotated[
        str, typer.Option("--older-than", help="REQUIRED, e.g. 30d (no implicit retention).")
    ] = "",
    tombstoned: Annotated[
        bool, typer.Option("--tombstoned", help="Only documents tombstoned that long ago.")
    ] = False,
    source: Annotated[str, typer.Option("--source")] = "all",
    dry_run: Annotated[
        bool, typer.Option("--dry-run/--no-dry-run", help="Default: dry-run (report only).")
    ] = True,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Physically delete tombstoned documents / apply retention. Requires --older-than."""
    from mcp_ingest.commands.prune import prune

    if not older_than:
        raise _fail("prune requires --older-than (e.g. --older-than 30d); there is no default")
    days = _older_than(older_than)
    settings = _settings()
    names = None if source == "all" else _sources(source)
    with _connect(_runtime_dsn(settings)) as conn:
        report = prune(conn, older_than_days=days, tombstoned=tombstoned, sources=names,
                       dry_run=dry_run)  # fmt: skip

    def human() -> list[str]:
        verb = "would delete" if report.dry_run else "deleted"
        lines = [f"{verb} {report.documents_deleted} documents, {report.chunks_deleted} chunks"]
        lines.extend(
            f"  {r.source_type}: {r.documents_deleted} documents, {r.chunks_deleted} chunks"
            for r in report.per_source
        )
        if report.reindex_recommended:
            lines.append(
                "recommended: SET maintenance_work_mem = '1GB'; "
                "REINDEX INDEX CONCURRENTLY kb.chunks_embedding_hnsw;"
            )
        return lines

    _emit(report, as_json, human)


def main() -> None:
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
