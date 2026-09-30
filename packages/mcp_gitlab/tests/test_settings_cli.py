"""T-023: settings + CLI skeleton (`serve`, `doctor`, `tools-dump`) for mcp-gitlab."""

from __future__ import annotations

import json
import subprocess
import sys
import threading

import pytest
from gitlab_helpers import json_response
from mcp_common.config import SourceMisconfiguredError, load_settings
from mcp_gitlab.settings import Settings

from mcp_gitlab import cli

ENV = {
    "MCP_GITLAB_BASE_URL": "https://gitlab.example.test/",
    "MCP_GITLAB_PRIVATE_TOKEN": "glpat-secret-value-000000",
}


def _set_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)


def test_settings_defaults_and_trailing_slash(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_env(monkeypatch)
    monkeypatch.delenv("MCP_GITLAB_PATH_DENY", raising=False)
    settings = load_settings(Settings, source="gitlab")
    assert settings.base_url == "https://gitlab.example.test"
    assert ".env" in ",".join(settings.deny_globs) and "*.pem" in settings.deny_globs


def test_path_deny_override(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_env(monkeypatch)
    monkeypatch.setenv("MCP_GITLAB_PATH_DENY", " *.key , secrets/* ,")
    assert load_settings(Settings).deny_globs == ["*.key", "secrets/*"]


def test_token_never_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_env(monkeypatch)
    assert "glpat-secret" not in repr(load_settings(Settings))


def test_private_token_file_supported(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    _set_env(monkeypatch)
    monkeypatch.delenv("MCP_GITLAB_PRIVATE_TOKEN")
    token = tmp_path / "t"
    token.write_text("from-file\n")
    monkeypatch.setenv("MCP_GITLAB_PRIVATE_TOKEN_FILE", str(token))
    assert load_settings(Settings).private_token.get_secret_value() == "from-file"


def test_missing_variable_is_named(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_env(monkeypatch)
    monkeypatch.delenv("MCP_GITLAB_PRIVATE_TOKEN")
    with pytest.raises(SourceMisconfiguredError) as exc:
        load_settings(Settings, source="gitlab")
    assert "MCP_GITLAB_PRIVATE_TOKEN" in exc.value.missing_vars


def test_bad_base_url_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_env(monkeypatch)
    monkeypatch.setenv("MCP_GITLAB_BASE_URL", "gitlab.example.test")
    with pytest.raises(SourceMisconfiguredError) as exc:
        load_settings(Settings, source="gitlab")
    assert "MCP_GITLAB_BASE_URL" in exc.value.missing_vars


def test_help_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "serve" in out and "doctor" in out and "tools-dump" in out


def test_tools_dump_lists_eleven_tools_without_credentials(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for key in ENV:
        monkeypatch.delenv(key, raising=False)
    assert cli.main(["tools-dump"]) == 0
    assert len(json.loads(capsys.readouterr().out)) == 11


@pytest.mark.parametrize("command", ["doctor", "serve"])
def test_missing_config_exits_2_naming_variable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], command: str
) -> None:
    for key in ENV:
        monkeypatch.delenv(key, raising=False)
    assert cli.main([command]) == 2
    assert "MCP_GITLAB_BASE_URL" in capsys.readouterr().err


def test_serve_over_real_stdio_lists_eleven_tools_and_keeps_stdout_clean() -> None:
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
        [sys.executable, "-m", "mcp_gitlab", "serve"],
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
    assert len(by_id[2]["result"]["tools"]) == 11
    assert "glpat-secret-value" not in stderr


# ---- doctor / serve against a mocked GitLab ----------------------------------------

PAT_SELF = "https://gitlab.example.test/api/v4/personal_access_tokens/self"


def test_doctor_ok_with_read_scopes(
    monkeypatch: pytest.MonkeyPatch, capsys, readonly_respx_router
) -> None:
    _set_env(monkeypatch)
    readonly_respx_router.get(PAT_SELF).mock(return_value=json_response("pat_self_ok.json"))
    assert cli.main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "config: ok" in out and "read-only check: ok" in out


def test_doctor_fails_for_api_scope_and_says_why(
    monkeypatch: pytest.MonkeyPatch, capsys, readonly_respx_router
) -> None:
    _set_env(monkeypatch)
    readonly_respx_router.get(PAT_SELF).mock(return_value=json_response("pat_self_api_scope.json"))
    assert cli.main(["doctor"]) == 1
    out = capsys.readouterr().out
    assert "FAILED" in out and "api" in out


def test_serve_refuses_token_with_api_scope(
    monkeypatch: pytest.MonkeyPatch, capsys, readonly_respx_router
) -> None:
    from mcp_common.logging import uninstall_stdout_guard

    _set_env(monkeypatch)
    monkeypatch.delenv("MCP_ALLOW_UNVERIFIED_CREDENTIALS", raising=False)
    readonly_respx_router.get(PAT_SELF).mock(return_value=json_response("pat_self_api_scope.json"))
    try:
        assert cli.main(["serve"]) == 2
    finally:
        uninstall_stdout_guard()
    assert "refusing to serve" in capsys.readouterr().err


def test_module_entry_point_runs_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    import runpy

    monkeypatch.setattr(sys, "argv", ["mcp-gitlab", "--help"])
    with pytest.raises(SystemExit) as exc:
        runpy.run_module("mcp_gitlab", run_name="__main__")
    assert exc.value.code == 0
