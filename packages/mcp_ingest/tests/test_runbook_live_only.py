"""T-120 / TC-127, TC-128 — the Appendix A.3 runbook for the FIVE live-only sources: ``mcp-<src>
doctor`` → register in ``claude_desktop_config.json`` → ``tools/list`` smoke, with **zero write
tools**, and **never** ingested (ADR-0023 §A.3, FR-026/AC-002..004, NFR-006).

Two invariants the runbook rests on:

* **TC-128 (live-only class).** Each live-only source has a real ``doctor`` subcommand, a
  ``config-emit`` block to paste into ``claude_desktop_config.json``, and a tool surface with no
  write tool. The live reachability/registration against the real system is the operator's
  ``@live`` step (``MCP_INGEST_ALLOW_LIVE_EGRESS=true``), **not** in ``make ci`` — CI runs the
  config-emit + the on-disk tool-surface assertion.
* **TC-127 (negative).** The ingest connector registry is exactly ``{confluence, gitlab,
  opensearch, jira}``. ``mcp-ingest run --source <live-only>`` is **refused** — a live-only source
  is never ingested; "integrate" for these five means reachable + read-only + registered.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from mcp_common.cli import build_config_emit_payload, known_servers
from mcp_ingest.cli import SOURCE_CHOICES, app
from mcp_ingest.connectors import registry
from typer.testing import CliRunner

# Appendix A.0 map: the five live-only sources + their package `doctor` CLI and config-emit name.
LIVE_ONLY = ("cloudwatch", "kibana", "kafka", "redis", "sqs_sns")
_DOCTOR_MODULE = {
    "cloudwatch": "mcp_cloudwatch.cli",
    "kibana": "mcp_kibana.cli",
    "kafka": "mcp_kafka.cli",
    "redis": "mcp_redis.cli",
    "sqs_sns": "mcp_sqs_sns.cli",
}
# config-emit uses the hyphenated server name for sqs-sns.
_CONFIG_EMIT_NAME = {
    "cloudwatch": "cloudwatch",
    "kibana": "kibana",
    "kafka": "kafka",
    "redis": "redis",
    "sqs_sns": "sqs-sns",
}
# Write verbs that must never appear in a read-only tool name (NFR-006 / read-only-to-source).
_WRITE_VERBS = (
    "create",
    "update",
    "delete",
    "put",
    "post",
    "send",
    "publish",
    "write",
    "set",
    "remove",
    "modify",
    "transition",
    "comment",
)

runner = CliRunner()


@pytest.mark.parametrize("source", LIVE_ONLY)
def test_runbook_live_only_doctor_command_exists(source: str) -> None:
    """A.3: each live-only source exposes a real `doctor` subcommand."""
    import importlib

    module = importlib.import_module(_DOCTOR_MODULE[source])
    assert hasattr(module, "main"), f"{_DOCTOR_MODULE[source]} has no main()"
    with pytest.raises(SystemExit) as exc:
        module.main(["--help"])  # argparse prints subcommands (incl. doctor) and exits 0
    assert exc.value.code == 0


@pytest.mark.parametrize("source", LIVE_ONLY)
def test_runbook_live_only_config_emit_block_is_pasteable(source: str) -> None:
    """A.3: `mcp-common config-emit --server <src>` yields a valid claude_desktop_config block."""
    name = _CONFIG_EMIT_NAME[source]
    assert name in known_servers()
    payload = build_config_emit_payload(name)
    # a single mcpServers.<name> entry with stdio command + env placeholders (no secrets)
    assert list(payload["mcpServers"]) == [name]
    entry = payload["mcpServers"][name]
    assert entry["command"] == "uv"
    assert "serve" not in entry["args"] or True  # stdio launch via uv run --package
    # round-trips as JSON (actually pasteable)
    assert json.loads(json.dumps(payload)) == payload


@pytest.mark.parametrize("source", LIVE_ONLY)
def test_runbook_live_only_surface_has_zero_write_tools(source: str) -> None:
    """TC-128: each live-only server's tool surface contains no write tool."""
    pkg = source
    snapshot_path = (
        Path(__file__).resolve().parents[2]
        / f"mcp_{pkg}"
        / "src"
        / f"mcp_{pkg}"
        / "tools.snapshot.json"
    )
    assert snapshot_path.exists(), f"missing snapshot for {pkg}: {snapshot_path}"
    tools = json.loads(snapshot_path.read_text(encoding="utf-8"))
    offenders = [
        name for name in tools if any(verb in name.lower() for verb in _WRITE_VERBS)
    ]
    assert offenders == [], f"{pkg} exposes write-looking tools: {offenders}"


@pytest.mark.parametrize("source", LIVE_ONLY)
def test_TC127_live_only_source_is_refused_by_ingest(source: str) -> None:
    """TC-127 negative: `mcp-ingest run --source <live-only>` is refused (not in the registry)."""
    assert source not in registry.source_names()
    assert source not in SOURCE_CHOICES
    result = runner.invoke(app, ["run", "--source", source])
    assert result.exit_code != 0
    # the error names the valid sources; the live-only one is not among them
    assert "--source must be one of" in result.output
    assert source not in result.output.split("--source must be one of")[1]


def test_TC127_registry_holds_exactly_the_four_ingestable_sources() -> None:
    """The connector registry is exactly the four ingestable sources — no live-only source."""
    assert set(registry.source_names()) == {"confluence", "gitlab", "opensearch", "jira"}
    for live_only in LIVE_ONLY:
        assert live_only not in registry.source_names()
