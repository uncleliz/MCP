"""T-017: settings + CLI skeleton (`serve`, `doctor`, `tools-dump`) for mcp-confluence."""

from __future__ import annotations

import json
import subprocess
import sys
import threading

import pytest
from mcp_common.config import SourceMisconfiguredError, load_settings
from mcp_confluence.settings import Settings

from mcp_confluence import cli

ENV = {
    "MCP_CONFLUENCE_BASE_URL": "https://acme.atlassian.net/wiki/",
    "MCP_CONFLUENCE_EMAIL": "svc@acme.test",
    "MCP_CONFLUENCE_API_TOKEN": "tok-secret-value",
}


def _set_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)


def test_settings_load_from_env_and_strip_trailing_slash(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_env(monkeypatch)
    settings = load_settings(Settings, source="confluence")
    assert settings.base_url == "https://acme.atlassian.net/wiki"
    assert settings.flavor == "cloud"
    assert settings.email == "svc@acme.test"


def test_api_token_never_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_env(monkeypatch)
    settings = load_settings(Settings, source="confluence")
    assert "tok-secret-value" not in repr(settings)
    assert settings.api_token.get_secret_value() == "tok-secret-value"


def test_api_token_file_is_supported(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    _set_env(monkeypatch)
    monkeypatch.delenv("MCP_CONFLUENCE_API_TOKEN")
    token_file = tmp_path / "token"
    token_file.write_text("from-file\n")
    monkeypatch.setenv("MCP_CONFLUENCE_API_TOKEN_FILE", str(token_file))
    assert load_settings(Settings).api_token.get_secret_value() == "from-file"


def test_missing_variable_is_named(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_env(monkeypatch)
    monkeypatch.delenv("MCP_CONFLUENCE_EMAIL")
    with pytest.raises(SourceMisconfiguredError) as exc:
        load_settings(Settings, source="confluence")
    assert "MCP_CONFLUENCE_EMAIL" in exc.value.missing_vars


def test_server_flavor_is_rejected_cloud_only(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_env(monkeypatch)
    monkeypatch.setenv("MCP_CONFLUENCE_FLAVOR", "server")
    with pytest.raises(SourceMisconfiguredError) as exc:
        load_settings(Settings, source="confluence")
    assert exc.value.missing_vars == ["MCP_CONFLUENCE_FLAVOR"]


def test_non_http_base_url_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_env(monkeypatch)
    monkeypatch.setenv("MCP_CONFLUENCE_BASE_URL", "ftp://acme")
    with pytest.raises(SourceMisconfiguredError) as exc:
        load_settings(Settings, source="confluence")
    assert "MCP_CONFLUENCE_BASE_URL" in exc.value.missing_vars


def test_help_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "serve" in out and "doctor" in out and "tools-dump" in out


def test_tools_dump_needs_no_credentials(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for key in ENV:
        monkeypatch.delenv(key, raising=False)
    assert cli.main(["tools-dump"]) == 0
    dumped = json.loads(capsys.readouterr().out)
    assert set(dumped) == {
        "confluence_search_pages",
        "confluence_get_page",
        "confluence_list_spaces",
        "confluence_list_page_children",
    }


def test_doctor_reports_missing_config(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for key in ENV:
        monkeypatch.delenv(key, raising=False)
    assert cli.main(["doctor"]) == 2
    err = capsys.readouterr().err
    assert "MCP_CONFLUENCE_BASE_URL" in err


def test_serve_with_missing_config_exits_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for key in ENV:
        monkeypatch.delenv(key, raising=False)
    assert cli.main(["serve"]) == 2
    assert "MCP_CONFLUENCE_EMAIL" in capsys.readouterr().err


def test_serve_over_real_stdio_lists_tools_and_keeps_stdout_clean(tmp_path) -> None:
    """Spawns `python -m mcp_confluence serve` and speaks JSON-RPC over stdio.

    The credential gate is bypassed with the documented escape hatch because there is no
    network here; the point is the stdio wiring + that stdout carries only JSON-RPC.
    """
    env = {
        **ENV,
        "MCP_ALLOW_UNVERIFIED_CREDENTIALS": "true",
        "PATH": "/usr/bin:/bin",
    }
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
        {"jsonrpc": "2.0", "id": 3, "method": "prompts/list"},
    ]
    proc = subprocess.Popen(
        [sys.executable, "-m", "mcp_confluence", "serve"],
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
        # Like a real client, keep stdin open until every answer has arrived.
        while {2, 3} - {line.get("id") for line in lines}:
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
    tool_names = {t["name"] for t in by_id[2]["result"]["tools"]}
    assert len(tool_names) == 4 and "confluence_search_pages" in tool_names
    assert [p["name"] for p in by_id[3]["result"]["prompts"]] == ["dev_knowledge_lookup"]
    assert "tok-secret-value" not in stderr


# ---- doctor / serve against a mocked Confluence ------------------------------------

BASE = "https://acme.atlassian.net/wiki"


def _mock_identity(router, fixture, sample: str) -> None:
    import httpx

    router.get(f"{BASE}/rest/api/user/current").mock(
        return_value=httpx.Response(200, json=fixture("current_user.json"))
    )
    router.get(f"{BASE}/rest/api/content/search").mock(
        return_value=httpx.Response(200, json=fixture(sample))
    )


def test_doctor_ok_with_readonly_account(
    monkeypatch: pytest.MonkeyPatch, capsys, readonly_respx_router, fixture
) -> None:
    _set_env(monkeypatch)
    monkeypatch.setenv("MCP_CONFLUENCE_BASE_URL", BASE)
    _mock_identity(readonly_respx_router, fixture, "sample_page_readonly.json")
    assert cli.main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "config: ok" in out and "read-only check: ok" in out


def test_doctor_fails_for_writable_account_and_says_why(
    monkeypatch: pytest.MonkeyPatch, capsys, readonly_respx_router, fixture
) -> None:
    _set_env(monkeypatch)
    monkeypatch.setenv("MCP_CONFLUENCE_BASE_URL", BASE)
    _mock_identity(readonly_respx_router, fixture, "sample_page_writable.json")
    assert cli.main(["doctor"]) == 1
    out = capsys.readouterr().out
    assert "FAILED" in out and "create:comment" in out


def test_serve_refuses_when_credential_check_fails(
    monkeypatch: pytest.MonkeyPatch, capsys, readonly_respx_router, fixture
) -> None:
    from mcp_common.logging import uninstall_stdout_guard

    _set_env(monkeypatch)
    monkeypatch.setenv("MCP_CONFLUENCE_BASE_URL", BASE)
    monkeypatch.delenv("MCP_ALLOW_UNVERIFIED_CREDENTIALS", raising=False)
    _mock_identity(readonly_respx_router, fixture, "sample_page_writable.json")
    try:
        assert cli.main(["serve"]) == 2
    finally:
        uninstall_stdout_guard()
    assert "refusing to serve" in capsys.readouterr().err


def test_module_entry_point_runs_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    import runpy

    monkeypatch.setattr(sys, "argv", ["mcp-confluence", "--help"])
    with pytest.raises(SystemExit) as exc:
        runpy.run_module("mcp_confluence", run_name="__main__")
    assert exc.value.code == 0
