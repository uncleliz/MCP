"""`mcp-ingest` command line (Typer). T-056 ships `db upgrade`; the other commands of the
contract (`run`, `status`, `sources`, `reembed`, `prune`) and the exit-code/report plumbing arrive
with T-068.

This is a CLI, not an MCP server: there is no stdio JSON-RPC channel to protect, so reports go to
stdout; diagnostics go to stderr.
"""

from __future__ import annotations

import psycopg
import typer
from mcp_common.config import SourceMisconfiguredError, load_settings

from mcp_ingest import db
from mcp_ingest.settings import Settings

__all__ = ["app", "main"]

EXIT_OK = 0
EXIT_FAILED = 2

app = typer.Typer(
    name="mcp-ingest",
    help="Ingest pipeline for the kb embedding store (the only component that writes to it).",
    no_args_is_help=True,
    add_completion=False,
)
db_app = typer.Typer(name="db", help="Database schema management.", no_args_is_help=True)
app.add_typer(db_app)


def _fail(message: str) -> typer.Exit:
    typer.echo(f"error: {message}", err=True)
    return typer.Exit(EXIT_FAILED)


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
        # The message of a connection error can echo connection parameters; keep it generic.
        raise _fail(
            f"cannot connect to the database ({type(exc).__name__}); check MCP_INGEST_ADMIN_DSN"
        ) from exc
    if as_json:
        typer.echo(result.model_dump_json(indent=2))
        return
    verb = "would apply" if dry_run else "applied"
    typer.echo(f"{verb}: {', '.join(result.applied) or '(nothing)'}")
    typer.echo(f"current version: {result.current_version}")


def main() -> None:
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
