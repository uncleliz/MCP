"""T-049: settings + CLI skeleton (`serve`, `doctor`, `tools-dump`) for mcp-redis."""

from __future__ import annotations

import json
import subprocess
import sys
import threading

import pytest
from mcp_common.config import SourceMisconfiguredError, load_settings
from mcp_redis.settings import Settings

from mcp_redis import cli

ENV = {
    "MCP_REDIS_URL": "redis://mcp_ro@redis.example.test:6379/0",
    "MCP_REDIS_PASSWORD": "pw-not-real",
}


def _set_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)


def test_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_env(monkeypatch)
    monkeypatch.delenv("MCP_REDIS_KEY_DENY", raising=False)
    settings = load_settings(Settings, source="redis")
    assert settings.url.startswith("redis://mcp_ro@")
    assert "*secret*" in settings.deny_globs and "*.pem" in settings.deny_globs


def test_key_deny_override_and_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_env(monkeypatch)
    monkeypatch.setenv("MCP_REDIS_KEY_DENY", " a* , b* ,")
    assert load_settings(Settings).deny_globs == ["a*", "b*"]
    monkeypatch.setenv("MCP_REDIS_KEY_DENY", "")
    assert load_settings(Settings).deny_globs == []


def test_password_never_in_repr_and_file_supported(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    _set_env(monkeypatch)
    assert "pw-not-real" not in repr(load_settings(Settings))
    monkeypatch.delenv("MCP_REDIS_PASSWORD")
    secret = tmp_path / "p"
    secret.write_text("from-file\n")
    monkeypatch.setenv("MCP_REDIS_PASSWORD_FILE", str(secret))
    assert load_settings(Settings).password.get_secret_value() == "from-file"


def test_missing_and_bad_url_are_named(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_env(monkeypatch)
    monkeypatch.delenv("MCP_REDIS_URL")
    with pytest.raises(SourceMisconfiguredError) as exc:
        load_settings(Settings, source="redis")
    assert "MCP_REDIS_URL" in exc.value.missing_vars
    monkeypatch.setenv("MCP_REDIS_URL", "http://nope")
    with pytest.raises(SourceMisconfiguredError):
        load_settings(Settings, source="redis")


def test_help_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "serve" in out and "doctor" in out and "tools-dump" in out


def test_tools_dump_lists_four_tools_without_credentials(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for key in ENV:
        monkeypatch.delenv(key, raising=False)
    assert cli.main(["tools-dump"]) == 0
    assert len(json.loads(capsys.readouterr().out)) == 4


@pytest.mark.parametrize("command", ["doctor", "serve"])
def test_missing_config_exits_2_naming_variable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], command: str
) -> None:
    monkeypatch.delenv("MCP_REDIS_URL", raising=False)
    assert cli.main([command]) == 2
    assert "MCP_REDIS_URL" in capsys.readouterr().err


def test_doctor_reports_ok_and_failed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], fake_data
) -> None:
    from redis_helpers import FakeRedis

    from mcp_redis import client as client_module

    _set_env(monkeypatch)
    monkeypatch.setattr(
        client_module.RedisClient,
        "_default_factory",
        lambda self, db: FakeRedis(fake_data, db=db),
    )
    assert cli.main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "read-only check: ok" in out and "acl user: mcp_ro" in out and "pw-not-real" not in out
    monkeypatch.setattr(
        client_module.RedisClient,
        "_default_factory",
        lambda self, db: FakeRedis(fake_data, acl_commands="+@all", db=db),
    )
    assert cli.main(["doctor"]) == 1
    assert "FAILED" in capsys.readouterr().out


def test_serve_refuses_a_user_that_can_write(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], fake_data
) -> None:
    """TC-032: ACL startup check gates `serve` (unless MCP_ALLOW_UNVERIFIED_CREDENTIALS)."""
    from redis_helpers import FakeRedis

    from mcp_redis import client as client_module

    _set_env(monkeypatch)
    monkeypatch.delenv("MCP_ALLOW_UNVERIFIED_CREDENTIALS", raising=False)
    monkeypatch.setattr(
        client_module.RedisClient,
        "_default_factory",
        lambda self, db: FakeRedis(fake_data, acl_commands="-@all +get +set", db=db),
    )
    assert cli.main(["serve"]) == 2
    assert "refusing to serve" in capsys.readouterr().err


def test_serve_over_real_stdio_lists_four_tools_and_keeps_stdout_clean() -> None:
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
        [sys.executable, "-m", "mcp_redis", "serve"],
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
    assert len(by_id[2]["result"]["tools"]) == 4
    assert "pw-not-real" not in stderr
