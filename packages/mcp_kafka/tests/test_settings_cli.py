"""T-046: settings + CLI skeleton (`serve`, `doctor`, `tools-dump`) for mcp-kafka."""

from __future__ import annotations

import json
import subprocess
import sys
import threading

import pytest
from confluent_kafka.admin import AclOperation
from kafka_helpers import FakeAdmin, FakeCluster, FakeConsumer, acl
from mcp_common.config import SourceMisconfiguredError, load_settings
from mcp_kafka.settings import Settings

from mcp_kafka import cli
from mcp_kafka import client as client_module

ENV = {
    "MCP_KAFKA_BOOTSTRAP_SERVERS": "kafka.example.test:9093, kafka2.example.test:9093",
    "MCP_KAFKA_SECURITY_PROTOCOL": "SASL_SSL",
    "MCP_KAFKA_SASL_USERNAME": "mcp_ro",
    "MCP_KAFKA_SASL_PASSWORD": "kafka-secret-value",
}


def _set_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)


def test_settings_parse_and_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_env(monkeypatch)
    settings = load_settings(Settings, source="kafka")
    assert settings.bootstrap_servers == "kafka.example.test:9093,kafka2.example.test:9093"
    assert settings.first_host == "kafka.example.test:9093"
    assert settings.sasl_mechanism == "SCRAM-SHA-512" and settings.client_id.startswith("mcp-kafka")


def test_plaintext_needs_no_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    for key in ENV:
        if key != "MCP_KAFKA_BOOTSTRAP_SERVERS":
            monkeypatch.delenv(key, raising=False)
    assert load_settings(Settings).security_protocol == "PLAINTEXT"


def test_secret_never_in_repr_and_file_supported(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    _set_env(monkeypatch)
    assert "kafka-secret" not in repr(load_settings(Settings))
    monkeypatch.delenv("MCP_KAFKA_SASL_PASSWORD")
    secret = tmp_path / "p"
    secret.write_text("from-file\n")
    monkeypatch.setenv("MCP_KAFKA_SASL_PASSWORD_FILE", str(secret))
    assert load_settings(Settings).sasl_password.get_secret_value() == "from-file"


def test_missing_bad_bootstrap_and_sasl_without_credentials_are_named(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_env(monkeypatch)
    monkeypatch.delenv("MCP_KAFKA_BOOTSTRAP_SERVERS")
    with pytest.raises(SourceMisconfiguredError) as exc:
        load_settings(Settings, source="kafka")
    assert "MCP_KAFKA_BOOTSTRAP_SERVERS" in exc.value.missing_vars
    monkeypatch.setenv("MCP_KAFKA_BOOTSTRAP_SERVERS", "kafka://host:9092")
    with pytest.raises(SourceMisconfiguredError):
        load_settings(Settings, source="kafka")
    monkeypatch.setenv("MCP_KAFKA_BOOTSTRAP_SERVERS", "host:9092")
    monkeypatch.delenv("MCP_KAFKA_SASL_USERNAME")
    with pytest.raises(SourceMisconfiguredError):
        load_settings(Settings, source="kafka")


def test_help_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "serve" in out and "doctor" in out and "tools-dump" in out


def test_tools_dump_lists_five_tools_without_credentials(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for key in ENV:
        monkeypatch.delenv(key, raising=False)
    assert cli.main(["tools-dump"]) == 0
    assert len(json.loads(capsys.readouterr().out)) == 5


@pytest.mark.parametrize("command", ["doctor", "serve"])
def test_missing_config_exits_2_naming_variable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], command: str
) -> None:
    for key in ENV:
        monkeypatch.delenv(key, raising=False)
    assert cli.main([command]) == 2
    assert "MCP_KAFKA_BOOTSTRAP_SERVERS" in capsys.readouterr().err


def _patch_clients(monkeypatch: pytest.MonkeyPatch, cluster: FakeCluster) -> None:
    monkeypatch.setattr(client_module, "AdminClient", lambda conf: FakeAdmin(conf, cluster))
    monkeypatch.setattr(client_module, "Consumer", lambda conf: FakeConsumer(conf, cluster))


def test_doctor_ok_and_failed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], cluster: FakeCluster
) -> None:
    _set_env(monkeypatch)
    _patch_clients(monkeypatch, cluster)
    cluster.acls = [acl(AclOperation.READ)]
    assert cli.main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "config: ok" in out and "read-only check: ok" in out and "kafka-secret" not in out
    cluster.acls = [acl(AclOperation.CREATE)]
    assert cli.main(["doctor"]) == 1
    assert "FAILED" in capsys.readouterr().out


def test_serve_refuses_a_principal_with_write_acls(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], cluster: FakeCluster
) -> None:
    _set_env(monkeypatch)
    monkeypatch.delenv("MCP_ALLOW_UNVERIFIED_CREDENTIALS", raising=False)
    _patch_clients(monkeypatch, cluster)
    cluster.acls = [acl(AclOperation.WRITE)]
    assert cli.main(["serve"]) == 2
    assert "refusing to serve" in capsys.readouterr().err


def test_serve_over_real_stdio_lists_five_tools_and_keeps_stdout_clean() -> None:
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
        [sys.executable, "-m", "mcp_kafka", "serve"],
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
    assert len(by_id[2]["result"]["tools"]) == 5
    assert "kafka-secret-value" not in stderr
