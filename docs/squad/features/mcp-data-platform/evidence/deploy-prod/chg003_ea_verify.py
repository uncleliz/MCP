#!/usr/bin/env python3
"""CHG-003 Step 6 — VERIFY retrieval over stdio against REAL EA content.

Starts the real `mcp-pgvector` MCP server over stdio (read-only role mcp_query_ro) and calls
kb_semantic_search with a query likely to hit the EA page just ingested. Proves the end-to-end
REAL path: a real stored chunk is returned whose citation/source_uri resolves to
tnexwm.atlassian.net (an EA page). Embeddings use the same loopback DeterministicFakeProvider
(no egress, no HF); relevance is NFR-003 UNVERIFIED, but the stored row and its citation are real.

The transcript is printed as JSON-RPC-style envelopes; no secret is printed.
"""
from __future__ import annotations

import asyncio
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[6]
sys.path.insert(0, str(ROOT / "e2e"))
sys.path.insert(0, str(ROOT / "packages" / "mcp_ingest" / "src"))

from mcp_ingest.embedding.fake import DeterministicFakeProvider  # noqa: E402

RO_DSN = "postgresql://mcp_query_ro:mcp_query_ro_dev_password@127.0.0.1:5433/mcp_kb"
MODEL = "fake/hashed-bow"
PROVIDER = DeterministicFakeProvider(dimensions=1024, model_id=MODEL)
QUERIES = [
    "hướng dẫn MKT config ZNS",
    "marketing configuration guide",
]


def _start_embed_stub():
    class H(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            p = urlparse(self.path)
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            if p.path.endswith("/embeddings"):
                texts = json.loads(body)["input"]
                vecs = PROVIDER.embed_documents(texts)
                payload = {"data": [{"index": i, "embedding": v} for i, v in enumerate(vecs)]}
                status = 200
            else:
                status, payload = 404, {}
            raw = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *a):
            return

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


async def run(embed_url: str) -> int:
    from harness import open_server, result_payload  # noqa: E402

    pg_env = {
        "MCP_PGVECTOR_DSN": RO_DSN,
        "MCP_INGEST_EMBEDDING_PROVIDER": "http",
        "MCP_INGEST_EMBEDDING_URL": embed_url + "/v1",
        "MCP_INGEST_EMBEDDING_MODEL": MODEL,
        "MCP_INGEST_EMBEDDING_DIMENSIONS": "1024",
    }
    ok_ea = False
    async with open_server("pgvector", pg_env) as s:
        tools = sorted(t.name for t in (await s.list_tools()).tools)
        print("--- JSON-RPC: tools/list (read-only surface) ---")
        print(json.dumps({"jsonrpc": "2.0", "result": {"tools": tools}}, indent=2))

        print("\n--- JSON-RPC: tools/call kb_list_sources {} ---")
        r0 = await s.call_tool("kb_list_sources", {})
        print(json.dumps(result_payload(r0), indent=2, ensure_ascii=False)[:1200])

        for q in QUERIES:
            print(f"\n--- JSON-RPC: tools/call kb_semantic_search "
                  f"{{query: {q!r}, top_k: 5, min_similarity: 0.0}} ---")
            print("    (min_similarity=0.0: fake/hashed-bow embeddings carry no semantic signal — "
                  "NFR-003 UNVERIFIED — so we retrieve by vector-nearest to prove the REAL stored "
                  "EA chunk is reachable end-to-end through the read-only MCP server; the row and "
                  "its citation are real.)")
            r = await s.call_tool("kb_semantic_search",
                                  {"query": q, "top_k": 5, "min_similarity": 0.0})
            payload = result_payload(r)
            text = json.dumps(payload, ensure_ascii=False)
            print(json.dumps(payload, indent=2, ensure_ascii=False)[:3200])
            if "tnexwm.atlassian.net" in text and "/spaces/EA/" in text:
                ok_ea = True

    print("\n=== VERDICT ===")
    if ok_ea:
        print("PASS: kb_semantic_search returned a real stored chunk citing tnexwm.atlassian.net "
              "(an EA page). End-to-end REAL path proven. (Relevance is NFR-003 UNVERIFIED — "
              "fake embeddings; the stored row + citation are real.)")
        return 0
    print("FAIL: no EA citation (tnexwm.atlassian.net /spaces/EA/) returned.")
    return 1


def main() -> int:
    srv, url = _start_embed_stub()
    try:
        return asyncio.run(run(url))
    finally:
        srv.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
