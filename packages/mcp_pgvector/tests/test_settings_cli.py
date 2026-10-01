"""T-062: CLI skeleton (`serve`, `doctor`, `tools-dump`) and the startup refusals (TC-043)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading

import pytest

from mcp_pgvector import cli

DSN_VARS = ("MCP_PGVECTOR_DSN", "MCP_PGVECTOR_DSN_FILE", "MCP_PGVECTOR_EMBEDDING_MODEL")


def _env(monkeypatch: pytest.MonkeyPatch, dsn: str | None, model: str = "fake/hashed-bow") -> None:
    for var in DSN_VARS:
        monkeypatch.delenv(var, raising=False)
    if dsn:
        monkeypatch.setenv("MCP_PGVECTOR_DSN", dsn)
    monkeypatch.setenv("MCP_INGEST_EMBEDDING_MODEL", model)
    monkeypatch.delenv("MCP_ALLOW_UNVERIFIED_CREDENTIALS", raising=False)


def test_help_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "serve" in out and "doctor" in out and "tools-dump" in out


def test_tools_dump_lists_three_tools_without_credentials(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _env(monkeypatch, None)
    assert cli.main(["tools-dump"]) == 0
    assert sorted(json.loads(capsys.readouterr().out)) == [
        "kb_get_document", "kb_list_sources", "kb_semantic_search",
    ]  # fmt: skip


@pytest.mark.parametrize("command", ["doctor", "serve"])
def test_missing_config_exits_2_naming_variable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], command: str
) -> None:
    _env(monkeypatch, None)
    assert cli.main([command]) == 2
    assert "MCP_PGVECTOR_DSN" in capsys.readouterr().err


def test_doctor_ok_for_the_read_only_role(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], ro_dsn: str, seeded_ids
) -> None:
    _env(monkeypatch, ro_dsn)
    assert cli.main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "config: ok" in out and "read-only check: ok" in out
    assert "role: mcp_query_ro" in out and "pgvector:" in out


def test_doctor_fails_for_the_write_capable_dsn(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], rw_dsn: str, seeded_ids
) -> None:
    _env(monkeypatch, rw_dsn)
    assert cli.main(["doctor"]) == 1
    out = capsys.readouterr().out
    assert "FAILED" in out and "INSERT on kb.chunks" in out


def test_TC_043_serve_refuses_the_ingest_rw_dsn(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], rw_dsn: str, seeded_ids
) -> None:
    _env(monkeypatch, rw_dsn)
    assert cli.main(["serve"]) == 2
    err = capsys.readouterr().err
    assert "refusing to serve" in err and "mcp_ingest_rw" in err


def test_TC_043_the_escape_hatch_does_not_bypass_a_write_capable_role(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], rw_dsn: str, seeded_ids
) -> None:
    _env(monkeypatch, rw_dsn)
    monkeypatch.setenv("MCP_ALLOW_UNVERIFIED_CREDENTIALS", "true")
    assert cli.main(["serve"]) == 2  # positive evidence of a write path is never "unverified"
    assert "refusing to serve" in capsys.readouterr().err


def test_serve_refuses_a_model_that_differs_from_the_stored_data(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], ro_dsn: str, seeded_ids
) -> None:
    _env(monkeypatch, ro_dsn, model="BAAI/bge-m3")  # data was embedded by fake/hashed-bow
    monkeypatch.setenv("MCP_ALLOW_UNVERIFIED_CREDENTIALS", "true")
    assert cli.main(["serve"]) == 2
    err = capsys.readouterr().err
    assert "reembed" in err and "BAAI/bge-m3" in err and "fake/hashed-bow" in err


def test_serve_with_an_unreachable_database_refuses_unless_unverified_is_allowed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _env(monkeypatch, "postgresql://mcp_query_ro:s3cr3tpw@127.0.0.1:1/kb")
    assert cli.main(["serve"]) == 2
    err = capsys.readouterr().err
    assert "startup check failed" in err and "refusing to serve" in err
    assert "s3cr3tpw" not in err


def _stdio_session(env: dict[str, str]) -> tuple[dict[int, dict], str]:
    messages = [
        {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                       "clientInfo": {"name": "t", "version": "0"}},
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "prompts/list"},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
         "params": {"name": "kb_list_sources", "arguments": {}}},
    ]  # fmt: skip
    proc = subprocess.Popen(
        [sys.executable, "-m", "mcp_pgvector", "serve"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
    )  # fmt: skip
    watchdog = threading.Timer(60, proc.kill)
    watchdog.start()
    lines: list[dict] = []
    try:
        assert proc.stdin is not None and proc.stdout is not None
        for message in messages:
            proc.stdin.write(json.dumps(message) + "\n")
            proc.stdin.flush()
        while not {2, 3, 4} <= {line.get("id") for line in lines}:
            raw = proc.stdout.readline()
            assert raw, "server exited before answering"
            lines.append(json.loads(raw))
    finally:
        watchdog.cancel()
        proc.stdin.close()
        proc.terminate()
        stderr = proc.stderr.read() if proc.stderr else ""
        proc.wait(timeout=10)
    assert all(line.get("jsonrpc") == "2.0" for line in lines)  # stdout carried only JSON-RPC
    return {line["id"]: line for line in lines if "id" in line}, stderr


def test_serve_over_real_stdio_with_the_read_only_dsn(ro_dsn: str, seeded_ids) -> None:
    env = {k: v for k, v in os.environ.items() if not k.startswith("MCP_")}
    env.update(
        MCP_PGVECTOR_DSN=ro_dsn,
        MCP_INGEST_EMBEDDING_MODEL="fake/hashed-bow",  # matches the seeded data; never loaded
        HF_HUB_OFFLINE="1",
    )
    by_id, stderr = _stdio_session(env)
    assert sorted(t["name"] for t in by_id[2]["result"]["tools"]) == [
        "kb_get_document", "kb_list_sources", "kb_semantic_search",
    ]  # fmt: skip
    assert [p["name"] for p in by_id[3]["result"]["prompts"]] == ["semantic_synthesis"]
    result = by_id[4]["result"]
    assert result["isError"] is False
    assert {i["source_type"] for i in result["structuredContent"]["items"]} == {
        "confluence", "gitlab",
    }  # fmt: skip
    assert "postgresql://" not in stderr
