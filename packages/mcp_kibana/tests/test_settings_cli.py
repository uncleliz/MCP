"""T-038: settings + CLI skeleton (`serve`, `doctor`, `tools-dump`) for mcp-kibana."""

from __future__ import annotations

import json
import subprocess
import sys
import threading

import pytest
from kibana_helpers import API, STATUS_OK, ok
from mcp_common.config import SourceMisconfiguredError, load_settings
from mcp_kibana.settings import Settings

from mcp_kibana import cli

ENV = {
    "MCP_KIBANA_BASE_URL": "https://kibana.example.test:5601/",
    "MCP_KIBANA_USERNAME": "mcp_ro",
    "MCP_KIBANA_PASSWORD": "kibana-secret-value",
}


def _set_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)


def test_settings_strip_trailing_slash_and_default_verify(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_env(monkeypatch)
    settings = load_settings(Settings, source="kibana")
    assert settings.base_url == "https://kibana.example.test:5601" and settings.verify_certs


def test_password_never_in_repr_and_file_supported(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    _set_env(monkeypatch)
    assert "kibana-secret" not in repr(load_settings(Settings))
    monkeypatch.delenv("MCP_KIBANA_PASSWORD")
    secret = tmp_path / "p"
    secret.write_text("from-file\n")
    monkeypatch.setenv("MCP_KIBANA_PASSWORD_FILE", str(secret))
    assert load_settings(Settings).password.get_secret_value() == "from-file"


def test_missing_bad_url_and_half_credentials_are_named(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_env(monkeypatch)
    monkeypatch.delenv("MCP_KIBANA_BASE_URL")
    with pytest.raises(SourceMisconfiguredError) as exc:
        load_settings(Settings, source="kibana")
    assert "MCP_KIBANA_BASE_URL" in exc.value.missing_vars
    monkeypatch.setenv("MCP_KIBANA_BASE_URL", "kibana.example.test")
    with pytest.raises(SourceMisconfiguredError):
        load_settings(Settings, source="kibana")
    monkeypatch.setenv("MCP_KIBANA_BASE_URL", "https://k.example.test")
    monkeypatch.delenv("MCP_KIBANA_USERNAME")
    with pytest.raises(SourceMisconfiguredError):
        load_settings(Settings, source="kibana")


def test_help_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "serve" in out and "doctor" in out and "tools-dump" in out


def test_tools_dump_lists_three_tools_without_credentials(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for key in ENV:
        monkeypatch.delenv(key, raising=False)
    assert cli.main(["tools-dump"]) == 0
    assert len(json.loads(capsys.readouterr().out)) == 3


@pytest.mark.parametrize("command", ["doctor", "serve"])
def test_missing_config_exits_2_naming_variable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], command: str
) -> None:
    for key in ENV:
        monkeypatch.delenv(key, raising=False)
    assert cli.main([command]) == 2
    assert "MCP_KIBANA_BASE_URL" in capsys.readouterr().err


def test_doctor_ok_and_failed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], readonly_respx_router
) -> None:
    _set_env(monkeypatch)
    route = readonly_respx_router.get(f"{API}/status").mock(return_value=ok(STATUS_OK))
    assert cli.main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "config: ok" in out and "read-only check: ok" in out and "kibana-secret" not in out
    route.mock(return_value=ok({"status": {"overall": {"level": "unavailable"}}}))
    assert cli.main(["doctor"]) == 1
    assert "FAILED" in capsys.readouterr().out


def test_serve_refuses_when_startup_check_fails(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], readonly_respx_router
) -> None:
    _set_env(monkeypatch)
    monkeypatch.delenv("MCP_ALLOW_UNVERIFIED_CREDENTIALS", raising=False)
    readonly_respx_router.get(f"{API}/status").mock(return_value=ok({"status": {}}))
    assert cli.main(["serve"]) == 2
    assert "refusing to serve" in capsys.readouterr().err


def test_serve_over_real_stdio_lists_three_tools_and_keeps_stdout_clean() -> None:
    env = {**ENV, "MCP_ALLOW_UNVERIFIED_CREDENTIALS": "true", "PATH": "/usr/bin:/bin"}
    messages = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "t", "version": "0"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]
    proc = subprocess.Popen(
        [sys.executable, "-m", "mcp_kibana", "serve"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    watchdog = threading.Timer(60, proc.kill)
    watchdog.start()
    lines: list[dict] = []
    try:
        assert proc.stdin is not None and proc.stdout is not None
        for message in messages:
            proc.stdin.write(json.dumps(message) + "\n")
            proc.stdin.flush()
        while 2 not in {line.get("id") for line in lines}:
            raw = proc.stdout.readline()
            assert raw, "server exited before answering"
            lines.append(json.loads(raw))
    finally:
        watchdog.cancel()
        proc.stdin.close()
        proc.terminate()
        stderr = proc.stderr.read() if proc.stderr else ""
        proc.wait(timeout=10)
    assert all(line.get("jsonrpc") == "2.0" for line in lines)
    by_id = {line["id"]: line for line in lines if "id" in line}
    assert len(by_id[2]["result"]["tools"]) == 3
    assert "kibana-secret-value" not in stderr
