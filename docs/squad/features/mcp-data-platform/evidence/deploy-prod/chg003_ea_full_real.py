#!/usr/bin/env python3
"""CHG-003 FULL EA ingest with the REAL bge-m3 model (squad-release, deploy-prod).

Differences from the earlier bounded chg003_ea_pull.py:
  * REAL embedding model: provider=local, BAAI/bge-m3, 1024-d, served OFFLINE from the on-disk
    model downloaded by chg003_hf_download.py (HF_HUB_OFFLINE=1 — NO network during embedding).
    No loopback fake endpoint. Query/stored vectors now carry real semantics.
  * FULL space: no --limit (ingest the ENTIRE EA space). --dry-run supported for the pre-flight.
  * Egress is open ONLY to *.atlassian.net for the pull (read-only GET). HF is NOT needed at
    ingest time (model already on disk); the allow-list for this run is '*.atlassian.net'.

The token is read from the environment and NEVER printed.

Usage (env sourced from credentials/.ingest-sources.env + MCP_INGEST_CONFLUENCE_TEAM_SPACES=EA):
    uv run python .../chg003_ea_full_real.py --dry-run
    uv run python .../chg003_ea_full_real.py            # real write, full space
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[6]

RW_DSN = "postgresql://mcp_ingest_rw:mcp_ingest_rw_dev_password@127.0.0.1:5433/mcp_kb"
MODEL = "BAAI/bge-m3"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if os.environ.get("MCP_INGEST_CONFLUENCE_TEAM_SPACES") != "EA":
        print("REFUSED: MCP_INGEST_CONFLUENCE_TEAM_SPACES must be 'EA'", file=sys.stderr)
        return 2
    if "atlassian.net" not in (os.environ.get("MCP_CONFLUENCE_BASE_URL") or ""):
        print("REFUSED: MCP_CONFLUENCE_BASE_URL is not the real atlassian tenant", file=sys.stderr)
        return 2

    env = dict(os.environ)
    # REAL model, served offline (weights already on disk from the controlled download).
    env.update({
        "MCP_INGEST_PGVECTOR_DSN": RW_DSN,
        "MCP_INGEST_EMBEDDING_PROVIDER": "local",
        "MCP_INGEST_EMBEDDING_MODEL": MODEL,
        "MCP_INGEST_EMBEDDING_DIMENSIONS": "1024",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        # Pull egress: atlassian only (model is local; HF not needed at ingest).
        "MCP_EGRESS_ALLOWLIST": "*.atlassian.net",
    })
    # full space => NO --limit AND --mode full so the whole EA space is re-crawled (the earlier
    # bounded pull advanced the incremental checkpoint; incremental would only see changed docs).
    # Upsert is by source id, so a full re-crawl is still additive/idempotent for unchanged pages.
    cli = ["run", "--source", "confluence", "--mode", "full", "--json"]
    if args.dry_run:
        cli.append("--dry-run")
    print(f"$ mcp-ingest {' '.join(cli)}  (space=EA FULL, real tenant, REAL bge-m3 offline)")
    p = subprocess.run([sys.executable, "-m", "mcp_ingest", *cli],
                       capture_output=True, text=True, timeout=3600, cwd=str(ROOT), env=env)
    print(p.stdout.rstrip())
    if p.stderr.strip():
        print("--- stderr tail ---", file=sys.stderr)
        print(p.stderr[-3000:], file=sys.stderr)
    print(f"exit={p.returncode}")
    return p.returncode


if __name__ == "__main__":
    raise SystemExit(main())
