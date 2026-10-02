"""`mcp-knowledge` command line: `serve` (default), `doctor`, `tools-dump`.

Only `serve` speaks the MCP protocol on stdout; `doctor` / `tools-dump` are interactive
diagnostics and write their report to stdout via `sys.stdout.write` (never `print`). Audit lines go
to stderr. The server opens **no network port**: stdio only (T-103, NFR-006).

`serve` refuses to start (exit 2) when the startup check finds positive evidence that serving would
be unsafe or wrong — a write-capable role (the `mcp_ingest_rw` DSN pasted into `MCP_KNOWLEDGE_DSN`,
ADR-0003 A1) or an embedding model that differs from the stored chunks (ADR-0010). Those two are
**not** bypassable with `MCP_ALLOW_UNVERIFIED_CREDENTIALS`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from mcp_common.config import CommonSettings, SourceMisconfiguredError, load_settings
from mcp_common.contract_testing import build_snapshot
from mcp_common.runtime import serve
from mcp_ingest.embedding import build_provider

from mcp_knowledge.client import SOURCE, CredentialReport, KnowledgeClient
from mcp_knowledge.server import SERVER_NAME, build_server
from mcp_knowledge.settings import Settings

__all__ = ["main"]


def _out(text: str) -> None:
    sys.stdout.write(text + "\n")


def _err(text: str) -> None:
    sys.stderr.write(text + "\n")


def _load() -> tuple[Settings, CommonSettings] | None:
    try:
        return load_settings(Settings, source=SOURCE), CommonSettings()
    except SourceMisconfiguredError as exc:
        _err(f"config error: {exc}")
        return None


def _serve() -> int:
    loaded = _load()
    if loaded is None:
        return 2
    settings, common = loaded
    provider = build_provider(settings.embedding_settings())
    client = KnowledgeClient(settings.dsn.get_secret_value())

    async def run() -> int:
        try:
            report = await client.verify_credentials(provider)
            for warning in report.warnings:
                _err(f"warning: {warning}")
            if report.fatal:
                for reason in report.reasons:
                    _err(f"refusing to serve: {reason}")
                return 2
            if not report.ok:
                for reason in report.reasons:
                    _err(f"startup check failed: {reason}")
            await serve(
                lambda: build_server(settings=settings, common=common, provider=provider),
                server_name=SERVER_NAME,
                credential_check=lambda: report.ok,
                settings=common,
            )
            return 0
        finally:
            await client.aclose()

    try:
        return asyncio.run(run())
    except SourceMisconfiguredError as exc:
        _err(f"refusing to serve: {exc}")
        return 2


def _print_report(report: CredentialReport) -> None:
    _out(f"credentials + read-only check: {'ok' if report.ok else 'FAILED'}")
    if report.role:
        _out(f"  role: {report.role}")
    for reason in report.reasons:
        _out(f"  - {reason}")
    for warning in report.warnings:
        _out(f"  ! {warning}")


def _doctor() -> int:
    loaded = _load()
    if loaded is None:
        return 2
    settings, common = loaded
    embedding = settings.embedding_settings()
    _out(
        f"config: ok (embedding={embedding.provider}:{embedding.model}, "
        f"dimensions={embedding.dimensions}, reranker_enabled={settings.reranker_enabled})"
    )

    async def run() -> bool:
        client = KnowledgeClient(settings.dsn.get_secret_value())
        try:
            report = await client.verify_credentials(build_provider(embedding))
        finally:
            await client.aclose()
        _print_report(report)
        return report.ok

    return 0 if asyncio.run(run()) else 1


def _tools_dump() -> int:
    snapshot = asyncio.run(build_snapshot(build_server()))
    _out(json.dumps(snapshot, indent=2, ensure_ascii=False, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mcp-knowledge", description=__doc__)
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("serve", help="run the MCP server on stdio (default)")
    sub.add_parser("doctor", help="check config, role read-only-ness and embedding consistency")
    sub.add_parser("tools-dump", help="print the tool surface (tools.snapshot.json content)")
    args = parser.parse_args(argv)
    command = args.command or "serve"
    if command == "doctor":
        return _doctor()
    if command == "tools-dump":
        return _tools_dump()
    return _serve()
