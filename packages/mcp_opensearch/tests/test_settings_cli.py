"""T-033: settings + CLI skeleton (`serve`, `doctor`, `tools-dump`) for mcp-opensearch."""

from __future__ import annotations

import json
import subprocess
import sys
import threading

import pytest
from mcp_common.config import SourceMisconfiguredError, load_settings
from mcp_opensearch.client import OpenSearchClient
from mcp_opensearch.settings import Settings, dsl_enabled_from_env
from os_helpers import AUTHINFO_ADMIN, FakeOpenSearch

from mcp_opensearch import cli

ENV = {
    "MCP_OPENSEARCH_HOSTS": "https://os1.example.test:9200/, https://os2.example.test:9200",
    "MCP_OPENSEARCH_USERNAME": "mcp_ro",
    "MCP_OPENSEARCH_PASSWORD": "os-secret-value",
}


def _set_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("MCP_OPENSEARCH_ALLOW_DSL", raising=False)


def test_settings_hosts_are_normalised_and_defaults_are_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_env(monkeypatch)
    settings = load_settings(Settings, source="opensearch")
    assert settings.host_list == ["https://os1.example.test:9200", "https://os2.example.test:9200"]
    assert settings.verify_certs is True and settings.allow_dsl is False
    assert settings.denied_roles == {"all_access", "security_manager", "admin"}


def test_allow_dsl_flag_is_read_without_any_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ENV:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("MCP_OPENSEARCH_ALLOW_DSL", raising=False)
    assert dsl_enabled_from_env() is False
    monkeypatch.setenv("MCP_OPENSEARCH_ALLOW_DSL", "true")
    assert dsl_enabled_from_env() is True


def test_password_never_in_repr_and_file_supported(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    _set_env(monkeypatch)
    assert "os-secret" not in repr(load_settings(Settings))
    monkeypatch.delenv("MCP_OPENSEARCH_PASSWORD")
    secret = tmp_path / "p"
    secret.write_text("from-file\n")
    monkeypatch.setenv("MCP_OPENSEARCH_PASSWORD_FILE", str(secret))
    assert load_settings(Settings).password.get_secret_value() == "from-file"


def test_missing_bad_hosts_and_half_credentials_are_named(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_env(monkeypatch)
    monkeypatch.delenv("MCP_OPENSEARCH_HOSTS")
    with pytest.raises(SourceMisconfiguredError) as exc:
        load_settings(Settings, source="opensearch")
    assert "MCP_OPENSEARCH_HOSTS" in exc.value.missing_vars
    monkeypatch.setenv("MCP_OPENSEARCH_HOSTS", "os.example.test:9200")
    with pytest.raises(SourceMisconfiguredError):
        load_settings(Settings, source="opensearch")
    monkeypatch.setenv("MCP_OPENSEARCH_HOSTS", "https://os.example.test:9200")
    monkeypatch.delenv("MCP_OPENSEARCH_USERNAME")
    with pytest.raises(SourceMisconfiguredError):
        load_settings(Settings, source="opensearch")


def test_help_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "serve" in out and "doctor" in out and "tools-dump" in out


def test_tools_dump_lists_all_six_contract_tools_without_credentials(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for key in ENV:
        monkeypatch.delenv(key, raising=False)
    assert cli.main(["tools-dump"]) == 0
    assert len(json.loads(capsys.readouterr().out)) == 6


@pytest.mark.parametrize("command", ["doctor", "serve"])
def test_missing_config_exits_2_naming_variable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], command: str
) -> None:
    for key in ENV:
        monkeypatch.delenv(key, raising=False)
    assert cli.main([command]) == 2
    assert "MCP_OPENSEARCH_HOSTS" in capsys.readouterr().err


def _patch_sdk(monkeypatch: pytest.MonkeyPatch, fake: FakeOpenSearch) -> None:
    monkeypatch.setattr(
        OpenSearchClient,
        "_build_sdk_client",
        staticmethod(lambda s, *, enforce_egress=False: fake),
    )


def test_doctor_ok_and_failed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _set_env(monkeypatch)
    fake = FakeOpenSearch()
    _patch_sdk(monkeypatch, fake)
    assert cli.main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "config: ok" in out and "read-only check: ok" in out and "os-secret" not in out
    fake.responses["transport GET /_plugins/_security/authinfo"] = AUTHINFO_ADMIN
    assert cli.main(["doctor"]) == 1
    assert "FAILED" in capsys.readouterr().out


def test_serve_refuses_when_startup_check_fails(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _set_env(monkeypatch)
    monkeypatch.delenv("MCP_ALLOW_UNVERIFIED_CREDENTIALS", raising=False)
    fake = FakeOpenSearch({"transport GET /_plugins/_security/authinfo": AUTHINFO_ADMIN})
    _patch_sdk(monkeypatch, fake)
    assert cli.main(["serve"]) == 2
    assert "refusing to serve" in capsys.readouterr().err


def test_serve_over_real_stdio_lists_five_tools_by_default_and_keeps_stdout_clean() -> None:
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
        [sys.executable, "-m", "mcp_opensearch", "serve"],
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
    names = sorted(t["name"] for t in by_id[2]["result"]["tools"])
    assert len(names) == 5 and "opensearch_search_dsl" not in names  # DSL flag off by default
    assert "os-secret-value" not in stderr
