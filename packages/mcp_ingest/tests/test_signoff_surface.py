"""T-085: the automated half of the Phase 3 sign-off, run as part of the suite.

`scripts/verify_tool_surface.py` checks the platform as a whole: 49 tools == the contract, 0 write
tools on all 9 sources (including the client methods `mcp-ingest` calls), unknown write tools
rejected at the JSON-RPC layer by all 9 servers, 3 prompts, 6 CLI commands, and that the
write-capable ingest credential is in no MCP server's env.
AC: FR-014/AC-001, FR-014/AC-002, FR-015, NFR-001, NFR-002, NFR-005.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "verify_tool_surface.py"


@pytest.fixture(scope="module")
def verify():
    spec = importlib.util.spec_from_file_location("verify_tool_surface", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["verify_tool_surface"] = module
    spec.loader.exec_module(module)
    return module


def test_the_whole_platform_passes_the_cross_cutting_checks(verify) -> None:
    checks = verify.run_all()
    failed = [f"{c.name}: {c.detail}" for c in checks if not c.ok]
    assert failed == []
    names = " ".join(c.name for c in checks)
    for needle in ("62 tools", "read-only surface [mcp_pgvector]", "unknown write tool", "4 prompts",  # noqa: E501
                   "6 mcp-ingest commands", "config-emit", "Claude Desktop config"):  # fmt: skip
        assert needle in names
    assert len(checks) >= 40


def test_the_ingest_operations_are_covered_for_the_three_connector_sources(verify) -> None:
    ops = verify._connector_operations()
    assert set(ops) == {"mcp_confluence", "mcp_gitlab", "mcp_opensearch"}
    assert all(ops.values())


def test_the_hygiene_check_fails_when_a_server_env_carries_the_ingest_credential(
    verify, monkeypatch
) -> None:
    from mcp_common import cli

    registry = {k: dict(v) for k, v in cli._SERVER_REGISTRY.items()}
    registry["redis"]["env_vars"] = [*registry["redis"]["env_vars"], "MCP_INGEST_PGVECTOR_DSN"]
    monkeypatch.setattr(cli, "_SERVER_REGISTRY", registry)
    first = verify.check_credential_hygiene()[0]
    assert not first.ok and "redis:MCP_INGEST_PGVECTOR_DSN" in first.detail


def test_the_hygiene_check_fails_when_a_doc_example_gives_a_server_the_write_dsn(
    verify, monkeypatch, tmp_path: Path
) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "bad.md").write_text(
        '```json\n{"mcpServers": {"pg": {"env": {"MCP_PGVECTOR_DSN": "x", '
        '"MCP_INGEST_PGVECTOR_DSN": "postgresql://mcp_ingest_rw@h/db"}}}}\n```\n'
    )
    monkeypatch.setattr(verify, "ROOT", tmp_path)
    result = verify.check_credential_hygiene()[-1]
    assert not result.ok and "bad.md" in result.detail


def test_the_code_scan_catches_a_server_that_reads_the_ingest_dsn_from_env(
    verify, monkeypatch, tmp_path: Path
) -> None:
    src = tmp_path / "packages" / "mcp_redis" / "src" / "mcp_redis"
    src.mkdir(parents=True)
    (src / "settings.py").write_text('import os\nDSN = os.environ["MCP_INGEST_PGVECTOR_DSN"]\n')
    monkeypatch.setattr(verify, "PACKAGES_DIR", tmp_path / "packages")
    monkeypatch.setattr(verify, "ROOT", tmp_path)
    result = verify.check_credential_hygiene()[1]
    assert not result.ok and "settings.py" in result.detail


def test_main_prints_a_table_and_exits_zero(verify, capsys) -> None:
    assert verify.main([]) == 0
    out = capsys.readouterr().out
    assert "PASS" in out and "FAIL" not in out and "checks passed" in out
    assert verify.main(["--json"]) == 0
