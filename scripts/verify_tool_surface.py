#!/usr/bin/env python3
"""Cross-cutting verification of the whole platform (T-085, NFR-001/002/005, FR-014).

The AUTOMATED half of the Phase 3 sign-off; the manual half (real sources, Claude Desktop) is the
checklist in docs/signoff/phase-3.md. Builds the 9 MCP servers *without credentials* (tools register
lazily) and checks that:

  1. exactly 49 tools are registered (opensearch with its DSN escape hatch on), they are exactly
     the non-CLI operations of api-contract.yaml, and each package's `tools.snapshot.json`
     matches the contract;
  2. `assert_readonly_tool_surface` holds for every package, including the client operations
     `mcp-ingest`'s connectors call directly on `client.py` (they must be in the allowlist);
  3. an unknown write tool is rejected at the JSON-RPC layer by all 9 servers (FR-014/AC-002);
  4. 3 prompts exist (`dev_knowledge_lookup`, `incident_investigation`, `semantic_synthesis`) and
     `mcp-ingest` exposes its 6 commands;
  5. every server has `doctor`, `serve`, `tools-dump` commands;
  6. the write-capable ingest credential (`mcp_ingest_rw` / `MCP_INGEST_PGVECTOR_DSN` /
     `MCP_INGEST_ADMIN_DSN`) appears in the env of NO MCP server: neither in the `config-emit`
     snippets nor in any server package source.

    uv run python scripts/verify_tool_surface.py          # prints a table, exit 1 on any failure
    uv run python scripts/verify_tool_surface.py --json
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PACKAGES_DIR = ROOT / "packages"

# (package, contract tag, name of its client allowlist, prompt it registers)
SERVERS: list[tuple[str, str, str | None, str | None]] = [
    ("mcp_confluence", "confluence", "ALLOWED_OPERATIONS", "dev_knowledge_lookup"),
    ("mcp_gitlab", "gitlab", "ALLOWED_OPERATIONS", None),
    ("mcp_opensearch", "opensearch", "ALLOWED_OPERATIONS", None),
    ("mcp_kibana", "kibana", "ALLOWED_OPERATIONS", None),
    ("mcp_cloudwatch", "cloudwatch", "ALLOWED_OPERATIONS", "incident_investigation"),
    ("mcp_kafka", "kafka", None, None),
    ("mcp_redis", "redis", "ALLOWED_COMMANDS", None),
    ("mcp_sqs_sns", "sqs-sns", "ALLOWED_OPERATIONS", None),
    ("mcp_pgvector", "pgvector", "ALLOWED_STATEMENTS", "semantic_synthesis"),
    # CHG-001 — source #10 (Jira, ADR-0019) + the Company Knowledge tier (ADR-0017 Option C).
    ("mcp_jira", "jira", "ALLOWED_OPERATIONS", None),
    ("mcp_knowledge", "knowledge", None, "company_knowledge_lookup"),
]
SERVER_CLI_NAMES = {pkg: pkg.replace("_", "-") for pkg, *_ in SERVERS}
# 48 base (Phase 1/2 + pgvector) + 5 Jira + 8 Knowledge = 61 default; 62 with the opensearch DSL
# escape hatch on (TC-104). The script builds opensearch with `allow_dsl=True`, so it expects 62.
EXPECTED_TOOLS = 62
EXPECTED_CLI_COMMANDS = ("db", "run", "status", "sources", "reembed", "prune")
INGEST_CREDENTIAL_MARKERS = (
    "MCP_INGEST_PGVECTOR_DSN", "MCP_INGEST_ADMIN_DSN", "mcp_ingest_rw",
    "pgvector_dsn", "admin_dsn",
)  # fmt: skip
WRITE_TOOL_PROBES = ("create_item", "delete_item", "update_item", "publish_message", "write_key")


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


def _contract() -> dict[str, Any]:
    from mcp_common.contract_testing import load_contract

    return load_contract()


def _build_server(package: str) -> Any:
    module = importlib.import_module(f"{package}.server")
    if package == "mcp_opensearch":
        return module.build_server(allow_dsl=True)  # the 49th tool is behind a flag
    return module.build_server()


def _snapshot(package: str) -> dict[str, Any]:
    module = importlib.import_module(package)
    path = Path(module.__file__).parent / "tools.snapshot.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _connector_operations() -> dict[str, tuple[str, ...]]:
    from mcp_ingest.connectors.confluence import ConfluenceConnector
    from mcp_ingest.connectors.gitlab import GitLabConnector
    from mcp_ingest.connectors.opensearch import OpenSearchConnector

    return {
        "mcp_confluence": ConfluenceConnector.operations_used,
        "mcp_gitlab": GitLabConnector.operations_used,
        "mcp_opensearch": OpenSearchConnector.operations_used,
    }


def check_tool_count_and_contract() -> list[Check]:
    from mcp_common.contract_testing import assert_snapshot_matches_contract, operations_by_id
    from mcp_common.tooling import registered_tool_functions

    contract = _contract()
    expected = {
        op_id for op_id, op in operations_by_id(contract).items() if op.get("x-interface") != "cli"
    }
    registered: set[str] = set()
    checks: list[Check] = []
    for package, tag, _allow, _prompt in SERVERS:
        tools = set(registered_tool_functions(_build_server(package)))
        registered |= tools
        try:
            assert_snapshot_matches_contract(_snapshot(package), contract, tag=tag)
            checks.append(Check(f"snapshot == contract [{tag}]", True, f"{len(tools)} tools"))
        except AssertionError as exc:
            checks.append(Check(f"snapshot == contract [{tag}]", False, str(exc)[:300]))
    checks.append(
        Check(
            f"{EXPECTED_TOOLS} tools registered and equal to the contract's non-CLI operations",
            len(registered) == EXPECTED_TOOLS and registered == expected,
            f"registered={len(registered)} contract={len(expected)} "
            f"diff={sorted(registered ^ expected)}",
        )
    )
    return checks


def check_readonly_surface() -> list[Check]:
    from mcp_common.contract_testing import operations_by_id
    from mcp_common.testing import assert_readonly_tool_surface
    from mcp_common.tooling import registered_tool_functions

    operations = operations_by_id(_contract())
    connector_ops = _connector_operations()
    checks: list[Check] = []
    for package, _tag, allow_name, _prompt in SERVERS:
        allowlist: tuple[str, ...] = ()
        if allow_name:
            allowlist = tuple(getattr(importlib.import_module(f"{package}.client"), allow_name))
        try:
            assert_readonly_tool_surface(
                registered_tools=registered_tool_functions(_build_server(package)),
                contract_operations=operations,
                snapshot=_snapshot(package),
                client_allowlist=allowlist,
                additional_client_operations=connector_ops.get(package, ()),
            )
            extra = (
                f", + {len(connector_ops[package])} ingest ops" if package in connector_ops else ""
            )
            checks.append(Check(f"read-only surface [{package}]", True, f"0 write tools{extra}"))
        except AssertionError as exc:
            checks.append(Check(f"read-only surface [{package}]", False, str(exc)[:300]))
    return checks


async def _check_unknown_tools() -> list[Check]:
    from mcp_common.testing import assert_unknown_tool_rejected_at_protocol_layer

    checks: list[Check] = []
    for package, *_ in SERVERS:
        server = _build_server(package)
        try:
            for probe in WRITE_TOOL_PROBES:
                await assert_unknown_tool_rejected_at_protocol_layer(server, probe, {"key": "x"})
            checks.append(Check(f"unknown write tool rejected by JSON-RPC [{package}]", True))
        except Exception as exc:  # noqa: BLE001
            checks.append(
                Check(f"unknown write tool rejected by JSON-RPC [{package}]", False, repr(exc))
            )
    return checks


async def _check_prompts() -> list[Check]:
    from mcp.shared.memory import create_connected_server_and_client_session

    found: dict[str, str] = {}
    for package, _tag, _allow, prompt in SERVERS:
        server = _build_server(package)
        async with create_connected_server_and_client_session(server) as session:
            listed = {p.name for p in (await session.list_prompts()).prompts}
        if prompt:
            found[prompt] = package
            if prompt not in listed:
                return [Check("4 prompts present", False, f"{package} does not list {prompt}")]
        elif listed:
            return [
                Check("4 prompts present", False, f"{package} lists unexpected {sorted(listed)}")
            ]
    return [Check("4 prompts present", len(found) == 4, ", ".join(sorted(found)))]


def check_ingest_cli_commands() -> list[Check]:
    from mcp_ingest.cli import app
    from typer.testing import CliRunner

    runner = CliRunner()
    help_text = runner.invoke(app, ["--help"]).output
    missing = [c for c in EXPECTED_CLI_COMMANDS if c not in help_text]
    sub_ok = all(
        runner.invoke(app, [*cmd.split(), "--help"]).exit_code == 0
        for cmd in ("db upgrade", "run", "status", "sources", "reembed", "prune")
    )
    return [Check("6 mcp-ingest commands present (each with working --help)", not missing and sub_ok,  # noqa: E501
                  f"missing={missing}")]  # fmt: skip


def check_server_cli_commands() -> list[Check]:
    checks = []
    for package, *_ in SERVERS:
        cli = importlib.import_module(f"{package}.cli")
        source = Path(cli.__file__).read_text(encoding="utf-8")
        missing = [c for c in ("doctor", "serve", "tools-dump") if f'"{c}"' not in source]
        checks.append(
            Check(
                f"doctor/serve/tools-dump commands [{package}]", not missing, f"missing={missing}"
            )
        )
    return checks


def check_credential_hygiene() -> list[Check]:
    from mcp_common.cli import build_config_emit_payload, known_servers

    checks: list[Check] = []
    leaks: list[str] = []
    for server in known_servers():
        env = build_config_emit_payload(server)["mcpServers"][server]["env"]
        leaks += [
            f"{server}:{name}" for name in env if "INGEST" in name and "EMBEDDING" not in name
        ]
    checks.append(
        Check("mcp_ingest_rw is in no `config-emit` server env", not leaks, ", ".join(leaks))
    )

    source_leaks: list[str] = []
    for package, *_ in SERVERS:
        for path in (PACKAGES_DIR / package / "src").rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for marker in INGEST_CREDENTIAL_MARKERS:
                if marker in text and not _is_documentation_only(text, marker, package):
                    source_leaks.append(f"{path.relative_to(ROOT)}:{marker}")
    checks.append(Check("no MCP server reads or names the ingest write credential in code",
                        not source_leaks, ", ".join(sorted(set(source_leaks)))))  # fmt: skip

    docs: list[str] = []
    for path in [*(ROOT / "docs").rglob("*.md"), ROOT / "README.md"]:
        if "squad" in path.parts or not path.is_file():
            continue
        for match in re.finditer(r"```json(.*?)```", path.read_text(encoding="utf-8"), re.S):
            block = match.group(1)
            if "mcpServers" in block and any(m in block for m in INGEST_CREDENTIAL_MARKERS):
                docs.append(str(path.relative_to(ROOT)))
    checks.append(Check("no Claude Desktop config example carries the ingest credential",
                        not docs, ", ".join(docs)))  # fmt: skip
    return checks


def _is_documentation_only(text: str, marker: str, package: str) -> bool:
    """`mcp_pgvector` and `mcp_knowledge` legitimately *refuse* the write-capable role and say so in
    messages and comments; what must never happen is reading it from the environment."""
    if package not in ("mcp_pgvector", "mcp_knowledge"):
        return False
    for line in text.splitlines():
        if marker in line and not line.lstrip().startswith(("#", '"""', "'")):
            stripped = line.strip()
            if "environ" in stripped or "getenv" in stripped or "env_prefix" in stripped:
                return False
    return True


def run_all() -> list[Check]:
    checks: list[Check] = []
    checks += check_tool_count_and_contract()
    checks += check_readonly_surface()
    checks += asyncio.run(_check_unknown_tools())
    checks += asyncio.run(_check_prompts())
    checks += check_ingest_cli_commands()
    checks += check_server_cli_commands()
    checks += check_credential_hygiene()
    return checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    checks = run_all()
    if args.json:
        print(json.dumps([c.__dict__ for c in checks], indent=2))
    else:
        for check in checks:
            print(
                f"{'PASS' if check.ok else 'FAIL'}  {check.name}"
                + (f"  ({check.detail})" if check.detail else "")
            )
        failed = [c for c in checks if not c.ok]
        print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed")
    return 0 if all(c.ok for c in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
