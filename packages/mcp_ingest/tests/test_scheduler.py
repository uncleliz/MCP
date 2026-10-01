"""T-082: scheduler templates, wrapper script and a real job run against a local database.

AC: FR-012/AC-002, NFR-004, R12. The "job ran once for real" check drives the real CLI through
`infra/scheduler/run-ingest.sh` as a subprocess, against the local PostgreSQL and two local HTTP
stubs (a Confluence REST API and an OpenAI-compatible embeddings endpoint, so the real
`mcp_confluence.client` and `HttpEmbeddingProvider` are exercised); only the network peers are
fake. Cadence is still `# THRESHOLD TBD` (PO, Open question 5).
"""

from __future__ import annotations

import json
import os
import plistlib
import re
import subprocess
import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from ingest_helpers import q, scalar
from mcp_ingest.embedding.fake import DeterministicFakeProvider

ROOT = Path(__file__).resolve().parents[3]
SCHEDULER = ROOT / "infra" / "scheduler"
SCRIPT = SCHEDULER / "run-ingest.sh"


# -- static checks ------------------------------------------------------------------------------


def test_the_wrapper_is_valid_bash_and_executable() -> None:
    assert os.access(SCRIPT, os.X_OK)
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)
    text = SCRIPT.read_text()
    assert "mcp_ingest_rw" in text  # the credential rule is documented where it matters
    assert "ADMIN_DSN" in text and "--json" in text


def test_the_wrapper_rejects_an_unknown_mode() -> None:
    result = subprocess.run([str(SCRIPT), "weekly"], capture_output=True, text=True)
    assert result.returncode == 64 and "usage" in result.stderr


def test_crontab_example_has_hourly_incremental_and_a_0300_full_run() -> None:
    lines = [
        line
        for line in (SCHEDULER / "crontab.example").read_text().splitlines()
        if line.strip() and not line.startswith("#") and "=" not in line.split()[0]
    ]
    assert len(lines) == 2
    incremental, full = lines
    assert re.match(r"^0 0-2,4-23 \* \* \*\s+\S+run-ingest\.sh incremental$", incremental)
    assert re.match(r"^0 3 \* \* \*\s+\S+run-ingest\.sh full$", full)
    assert "THRESHOLD TBD" in (SCHEDULER / "crontab.example").read_text()


def test_launchd_templates_parse_and_cover_the_same_cadence() -> None:
    inc = plistlib.loads((SCHEDULER / "com.mcp.ingest.plist").read_bytes())
    full = plistlib.loads((SCHEDULER / "com.mcp.ingest.full.plist").read_bytes())
    assert inc["Label"] == "com.mcp.ingest" and full["Label"] == "com.mcp.ingest.full"
    assert inc["ProgramArguments"][1:] == ["incremental"] and full["ProgramArguments"][1:] == [
        "full"
    ]
    hours = {entry["Hour"] for entry in inc["StartCalendarInterval"]}
    assert hours == set(range(24)) - {3}
    assert full["StartCalendarInterval"] == {"Minute": 0, "Hour": 3}
    for plist in (inc, full):
        assert not any(
            "DSN" in key or "TOKEN" in key for key in plist.get("EnvironmentVariables", {})
        )


def test_templates_never_carry_credentials() -> None:
    for path in SCHEDULER.iterdir():
        text = path.read_text()
        assert not re.search(r"postgres(ql)?://\w+:[^@\s]+@", text), path.name
        assert not re.search(r"(?i)(token|password|secret)\s*=\s*[^\s$<]{8,}", text), path.name


def test_runbook_covers_every_exit_code_and_the_operational_rules() -> None:
    text = (SCHEDULER / "runbook-ingest.md").read_text()
    for needle in (
        "**0** success", "**1** partial", "**2** failed", "**3** lock",
        "--retry-failed", "maintenance_work_mem", "mcp_ingest_rw", "prune", "THRESHOLD TBD",
        "cadence", "blocked_by_policy",
    ):  # fmt: skip
        assert needle.lower() in text.lower(), needle
    assert re.search(r"Không bao giờ.*mcp_ingest_rw", text, re.S)


# -- the job really runs ------------------------------------------------------------------------


class _Quiet(BaseHTTPRequestHandler):
    def log_message(self, *args: Any) -> None:  # silence
        return


def _confluence_handler(pages: list[dict[str, Any]]) -> type[BaseHTTPRequestHandler]:
    class Handler(_Quiet):
        def do_GET(self) -> None:  # noqa: N802
            if self.path.startswith("/wiki/rest/api/content/search"):
                body = {"results": pages, "_links": {}}
            else:
                self.send_response(404)
                self.end_headers()
                return
            data = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return Handler


def _embeddings_handler() -> type[BaseHTTPRequestHandler]:
    fake = DeterministicFakeProvider(dimensions=1024, normalize=False)

    class Handler(_Quiet):
        def do_POST(self) -> None:  # noqa: N802
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            vectors = fake.embed_documents(payload["input"])
            body = {"data": [{"index": i, "embedding": v} for i, v in enumerate(vectors)]}
            data = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return Handler


def _serve(handler: type[BaseHTTPRequestHandler]) -> Iterator[ThreadingHTTPServer]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


def _page(page_id: str, title: str, text: str) -> dict[str, Any]:
    return {
        "id": page_id, "type": "page", "title": title,
        "space": {"key": "PAY", "type": "global"},
        "version": {"number": 1, "when": "2026-09-30T10:00:00.000Z", "by": {"displayName": "An"}},
        "body": {"storage": {"value": f"<h1>{title}</h1><p>{text}</p>"}},
        "ancestors": [],
        "restrictions": {"read": {"restrictions": {"user": {"results": []}, "group": {"results": []}}}},  # noqa: E501
        "_links": {"webui": f"/spaces/PAY/pages/{page_id}/x", "base": "https://wiki.example.com/wiki"},
    }  # fmt: skip


@pytest.fixture
def confluence_stub() -> Iterator[ThreadingHTTPServer]:
    pages = [
        _page("1", "Payment retry policy", "Retry three times with exponential backoff."),
        _page("2", "Kafka lag runbook", "Check consumer group lag and rebalance partitions."),
    ]
    yield from _serve(_confluence_handler(pages))


@pytest.fixture
def embeddings_stub() -> Iterator[ThreadingHTTPServer]:
    yield from _serve(_embeddings_handler())


def job_env(
    tmp_path: Path, rw_dsn: str, confluence: ThreadingHTTPServer, embeddings: ThreadingHTTPServer
) -> dict[str, str]:
    env_file = tmp_path / "ingest.env"
    env_file.write_text(
        "\n".join(
            [
                f"MCP_INGEST_PGVECTOR_DSN={rw_dsn}",
                f"MCP_CONFLUENCE_BASE_URL=http://127.0.0.1:{confluence.server_port}/wiki",
                "MCP_CONFLUENCE_EMAIL=svc@example.test",
                "MCP_CONFLUENCE_API_TOKEN=not-a-real-token",
                "MCP_INGEST_CONFLUENCE_TEAM_SPACES=PAY",
                "MCP_INGEST_EMBEDDING_PROVIDER=http",
                f"MCP_INGEST_EMBEDDING_URL=http://127.0.0.1:{embeddings.server_port}/v1",
                "MCP_INGEST_EMBEDDING_MODEL=fake/http-model",
                "MCP_INGEST_EMBEDDING_DIMENSIONS=1024",
                "MCP_INGEST_CHUNK_TOKENS=64",
                "MCP_INGEST_CHUNK_OVERLAP=8",
            ]
        )
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith(("MCP_", "PYTHON"))}
    env.update(
        MCP_INGEST_ENV_FILE=str(env_file),
        MCP_INGEST_LOG_DIR=str(tmp_path / "logs"),
        MCP_INGEST_BIN=f"{sys.executable} -m mcp_ingest",
        HOME=str(tmp_path),
    )
    return env


def test_FR_012_AC_002_the_scheduled_job_runs_for_real_and_records_ingest_runs(
    tmp_path: Path, rw_dsn: str, confluence_stub, embeddings_stub
) -> None:
    env = job_env(tmp_path, rw_dsn, confluence_stub, embeddings_stub)

    result = subprocess.run(
        [str(SCRIPT), "incremental"], env=env, capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 0, result.stderr

    logs = tmp_path / "logs"
    (report_file,) = logs.glob("run-incremental-*.json")
    report = json.loads(report_file.read_text())
    assert report["status"] == "success" and report["exit_code"] == 0
    by_source = {s["source_type"]: s for s in report["sources"]}
    assert by_source["confluence"]["documents_upserted"] == 2
    # GitLab/OpenSearch are not configured here: neutral, but their reason is reported
    assert (
        by_source["gitlab"]["status"] == "skipped"
        and by_source["gitlab"]["errors"][0]["stage"] == "config"
    )
    assert by_source["opensearch"]["status"] == "skipped"

    assert (logs / "last-exit-code").read_text().strip() == "0"
    history = [json.loads(line) for line in (logs / "history.jsonl").read_text().splitlines()]
    assert history[0]["mode"] == "incremental" and history[0]["exit_code"] == 0
    (log_file,) = logs.glob("run-incremental-*.log")
    assert any(
        json.loads(line)["message"].startswith("source confluence")
        for line in log_file.read_text().splitlines()
        if line.startswith("{")
    )

    # ingest_runs: one row per source that ran; the data is really there with the http model
    assert q(rw_dsn, "SELECT source_type, status, documents_seen FROM kb.ingest_runs") == [
        ("confluence", "success", 2)
    ]
    assert scalar(rw_dsn, "SELECT count(*) FROM kb.documents") == 2
    assert {r[0] for r in q(rw_dsn, "SELECT DISTINCT embedding_model FROM kb.chunks")} == {
        "fake/http-model"
    }
    assert scalar(
        rw_dsn,
        "SELECT last_success_at IS NOT NULL FROM kb.ingest_source_state WHERE source_type='confluence'",  # noqa: E501
    )

    # the nightly full run on top of it: nothing duplicated, nothing tombstoned, exit 0
    full = subprocess.run(
        [str(SCRIPT), "full"], env=env, capture_output=True, text=True, timeout=120
    )
    assert full.returncode == 0, full.stderr
    assert scalar(rw_dsn, "SELECT count(*) FROM kb.documents WHERE deleted_at IS NULL") == 2
    assert scalar(rw_dsn, "SELECT count(*) FROM kb.ingest_runs") == 2

    status = subprocess.run(
        [sys.executable, "-m", "mcp_ingest", "status", "--json"],
        env={**env, "MCP_INGEST_PGVECTOR_DSN": rw_dsn},
        capture_output=True,
        text=True,
    )
    assert status.returncode == 0, status.stderr
    rows = {r["source_type"]: r for r in json.loads(status.stdout)["sources"]}
    assert rows["confluence"]["document_count"] == 2 and rows["confluence"]["staleness_hours"] < 1


def test_the_wrapper_passes_through_a_failing_exit_code_and_explains_it(
    tmp_path: Path, rw_dsn: str, confluence_stub, embeddings_stub
) -> None:
    env = job_env(tmp_path, rw_dsn, confluence_stub, embeddings_stub)
    env["MCP_INGEST_BIN"] = f"{sys.executable} -c 'import sys; sys.exit(int(sys.argv[-1]))' 1"
    # `$bin run --source all ...` appends arguments; the last one is --json, so use a tiny shim
    shim = tmp_path / "shim.sh"
    shim.write_text('#!/usr/bin/env bash\nexit "${SHIM_EXIT:-0}"\n')
    shim.chmod(0o755)
    env["MCP_INGEST_BIN"] = str(shim)
    for code, word in ((1, "PARTIAL"), (2, "FAILED"), (3, "lock"), (9, "unexpected")):
        env["SHIM_EXIT"] = str(code)
        result = subprocess.run([str(SCRIPT), "full"], env=env, capture_output=True, text=True)
        assert result.returncode == code and word.lower() in result.stderr.lower()
        assert (tmp_path / "logs" / "last-exit-code").read_text().strip() == str(code)


def test_every_ingest_setting_is_documented_in_env_example() -> None:
    from mcp_ingest.settings import Settings

    text = (ROOT / ".env.example").read_text()
    missing = [
        f"MCP_INGEST_{name.upper()}"
        for name in Settings.model_fields
        if f"MCP_INGEST_{name.upper()}" not in text
    ]
    assert missing == []
