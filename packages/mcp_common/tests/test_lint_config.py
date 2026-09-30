"""T-002: the workspace lint config must ban print() (R2 — stdout is the JSON-RPC
transport channel for stdio MCP servers; any stray print() corrupts the session).

This test shells out to the real `ruff` binary against the repo's root `ruff.toml`
so it fails the moment someone loosens the T20 (flake8-print) rule, not just when
mcp_common's own code regresses.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def _run_ruff_on_snippet(tmp_path: Path, snippet: str) -> subprocess.CompletedProcess[str]:
    offender = tmp_path / "offender.py"
    offender.write_text(snippet, encoding="utf-8")
    ruff_config = str(REPO_ROOT / "ruff.toml")
    return subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--config", ruff_config, str(offender)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_print_call_is_rejected_by_lint(tmp_path: Path) -> None:
    result = _run_ruff_on_snippet(tmp_path, 'print("leaked to stdout")\n')

    assert result.returncode != 0
    assert "T201" in result.stdout


def test_non_print_code_is_accepted_by_lint(tmp_path: Path) -> None:
    result = _run_ruff_on_snippet(tmp_path, "x = 1 + 1\n")

    assert result.returncode == 0
