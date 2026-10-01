"""E2E harness: real stdio MCP server subprocesses driven by the official MCP client.

There is no browser/UI in this project; "E2E" means JSON-RPC over stdio against the installed
console entry points (`python -m mcp_<pkg>`), with local stubs standing in for remote sources
(HTTP stub server, moto server, local Postgres+pgvector, local OpenAI-compatible embedding stub).
Nothing here talks to the network beyond 127.0.0.1.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import threading
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parent.parent
PACKAGES = ROOT / "packages"

# Re-export the throw-away Postgres+pgvector fixtures of packages/conftest.py.
_spec = importlib.util.spec_from_file_location("_pkg_conftest", PACKAGES / "conftest.py")
assert _spec and _spec.loader
_pkg_conftest = importlib.util.module_from_spec(_spec)
sys.modules["_pkg_conftest"] = _pkg_conftest
_spec.loader.exec_module(_pkg_conftest)
pg_server = _pkg_conftest.pg_server
pg_database_factory = _pkg_conftest.pg_database_factory
PgServer = _pkg_conftest.PgServer

# server key -> (python module, minimal env that makes the process start; tools/list needs none)
SERVERS: dict[str, tuple[str, dict[str, str]]] = {
    "confluence": (
        "mcp_confluence",
        {
            "MCP_CONFLUENCE_BASE_URL": "http://127.0.0.1:9",
            "MCP_CONFLUENCE_EMAIL": "a@b.c",
            "MCP_CONFLUENCE_API_TOKEN": "x",
        },
    ),
    "gitlab": (
        "mcp_gitlab",
        {"MCP_GITLAB_BASE_URL": "http://127.0.0.1:9", "MCP_GITLAB_PRIVATE_TOKEN": "x"},
    ),
    "opensearch": ("mcp_opensearch", {"MCP_OPENSEARCH_HOSTS": "http://127.0.0.1:9"}),
    "kibana": ("mcp_kibana", {"MCP_KIBANA_BASE_URL": "http://127.0.0.1:9"}),
    "cloudwatch": ("mcp_cloudwatch", {"MCP_CLOUDWATCH_REGION": "us-east-1"}),
    "kafka": ("mcp_kafka", {"MCP_KAFKA_BOOTSTRAP_SERVERS": "127.0.0.1:9"}),
    "redis": ("mcp_redis", {"MCP_REDIS_URL": "redis://127.0.0.1:9/0"}),
    "sqs_sns": ("mcp_sqs_sns", {"MCP_SQS_SNS_REGION": "us-east-1"}),
    "pgvector": ("mcp_pgvector", {"MCP_PGVECTOR_DSN": "postgresql://x@127.0.0.1:9/x"}),
}


def server_env(key: str, extra: dict[str, str] | None = None) -> dict[str, str]:
    base = {
        k: v
        for k, v in os.environ.items()
        if k.startswith(("PATH", "HOME", "LANG", "VIRTUAL_ENV", "PYTHON"))
    }
    base.update(
        {
            "MCP_ALLOW_UNVERIFIED_CREDENTIALS": "true",
            "AWS_ACCESS_KEY_ID": "test",
            "AWS_SECRET_ACCESS_KEY": "test",
            "AWS_DEFAULT_REGION": "us-east-1",
        }
    )
    base.update(SERVERS[key][1])
    base.update(extra or {})
    return base


@asynccontextmanager
async def open_server(
    key: str, extra_env: dict[str, str] | None = None
) -> AsyncIterator[ClientSession]:
    """Spawn `python -m <module>` (stdio) and yield an initialized MCP client session."""
    module = SERVERS[key][0]
    params = StdioServerParameters(
        command=sys.executable, args=["-m", module], env=server_env(key, extra_env), cwd=str(ROOT)
    )
    with open(os.devnull, "w") as errlog:  # server logs go to stderr by design
        async with stdio_client(params, errlog=errlog) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session


def result_text(result: Any) -> str:
    return "\n".join(c.text for c in result.content if getattr(c, "type", "") == "text")


def result_payload(result: Any) -> dict[str, Any]:
    """The tool's structured result (ToolResult/ErrorEnvelope dict) if present, else parsed text."""
    if getattr(result, "structuredContent", None):
        return dict(result.structuredContent)
    text = result_text(result)
    try:
        return dict(json.loads(text))
    except (ValueError, TypeError):
        return {"_text": text}


# -- local HTTP stub standing in for Confluence / GitLab / Kibana / OpenSearch / embeddings -------


class StubHttp:
    """Tiny threaded HTTP server; `routes(method, path, query, body) -> (status, json|str)`."""

    def __init__(self, routes: Callable[[str, str, dict[str, list[str]], bytes], tuple[int, Any]]):
        self.requests: list[tuple[str, str]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def _handle(self) -> None:
                parsed = urlparse(self.path)
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                outer.requests.append((self.command, parsed.path))
                status, payload = routes(self.command, parsed.path, parse_qs(parsed.query), body)
                raw = payload.encode() if isinstance(payload, str) else json.dumps(payload).encode()
                self.send_response(status)
                self.send_header(
                    "Content-Type", "text/plain" if isinstance(payload, str) else "application/json"
                )
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            do_GET = do_POST = do_PUT = do_DELETE = do_HEAD = _handle  # noqa: N815

            def log_message(self, *args: Any) -> None:
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


def load_fixture(package: str, relative: str) -> Any:
    path = PACKAGES / package / "tests" / "fixtures" / relative
    text = path.read_text()
    return json.loads(text) if path.suffix == ".json" else text
