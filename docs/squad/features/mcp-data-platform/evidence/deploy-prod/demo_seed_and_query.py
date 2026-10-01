#!/usr/bin/env python3
"""Local go-live DEMO for mcp-data-platform (squad-release).

DEMO DATA ONLY — no real sources.
  * Real Confluence/GitLab/OpenSearch need VPN + credentials that are not present on this
    machine, so a tiny local HTTP stub serves two SAMPLE Confluence pages instead.
  * Hugging Face egress is blocked (ADR-0010 / HF 403), so embeddings come from the
    repo's DeterministicFakeProvider exposed behind a local OpenAI-compatible HTTP
    endpoint (exactly the seam the e2e suite uses). This proves the ingest -> pgvector ->
    MCP-server-over-stdio path end to end against the REAL Postgres+pgvector in Docker;
    it does NOT measure semantic relevance (CAB caveat 1 / NFR-003 UNVERIFIED).

Everything else is real: the real `mcp-ingest` CLI writes to the real pgvector DB, and the
real `mcp-pgvector` MCP server answers real JSON-RPC tool calls read from that DB.

Run:  uv run python docs/.../evidence/deploy-prod/demo_seed_and_query.py
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[6]
sys.path.insert(0, str(ROOT / "e2e"))
sys.path.insert(0, str(ROOT / "packages" / "mcp_ingest" / "src"))

from mcp_ingest.embedding.fake import DeterministicFakeProvider  # noqa: E402

ADMIN_DSN = "postgresql://mcp_admin:mcp_admin_dev_password@localhost:5433/mcp_kb"
RW_DSN = "postgresql://mcp_ingest_rw:mcp_ingest_rw_dev_password@localhost:5433/mcp_kb"
RO_DSN = "postgresql://mcp_query_ro:mcp_query_ro_dev_password@localhost:5433/mcp_kb"
MODEL = "fake/hashed-bow"
PROVIDER = DeterministicFakeProvider(dimensions=1024, model_id=MODEL)

# --- SAMPLE Confluence pages (demo data, not a real wiki) -----------------------------
PAGES = [
    {
        "id": "1001", "type": "page", "title": "Payment retry policy",
        "space": {"key": "PAY", "type": "global"},
        "version": {"number": 3, "when": "2026-09-28T09:00:00.000Z",
                    "by": {"displayName": "An Nguyen"}},
        "body": {"storage": {"value": "<h1>Payment retry policy</h1><h2>Backoff</h2>"
                 "<p>The payment worker retries failed transactions three times with "
                 "exponential backoff before moving the message to the dead letter "
                 "queue payment-retry-dlq.</p>"}},
        "ancestors": [],
        "restrictions": {"read": {"restrictions": {"user": {"results": []},
                                                     "group": {"results": []}}}},
        "_links": {"webui": "/spaces/PAY/pages/1001/payment-retry",
                   "base": "https://wiki.example.test/wiki"},
    },
    {
        "id": "1002", "type": "page", "title": "Incident runbook: Kafka consumer lag",
        "space": {"key": "PAY", "type": "global"},
        "version": {"number": 5, "when": "2026-09-29T14:30:00.000Z",
                    "by": {"displayName": "Binh Tran"}},
        "body": {"storage": {"value": "<h1>Kafka consumer lag runbook</h1>"
                 "<p>When consumer lag on the orders topic exceeds the alert threshold, "
                 "scale the consumer group and check the broker for under-replicated "
                 "partitions. Restart the lagging consumer last.</p>"}},
        "ancestors": [],
        "restrictions": {"read": {"restrictions": {"user": {"results": []},
                                                     "group": {"results": []}}}},
        "_links": {"webui": "/spaces/PAY/pages/1002/kafka-lag-runbook",
                   "base": "https://wiki.example.test/wiki"},
    },
]


def _start_stub(routes):
    class H(BaseHTTPRequestHandler):
        def _h(self):
            p = urlparse(self.path)
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            status, payload = routes(self.command, p.path, body)
            raw = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        do_GET = do_POST = _h  # noqa: N815

        def log_message(self, *a):  # silence
            return
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def confluence_routes(method, path, body):
    if path.endswith("/rest/api/content/search"):
        return 200, {"results": PAGES, "start": 0, "limit": 25, "size": len(PAGES),
                     "_links": {}}
    if path.endswith("/rest/api/user/current"):
        return 200, {"accountId": "demo", "email": "demo@example.test"}
    return 404, {}


def embed_routes(method, path, body):
    if path.endswith("/embeddings"):
        texts = json.loads(body)["input"]
        vecs = PROVIDER.embed_documents(texts)
        return 200, {"data": [{"index": i, "embedding": v} for i, v in enumerate(vecs)]}
    return 404, {}


def _ingest_env(conf_url: str, embed_url: str) -> dict:
    env = dict(os.environ)
    env.update({
        "MCP_INGEST_ADMIN_DSN": ADMIN_DSN,
        "MCP_INGEST_PGVECTOR_DSN": RW_DSN,
        "MCP_INGEST_EMBEDDING_PROVIDER": "http",
        "MCP_INGEST_EMBEDDING_URL": embed_url + "/v1",
        "MCP_INGEST_EMBEDDING_MODEL": MODEL,
        "MCP_INGEST_EMBEDDING_DIMENSIONS": "1024",
        "MCP_INGEST_CONFLUENCE_TEAM_SPACES": "PAY",
        "MCP_CONFLUENCE_BASE_URL": conf_url,
        "MCP_CONFLUENCE_EMAIL": "demo@example.test",
        "MCP_CONFLUENCE_API_TOKEN": "demo-token",
        "MCP_ALLOW_UNVERIFIED_CREDENTIALS": "true",
    })
    return env


def run_cli(args, env, expect=(0,)):
    p = subprocess.run([sys.executable, "-m", "mcp_ingest", *args],
                       capture_output=True, text=True, timeout=180, cwd=str(ROOT), env=env)
    print(f"\n$ mcp-ingest {' '.join(args)}  (exit {p.returncode})")
    print(p.stdout.rstrip())
    if p.returncode not in expect:
        print(p.stderr[-1500:], file=sys.stderr)
        raise SystemExit(f"CLI failed: {args}")
    return p


async def demo_queries(embed_url: str):
    from harness import open_server, result_payload  # noqa: E402
    pg_env = {
        "MCP_PGVECTOR_DSN": RO_DSN,
        "MCP_INGEST_EMBEDDING_PROVIDER": "http",
        "MCP_INGEST_EMBEDDING_URL": embed_url + "/v1",
        "MCP_INGEST_EMBEDDING_MODEL": MODEL,
        "MCP_INGEST_EMBEDDING_DIMENSIONS": "1024",
    }
    async with open_server("pgvector", pg_env) as s:
        tools = sorted(t.name for t in (await s.list_tools()).tools)
        print("\n--- mcp-pgvector tools/list (read-only surface) ---")
        print(json.dumps({"jsonrpc": "2.0", "result": {"tools": tools}}, indent=2))

        print("\n--- tools/call kb_list_sources ---")
        r1 = await s.call_tool("kb_list_sources", {})
        print(json.dumps(result_payload(r1), indent=2, ensure_ascii=False)[:1800])

        print("\n--- tools/call kb_semantic_search "
              "{query: 'how many times does the payment worker retry', top_k: 3} ---")
        r2 = await s.call_tool("kb_semantic_search",
                               {"query": "how many times does the payment worker retry failed "
                                "transactions", "top_k": 3})
        print(json.dumps(result_payload(r2), indent=2, ensure_ascii=False)[:2200])

        print("\n--- negative: tools/call kb_delete_document (must be refused, read-only) ---")
        r3 = await s.call_tool("kb_delete_document", {"id": "x"})
        txt = "\n".join(c.text for c in r3.content if getattr(c, "type", "") == "text")
        print(json.dumps({"isError": bool(r3.isError), "text": txt,
                          "structuredContent": r3.structuredContent}, indent=2))


def main():
    conf_srv, conf_url = _start_stub(confluence_routes)
    embed_srv, embed_url = _start_stub(embed_routes)
    try:
        env = _ingest_env(conf_url, embed_url)
        run_cli(["db", "upgrade"], env)
        run_cli(["run", "--source", "confluence", "--mode", "full", "--json"], env, expect=(0, 1))
        run_cli(["status", "--json"], env)
        asyncio.run(demo_queries(embed_url))
    finally:
        conf_srv.shutdown(); embed_srv.shutdown()


if __name__ == "__main__":
    main()
