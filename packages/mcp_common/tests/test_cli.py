"""T-016 tests: `mcp-common config-emit` (NFR-005)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from mcp_common.cli import build_config_emit_payload, known_servers, main

ENV_EXAMPLE = Path(__file__).resolve().parents[3] / ".env.example"


def test_nfr005_config_emit_prints_valid_json(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["config-emit", "--server", "confluence"]) == 0
    out = capsys.readouterr()
    payload = json.loads(out.out)
    entry = payload["mcpServers"]["confluence"]
    assert entry["command"] == "uv"
    assert "MCP_CONFLUENCE_BASE_URL" in entry["env"]
    assert out.err == ""


def test_nfr005_config_emit_never_emits_real_secrets() -> None:
    for server in known_servers():
        env = build_config_emit_payload(server)["mcpServers"][server]["env"]
        assert set(env.values()) == {"<dien-gia-tri-that-vao-day>"}


def test_nfr005_all_nine_servers_registered() -> None:
    assert len(known_servers()) == 9


def test_nfr005_registry_env_vars_exist_in_env_example() -> None:
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    declared = set(re.findall(r"^#?\s*(MCP_[A-Z0-9_]+)=", text, flags=re.M))
    for server in known_servers():
        for var in build_config_emit_payload(server)["mcpServers"][server]["env"]:
            assert var in declared, f"{var} ({server}) missing from .env.example"


def test_nfr005_unknown_server_raises_with_known_list() -> None:
    with pytest.raises(ValueError, match="Known servers"):
        build_config_emit_payload("nope")


def test_nfr005_config_emit_unknown_server_rejected_by_argparse() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["config-emit", "--server", "nope"])
    assert exc.value.code == 2


def test_nfr005_config_emit_handler_reports_error(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    import mcp_common.cli as cli

    def boom(_server: str) -> dict[str, object]:
        raise ValueError("Unknown server 'x'")

    monkeypatch.setattr(cli, "build_config_emit_payload", boom)
    assert cli.main(["config-emit", "--server", "redis"]) == 1
    assert "error:" in capsys.readouterr().err
