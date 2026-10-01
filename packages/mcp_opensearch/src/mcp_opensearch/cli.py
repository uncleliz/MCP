"""`mcp-opensearch` command line: `serve` (default), `doctor`, `tools-dump`.

Only `serve` speaks the MCP protocol on stdout; `doctor` / `tools-dump` are interactive
diagnostics and write their report to stdout via `sys.stdout.write` (never `print`).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from mcp_common.config import CommonSettings, SourceMisconfiguredError, load_settings
from mcp_common.contract_testing import build_snapshot
from mcp_common.runtime import serve

from mcp_opensearch.client import SOURCE, OpenSearchClient
from mcp_opensearch.server import SERVER_NAME, build_server
from mcp_opensearch.settings import Settings

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
    client = OpenSearchClient(settings, common=common)

    async def run() -> None:
        try:
            await serve(
                lambda: build_server(settings=settings, common=common),
                server_name=SERVER_NAME,
                credential_check=client.credential_check,
                settings=common,
            )
        finally:
            await client.aclose()

    try:
        asyncio.run(run())
    except SourceMisconfiguredError as exc:
        _err(f"refusing to serve: {exc}")
        return 2
    return 0


def _doctor() -> int:
    loaded = _load()
    if loaded is None:
        return 2
    settings, common = loaded
    dsl = "on" if settings.allow_dsl else "off"
    auth = "basic" if settings.username else "none"
    _out(f"config: ok (hosts={len(settings.host_list)}, dsl={dsl}, auth={auth})")

    async def run() -> bool:
        client = OpenSearchClient(settings, common=common)
        try:
            report = await client.verify_credentials()
        finally:
            await client.aclose()
        _out(f"credentials + read-only check: {'ok' if report.ok else 'FAILED'}")
        if report.user:
            _out(f"  user: {report.user}")
        for reason in report.reasons:
            _out(f"  - {reason}")
        return report.ok

    return 0 if asyncio.run(run()) else 1


def _tools_dump() -> int:
    snapshot = asyncio.run(build_snapshot(build_server(allow_dsl=True)))  # full contract surface
    _out(json.dumps(snapshot, indent=2, ensure_ascii=False, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mcp-opensearch", description=__doc__)
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("serve", help="run the MCP server on stdio (default)")
    sub.add_parser("doctor", help="check config, credentials and read-only permissions")
    sub.add_parser("tools-dump", help="print the tool surface (tools.snapshot.json content)")
    args = parser.parse_args(argv)
    command = args.command or "serve"
    if command == "doctor":
        return _doctor()
    if command == "tools-dump":
        return _tools_dump()
    return _serve()
