"""E2E read-only surface over real stdio (FR-014, FR-001/AC-003): TC-005, TC-054 (live surface),
TC-056, TC-069 (stdout framing).

Observed SDK behaviour (mcp 1.30): a `tools/call` for a tool name that is not registered is
rejected by the SDK layer as `CallToolResult(isError=true, "Unknown tool: <name>")`, not as a
JSON-RPC error object. Either way the call is refused before any server code runs; the tests
assert that (no ErrorEnvelope, no structured content, zero upstream requests).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys

import pytest
from harness import ROOT, SERVERS, open_server, result_text, server_env

FABRICATED = {
    "confluence": "confluence_create_page",
    "gitlab": "gitlab_merge_merge_request",
    "opensearch": "opensearch_delete_index",
    "kibana": "kibana_delete_saved_object",
    "cloudwatch": "cloudwatch_put_metric_alarm",
    "kafka": "kafka_produce_message",
    "redis": "redis_set_key",
    "sqs_sns": "sns_publish_message",
    "pgvector": "kb_delete_document",
}
WRITE_VERB = re.compile(
    r"(create|update|delete|put|post|publish|send|write|set_|produce|merge_merge|purge)"
)


async def test_TC_005_confluence_unknown_write_tool_refused(stub_http):
    stub = stub_http(lambda m, p, q, b: (404, {}))
    async with open_server("confluence", {"MCP_CONFLUENCE_BASE_URL": stub.url}) as s:
        names = {t.name for t in (await s.list_tools()).tools}
        assert "confluence_create_page" not in names
        res = await s.call_tool("confluence_create_page", {"title": "x"})
    assert res.isError and "Unknown tool" in result_text(res)
    assert res.structuredContent is None
    # only the read-only startup credential probe may reach Confluence; no write, no content call
    assert all(m == "GET" and p.endswith("/user/current") for m, p in stub.requests), stub.requests


@pytest.mark.parametrize("key", sorted(SERVERS))
async def test_TC_056_unknown_write_tool_refused_on_every_server(key):
    async with open_server(key) as s:
        names = {t.name for t in (await s.list_tools()).tools}
        assert FABRICATED[key] not in names
        res = await s.call_tool(FABRICATED[key], {})
    assert res.isError and "Unknown tool" in result_text(res)
    assert res.structuredContent is None  # protocol-layer refusal, not an ErrorEnvelope


async def test_TC_054_live_tool_surface_is_48_and_readonly():
    total = 0
    for key in sorted(SERVERS):
        async with open_server(key) as s:
            tools = (await s.list_tools()).tools
        total += len(tools)
        bad = [t.name for t in tools if WRITE_VERB.search(t.name)]
        assert not bad, f"{key}: write-looking tools {bad}"
    assert total == 48


async def test_TC_054_opensearch_dsl_toggle_adds_exactly_one_tool():
    async with open_server("opensearch") as s:
        default = {t.name for t in (await s.list_tools()).tools}
    async with open_server("opensearch", {"MCP_OPENSEARCH_ALLOW_DSL": "true"}) as s:
        with_dsl = {t.name for t in (await s.list_tools()).tools}
    assert "opensearch_search_dsl" not in default
    assert (
        with_dsl - default == {"opensearch_search_dsl"} and len(with_dsl) == 6 and len(default) == 5
    )


@pytest.mark.parametrize("key", ["confluence", "kibana", "sqs_sns"])
def test_TC_069_stdout_carries_only_jsonrpc_frames(key):
    """Raw subprocess: every byte on stdout must be a JSON-RPC frame; logs go to stderr."""
    module = SERVERS[key][0]
    frames = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "t", "version": "0"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]
    proc = subprocess.Popen(
        [sys.executable, "-m", module],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=ROOT,
        env=server_env(key),
    )
    assert proc.stdin and proc.stdout
    lines: list[str] = []
    try:
        # Keep stdin open until both replies arrived: closing it early makes the server exit
        # (EOF) before answering tools/list, which is a race in the test, not in the server.
        for frame in frames:
            proc.stdin.write(json.dumps(frame) + "\n")
            proc.stdin.flush()
            if "id" in frame:
                lines.append(proc.stdout.readline())
    finally:
        # Let communicate() close stdin itself (it flushes, then closes since input=None):
        # closing stdin here first would make communicate()'s own flush raise
        # "ValueError: I/O operation on closed file" on CPython 3.12 (its flush() there does
        # not swallow ValueError for an already-closed stream).
        try:
            _, stderr = proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            _, stderr = proc.communicate()
    assert len(lines) == 2 and all(ln.strip() for ln in lines)
    for ln in lines:
        assert json.loads(ln)["jsonrpc"] == "2.0"
    assert stderr.strip(), "structured logs are expected on stderr"
