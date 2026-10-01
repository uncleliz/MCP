"""`mcp-kafka` command line: `serve` (default), `doctor`, `tools-dump`.

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

from mcp_kafka.client import SOURCE, KafkaClient
from mcp_kafka.server import SERVER_NAME, build_server
from mcp_kafka.settings import Settings

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
    client = KafkaClient(settings, common=common)

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
    _out(
        f"config: ok (bootstrap={settings.bootstrap_servers}, "
        f"security={settings.security_protocol})"
    )

    async def run() -> bool:
        client = KafkaClient(settings, common=common)
        try:
            report = await client.verify_credentials()
        finally:
            await client.aclose()
        _out(f"credentials + read-only check: {'ok' if report.ok else 'FAILED'}")
        for reason in report.reasons:
            _out(f"  - {reason}")
        for warning in report.warnings:
            _out(f"  ! {warning}")
        return report.ok

    return 0 if asyncio.run(run()) else 1


def _tools_dump() -> int:
    snapshot = asyncio.run(build_snapshot(build_server()))
    _out(json.dumps(snapshot, indent=2, ensure_ascii=False, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mcp-kafka", description=__doc__)
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
