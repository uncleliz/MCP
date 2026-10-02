#!/usr/bin/env python3
"""CHG-003 REAL bounded pull for space EA (squad-release, deploy-prod).

REAL SOURCE, additive, bounded.
  * Confluence is the REAL tenant from the environment (MCP_CONFLUENCE_* loaded from the
    git-ignored credentials file; base_url=https://tnexwm.atlassian.net/wiki). The ingest
    pull is read-only (GET/HEAD) through the single egress choke point; only *.atlassian.net
    is dialable (MCP_EGRESS_ALLOWLIST). This is the first REAL content pull on this feature.
  * Embeddings use the repo's DeterministicFakeProvider behind a LOOPBACK (127.0.0.1)
    OpenAI-compatible endpoint — NOT an external call, NO egress, NO Hugging Face download
    (HF is not in the allow-list this run). Same model id `fake/hashed-bow`, 1024-d, as the
    existing demo rows, so the pull is additive and never needs a schema migration.
    Semantic *relevance* is therefore still NFR-003 UNVERIFIED (CEO-accepted DK1); this run
    proves the end-to-end REAL ingest -> pgvector -> read-only MCP path with REAL EA content.

The token is read from the environment and NEVER printed.

Usage (env already sourced + MCP_INGEST_CONFLUENCE_TEAM_SPACES=EA exported):
    uv run python .../chg003_ea_pull.py --dry-run
    uv run python .../chg003_ea_pull.py            # real write
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[6]
sys.path.insert(0, str(ROOT / "packages" / "mcp_ingest" / "src"))

from mcp_ingest.embedding.fake import DeterministicFakeProvider  # noqa: E402

RW_DSN = "postgresql://mcp_ingest_rw:mcp_ingest_rw_dev_password@127.0.0.1:5433/mcp_kb"
MODEL = "fake/hashed-bow"
PROVIDER = DeterministicFakeProvider(dimensions=1024, model_id=MODEL)


def _start_embed_stub():
    """Loopback OpenAI-compatible /embeddings endpoint — DeterministicFakeProvider, no egress."""
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

        def log_message(self, *a):  # silence
            return

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", default="20")
    args = ap.parse_args()

    if os.environ.get("MCP_INGEST_CONFLUENCE_TEAM_SPACES") != "EA":
        print("REFUSED: MCP_INGEST_CONFLUENCE_TEAM_SPACES must be 'EA' for this run", file=sys.stderr)
        return 2
    if "atlassian.net" not in (os.environ.get("MCP_CONFLUENCE_BASE_URL") or ""):
        print("REFUSED: MCP_CONFLUENCE_BASE_URL is not the real atlassian tenant", file=sys.stderr)
        return 2

    embed_srv, embed_url = _start_embed_stub()
    try:
        env = dict(os.environ)
        env.update({
            "MCP_INGEST_PGVECTOR_DSN": RW_DSN,
            "MCP_INGEST_EMBEDDING_PROVIDER": "http",
            "MCP_INGEST_EMBEDDING_URL": embed_url + "/v1",
            "MCP_INGEST_EMBEDDING_MODEL": MODEL,
            "MCP_INGEST_EMBEDDING_DIMENSIONS": "1024",
        })
        cli = ["run", "--source", "confluence", "--limit", str(args.limit), "--json"]
        if args.dry_run:
            cli.append("--dry-run")
        print(f"$ mcp-ingest {' '.join(cli)}  (space=EA, real tenant, loopback embed)")
        p = subprocess.run([sys.executable, "-m", "mcp_ingest", *cli],
                           capture_output=True, text=True, timeout=600, cwd=str(ROOT), env=env)
        print(p.stdout.rstrip())
        if p.stderr.strip():
            print("--- stderr tail ---", file=sys.stderr)
            print(p.stderr[-3000:], file=sys.stderr)
        print(f"exit={p.returncode}")
        return p.returncode
    finally:
        embed_srv.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
