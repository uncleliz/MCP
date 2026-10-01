"""T-068: CLI shell, report schemas and exit codes.

AC: FR-012/AC-001, FR-012/AC-002. `--json` output of each command validates against the
`x-interface: cli` operations of api-contract.yaml; exit codes 0/1/2/3 are tested.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import psycopg
import pytest
from ingest_helpers import FakeConnector, assert_valid, doc, provider, q, scalar
from mcp_ingest.pipeline.checkpoint import try_lock
from typer.testing import CliRunner

import mcp_ingest
from mcp_ingest import cli

runner = CliRunner()
COMMANDS = [["db", "upgrade"], ["run"], ["status"], ["sources"], ["reembed"], ["prune"]]


@pytest.fixture
def env(monkeypatch, rw_dsn: str) -> str:
    for name in ("MCP_INGEST_ADMIN_DSN", "MCP_INGEST_PGVECTOR_DSN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MCP_INGEST_PGVECTOR_DSN", rw_dsn)
    monkeypatch.setattr(cli, "provider_factory", lambda settings: provider(settings.model))
    monkeypatch.setenv("MCP_INGEST_EMBEDDING_MODEL", "fake/hashed-bow")
    monkeypatch.setenv("MCP_INGEST_CHUNK_TOKENS", "64")
    monkeypatch.setenv("MCP_INGEST_CHUNK_OVERLAP", "8")
    return rw_dsn


def inject(monkeypatch, connectors: dict[str, FakeConnector]) -> None:
    monkeypatch.setattr(cli.registry, "source_names", lambda: list(connectors))
    monkeypatch.setattr(cli, "connector_builder", lambda name, settings: connectors[name])
    monkeypatch.setattr(
        cli, "connector_describer", lambda name, settings: connectors[name].status()
    )


def invoke(*args: str):
    return runner.invoke(cli.app, list(args))


@pytest.mark.parametrize("command", COMMANDS)
def test_FR_012_AC_001_every_command_has_working_help(command: list[str]) -> None:
    result = invoke(*command, "--help")
    assert result.exit_code == 0 and "Usage" in result.output


def test_the_top_level_help_lists_the_six_commands() -> None:
    out = invoke("--help").output
    for name in ("run", "status", "sources", "reembed", "prune", "db"):
        assert name in out


def test_package_version_is_exposed() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+", mcp_ingest.__version__)


# -- --json validates against the contract -----------------------------------------------------


def test_db_upgrade_json_matches_the_contract(monkeypatch, fresh_db: str) -> None:
    monkeypatch.setenv("MCP_INGEST_ADMIN_DSN", fresh_db)
    monkeypatch.delenv("MCP_INGEST_PGVECTOR_DSN", raising=False)
    dry = invoke("db", "upgrade", "--dry-run", "--json")
    assert dry.exit_code == 0
    assert_valid(json.loads(dry.output), operation_id="ingest_db_upgrade")
    assert json.loads(dry.output)["dry_run"] is True
    real = invoke("db", "upgrade", "--json")
    payload = json.loads(real.output)
    assert_valid(payload, operation_id="ingest_db_upgrade")
    assert payload["current_version"] == "0006_review_followup"
    assert "applied: " not in real.output  # --json prints the JSON document only


def test_run_json_matches_the_contract_and_exit_0(monkeypatch, env: str) -> None:
    inject(
        monkeypatch, {"confluence": FakeConnector("confluence", [doc("1"), doc("2", minutes=1)])}
    )
    result = invoke("run", "--json")
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert_valid(payload, operation_id="ingest_run")
    assert payload["status"] == "success" and payload["exit_code"] == 0
    assert payload["sources"][0]["documents_upserted"] == 2
    assert payload["embedding_model"] == "fake/hashed-bow" and payload["mode"] == "incremental"


def test_exit_code_1_when_one_source_fails(monkeypatch, env: str) -> None:
    inject(monkeypatch, {
        "confluence": FakeConnector("confluence", [doc("1")]),
        "gitlab": FakeConnector("gitlab", [doc("1")], die_after=0),
    })  # fmt: skip
    result = invoke("run", "--json")
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert_valid(payload, operation_id="ingest_run")
    assert (payload["status"], payload["exit_code"]) == ("partial", 1)


def test_exit_code_2_when_everything_fails(monkeypatch, env: str) -> None:
    inject(monkeypatch, {"gitlab": FakeConnector("gitlab", [doc("1")], die_after=0)})
    result = invoke("run", "--json")
    assert result.exit_code == 2
    assert json.loads(result.stdout)["status"] == "failed"


def test_exit_code_3_when_the_source_lock_is_held(monkeypatch, env: str) -> None:
    inject(monkeypatch, {"confluence": FakeConnector("confluence", [doc("1")])})
    with psycopg.connect(env, autocommit=True) as holder:
        assert try_lock(holder, "confluence")
        result = invoke("run", "--json")
    assert result.exit_code == 3
    payload = json.loads(result.stdout)
    assert_valid(payload, operation_id="ingest_run")
    assert payload["exit_code"] == 3 and payload["sources"][0]["status"] == "skipped"


def test_run_human_output_and_json_logs_on_stderr(monkeypatch, env: str) -> None:
    inject(monkeypatch, {"confluence": FakeConnector("confluence", [doc("1")])})
    result = invoke("run")
    assert result.exit_code == 0
    assert "confluence: success" in result.stdout and "[dry-run]" not in result.stdout
    log_lines = [line for line in result.stderr.splitlines() if line.startswith("{")]
    assert log_lines, "structured logs go to stderr"
    record = json.loads(log_lines[-1])
    assert record["server"] == "ingest" and "confluence" in record["message"]
    assert "{" not in result.stdout.replace("\n", "")[:1] and not any(
        line.startswith("{") for line in result.stdout.splitlines()
    ), "stdout carries the report only"


def test_run_dry_run_flag_writes_nothing(monkeypatch, env: str) -> None:
    inject(monkeypatch, {"confluence": FakeConnector("confluence", [doc("1")])})
    result = invoke("run", "--dry-run", "--json")
    assert result.exit_code == 0 and json.loads(result.stdout)["dry_run"] is True
    assert scalar(env, "SELECT count(*) FROM kb.documents") == 0
    assert scalar(env, "SELECT count(*) FROM kb.ingest_runs") == 0


def test_run_rejects_bad_source_and_mode(monkeypatch, env: str) -> None:
    inject(monkeypatch, {"confluence": FakeConnector("confluence", [])})
    assert invoke("run", "--source", "kafka").exit_code == 2
    assert invoke("run", "--mode", "weekly").exit_code == 2


def test_run_refuses_a_chunk_size_the_model_cannot_take(monkeypatch, env: str) -> None:
    inject(monkeypatch, {"confluence": FakeConnector("confluence", [])})
    monkeypatch.setenv("MCP_INGEST_CHUNK_TOKENS", "4000")
    result = invoke("run")
    assert result.exit_code == 2 and "max input" in result.stderr


def test_run_reports_a_model_mismatch_as_exit_2_with_the_reembed_hint(
    monkeypatch, env: str
) -> None:
    inject(monkeypatch, {"confluence": FakeConnector("confluence", [doc("1")])})
    assert invoke("run").exit_code == 0
    monkeypatch.setattr(cli, "provider_factory", lambda settings: provider("another/model"))
    result = invoke("run")
    assert result.exit_code == 2 and "reembed" in result.stderr


def test_status_json_matches_the_contract(monkeypatch, env: str) -> None:
    inject(monkeypatch, {"confluence": FakeConnector("confluence", [doc("1")])})
    invoke("run")
    result = invoke("status", "--json")
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert_valid(payload, operation_id="ingest_status")
    assert payload["sources"][0]["document_count"] == 1
    human = invoke("status")
    assert "confluence" in human.stdout and "failures=0" in human.stdout


def test_sources_json_matches_the_contract(monkeypatch, env: str) -> None:
    result = invoke("sources", "--json")
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert_valid(payload, operation_id="ingest_sources")
    assert {c["source_type"] for c in payload["connectors"]} == {
        "confluence",
        "gitlab",
        "opensearch",
    }
    assert "enabled=False" in invoke("sources").stdout


def test_reembed_json_matches_the_contract(monkeypatch, env: str) -> None:
    inject(monkeypatch, {"confluence": FakeConnector("confluence", [doc("1")])})
    invoke("run")
    result = invoke("reembed", "--model", "fake/model-b", "--json")
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert_valid(payload, operation_id="ingest_reembed")
    assert payload["mode"] == "reembed" and payload["embedding_model"] == "fake/model-b"
    assert {r[0] for r in q(env, "SELECT embedding_model FROM kb.chunks")} == {"fake/model-b"}
    assert invoke("reembed").exit_code == 2  # --model is required


def test_prune_requires_older_than_and_defaults_to_dry_run(monkeypatch, env: str) -> None:
    inject(monkeypatch, {"confluence": FakeConnector("confluence", [doc("1")])})
    invoke("run")
    q(env, "UPDATE kb.documents SET deleted_at = now() - interval '60 days'")

    missing = invoke("prune", "--tombstoned")
    assert missing.exit_code == 2 and "--older-than" in missing.stderr
    assert invoke("prune", "--tombstoned", "--older-than", "soon").exit_code == 2
    assert invoke("prune", "--tombstoned", "--older-than", "0d").exit_code == 2

    dry = invoke("prune", "--tombstoned", "--older-than", "30d", "--json")
    assert dry.exit_code == 0
    payload = json.loads(dry.stdout)
    assert_valid(payload, operation_id="ingest_prune")
    assert payload["dry_run"] is True and payload["documents_deleted"] == 1
    assert scalar(env, "SELECT count(*) FROM kb.documents") == 1  # nothing was deleted

    real = invoke(
        "prune", "--tombstoned", "--older-than", "30", "--no-dry-run", "--source", "confluence"
    )
    assert real.exit_code == 0 and "deleted 1 documents" in real.stdout
    assert scalar(env, "SELECT count(*) FROM kb.documents") == 0


# -- DSN discipline (ADR-0011 A6) --------------------------------------------------------------


def test_db_upgrade_without_any_dsn_is_a_clear_config_error(monkeypatch) -> None:
    for name in ("MCP_INGEST_ADMIN_DSN", "MCP_INGEST_PGVECTOR_DSN"):
        monkeypatch.delenv(name, raising=False)
    result = invoke("db", "upgrade")
    assert result.exit_code == 2
    assert "MCP_INGEST_ADMIN_DSN" in result.stderr and "MCP_INGEST_PGVECTOR_DSN" in result.stderr


@pytest.mark.parametrize(
    "command", [["run"], ["status"], ["reembed", "--model", "m"], ["prune", "--older-than", "1d"]]
)
def test_no_other_command_reads_the_admin_dsn(monkeypatch, command: list[str]) -> None:
    for name in ("MCP_INGEST_ADMIN_DSN", "MCP_INGEST_PGVECTOR_DSN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MCP_INGEST_ADMIN_DSN", "postgresql://admin@127.0.0.1:1/never")
    result = invoke(*command)
    assert result.exit_code == 2
    assert "MCP_INGEST_PGVECTOR_DSN" in result.stderr and "never" not in result.stderr


def test_the_admin_dsn_is_referenced_only_by_migration_dsn() -> None:
    root = Path(mcp_ingest.__file__).parent
    hits = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*.py")
        if "admin_dsn" in path.read_text(encoding="utf-8")
    }
    assert hits == {"settings.py"}
    source = (root / "settings.py").read_text(encoding="utf-8")
    assert source.count("self.admin_dsn") == 1  # only inside migration_dsn()


def test_a_connection_error_never_echoes_the_dsn(monkeypatch) -> None:
    monkeypatch.setenv("MCP_INGEST_PGVECTOR_DSN", "postgresql://u:hunter2@127.0.0.1:1/db")
    result = invoke("status")
    assert result.exit_code == 2 and "hunter2" not in result.stderr
    assert "cannot connect" in result.stderr
