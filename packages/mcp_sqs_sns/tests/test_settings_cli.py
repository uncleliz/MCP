"""T-059: settings + CLI skeleton (`serve`, `doctor`, `tools-dump`) for mcp-sqs-sns."""

from __future__ import annotations

import json
import subprocess
import sys
import threading

import pytest
from mcp_common.config import SourceMisconfiguredError, load_settings
from mcp_sqs_sns.client import SqsSnsClient
from mcp_sqs_sns.settings import Settings
from sqs_helpers import Stubs

from mcp_sqs_sns import cli

ENV = {
    "MCP_SQS_SNS_REGION": "ap-southeast-1",
    "MCP_SQS_SNS_AWS_ACCESS_KEY_ID": "AKIDEXAMPLEEXAMPLEXX",
    "MCP_SQS_SNS_AWS_SECRET_ACCESS_KEY": "aws-secret-value-not-real",
}
IDENTITY = {
    "UserId": "AROA:s",
    "Account": "123456789012",
    "Arn": "arn:aws:sts::123456789012:assumed-role/mcp-readonly/s",
}


def _set_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)


def test_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_env(monkeypatch)
    settings = load_settings(Settings, source="sqs-sns")
    assert settings.region == "ap-southeast-1" and settings.endpoint_url is None
    assert "sqs:SendMessage" in settings.simulate_action_list


def test_secret_never_in_repr_and_file_supported(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    _set_env(monkeypatch)
    assert "aws-secret-value" not in repr(load_settings(Settings))
    monkeypatch.delenv("MCP_SQS_SNS_AWS_SECRET_ACCESS_KEY")
    secret = tmp_path / "s"
    secret.write_text("from-file\n")
    monkeypatch.setenv("MCP_SQS_SNS_AWS_SECRET_ACCESS_KEY_FILE", str(secret))
    assert load_settings(Settings).aws_secret_access_key.get_secret_value() == "from-file"


def test_default_credential_chain_when_no_keys_but_half_keys_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_SQS_SNS_REGION", "ap-southeast-1")
    for key in ("MCP_SQS_SNS_AWS_ACCESS_KEY_ID", "MCP_SQS_SNS_AWS_SECRET_ACCESS_KEY"):
        monkeypatch.delenv(key, raising=False)
    assert load_settings(Settings).aws_access_key_id is None
    monkeypatch.setenv("MCP_SQS_SNS_AWS_ACCESS_KEY_ID", "AKIDEXAMPLEEXAMPLEXX")
    with pytest.raises(SourceMisconfiguredError):
        load_settings(Settings, source="sqs-sns")


def test_missing_region_is_named(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_env(monkeypatch)
    monkeypatch.delenv("MCP_SQS_SNS_REGION")
    with pytest.raises(SourceMisconfiguredError) as exc:
        load_settings(Settings, source="sqs-sns")
    assert "MCP_SQS_SNS_REGION" in exc.value.missing_vars


def test_help_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "serve" in out and "doctor" in out and "tools-dump" in out


def test_tools_dump_lists_six_tools_without_credentials(
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
    assert "MCP_SQS_SNS_REGION" in capsys.readouterr().err


def _patch_boto(monkeypatch: pytest.MonkeyPatch, stubs: Stubs) -> None:
    monkeypatch.setattr(
        SqsSnsClient, "_default_factory", lambda self, service: stubs.clients[service]
    )


def _simulate(decision: str) -> dict:
    actions = [*Settings(region="r").simulate_action_list, "sqs:ReceiveMessage"]
    return {
        "EvaluationResults": [
            {"EvalActionName": a, "EvalDecision": decision, "EvalResourceName": "*"}
            for a in actions
        ]
    }


def test_doctor_ok_and_failed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], stubs: Stubs
) -> None:
    _set_env(monkeypatch)
    _patch_boto(monkeypatch, stubs)
    stubs.add("sts", "get_caller_identity", IDENTITY)
    stubs.add("iam", "simulate_principal_policy", _simulate("implicitDeny"))
    assert cli.main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "config: ok" in out and "read-only check: ok" in out
    assert "123456789012" in out and "aws-secret-value" not in out
    stubs.add("sts", "get_caller_identity", IDENTITY)
    stubs.add("iam", "simulate_principal_policy", _simulate("allowed"))
    assert cli.main(["doctor"]) == 1
    assert "FAILED" in capsys.readouterr().out


def test_serve_refuses_a_principal_that_can_write(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], stubs: Stubs
) -> None:
    _set_env(monkeypatch)
    monkeypatch.delenv("MCP_ALLOW_UNVERIFIED_CREDENTIALS", raising=False)
    _patch_boto(monkeypatch, stubs)
    stubs.add("sts", "get_caller_identity", IDENTITY)
    stubs.add("iam", "simulate_principal_policy", _simulate("allowed"))
    assert cli.main(["serve"]) == 2
    assert "refusing to serve" in capsys.readouterr().err


def test_serve_over_real_stdio_lists_six_tools_and_keeps_stdout_clean() -> None:
    env = {
        **ENV, "MCP_ALLOW_UNVERIFIED_CREDENTIALS": "true", "PATH": "/usr/bin:/bin",
        "MCP_SQS_SNS_ENDPOINT_URL": "http://127.0.0.1:9",
    }  # fmt: skip
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
        [sys.executable, "-m", "mcp_sqs_sns", "serve"],
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
    assert sorted(t["name"] for t in by_id[2]["result"]["tools"]) == [
        "sns_get_topic_attributes", "sns_list_subscriptions_by_topic", "sns_list_topics",
        "sqs_get_queue_attributes", "sqs_list_dead_letter_source_queues", "sqs_list_queues",
    ]  # fmt: skip
    assert "aws-secret-value-not-real" not in stderr
