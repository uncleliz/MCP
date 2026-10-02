#!/usr/bin/env python3
"""CHG-003 controlled REAL embedding-model download: BAAI/bge-m3 (ADR-0023 §6d, ADR-0010).

What this proves and does, in order (stop on failure):

  1. Egress guard BEFORE opening HF: with the allow-list = '*.atlassian.net' only, assert that
     `huggingface.co` is DENIED (default-deny holds).
  2. Open HF egress: set MCP_EGRESS_ALLOWLIST='*.atlassian.net,huggingface.co' (+ the CDN host
     cdn-lfs.huggingface.co which HF uses for weight blobs). Then assert through the SINGLE
     check_egress choke point that `huggingface.co` is PERMITTED and an unlisted host
     (`evil.example.com`) is still DENIED.
  3. Controlled download via download.py::download_model — which routes `huggingface.co` through
     check_egress, flips HF_HUB_OFFLINE online ONLY for the download inside online_for_download(),
     loads BAAI/bge-m3 via LocalSentenceTransformerProvider (~2 GB fetch on first run), warms it,
     then restores offline. Reports model_id + dimensions + offline_restored.

The real tenant token is NOT used or read here (model path is independent of Atlassian). No secret
is printed. If the ~2 GB download fails (network/egress), this exits non-zero and the operator must
STOP and report — never fall back to the fake provider silently.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[6]
sys.path.insert(0, str(ROOT / "packages" / "mcp_ingest" / "src"))

from mcp_common.config import CommonSettings  # noqa: E402
from mcp_common.egress import EgressDenied, check_egress  # noqa: E402

from mcp_ingest.embedding.config import EmbeddingSettings  # noqa: E402
from mcp_ingest.embedding.download import HF_MODEL_HOST, download_model  # noqa: E402

MODEL = "BAAI/bge-m3"
DIM = 1024
CDN = "cdn-lfs.huggingface.co"


def _probe(host: str) -> str:
    try:
        check_egress(host, settings=CommonSettings())
        return "ALLOWED"
    except EgressDenied:
        return "DENIED"


def main() -> int:
    report: dict[str, object] = {}

    # 1. Default-deny proof BEFORE opening HF (allow-list is still '*.atlassian.net' only).
    os.environ["MCP_EGRESS_ALLOWLIST"] = "*.atlassian.net"
    before = {
        "tnexwm.atlassian.net": _probe("tnexwm.atlassian.net"),
        "huggingface.co": _probe("huggingface.co"),
    }
    report["egress_before_open"] = before
    print("[1] egress BEFORE opening HF (allow-list='*.atlassian.net'):", json.dumps(before))
    if before["huggingface.co"] != "DENIED":
        print("REFUSED: huggingface.co should be DENIED before opening HF egress", file=sys.stderr)
        return 2

    # 2. Open HF egress (add huggingface.co + its CDN); prove permitted + unlisted still denied.
    os.environ["MCP_EGRESS_ALLOWLIST"] = f"*.atlassian.net,{HF_MODEL_HOST},{CDN}"
    after = {
        "huggingface.co": _probe("huggingface.co"),
        "cdn-lfs.huggingface.co": _probe(CDN),
        "evil.example.com": _probe("evil.example.com"),
        "tnexwm.atlassian.net": _probe("tnexwm.atlassian.net"),
    }
    report["egress_after_open"] = after
    print("[2] egress AFTER opening HF "
          f"(allow-list='{os.environ['MCP_EGRESS_ALLOWLIST']}'):", json.dumps(after))
    if after["huggingface.co"] != "ALLOWED" or after["evil.example.com"] != "DENIED":
        print("REFUSED: expected huggingface.co ALLOWED and evil.example.com DENIED", file=sys.stderr)
        return 2

    # 3. Controlled download through the guarded path (HF_HUB_OFFLINE flips online, then restores).
    os.environ.setdefault("MCP_INGEST_ALLOW_LIVE_EGRESS", "true")
    settings = EmbeddingSettings(provider="local", model=MODEL, dimensions=DIM)
    pre_offline = os.environ.get("HF_HUB_OFFLINE")
    print(f"[3] HF_HUB_OFFLINE before download = {pre_offline!r}; starting controlled download of "
          f"{MODEL} (~2GB first run) via download_model() ...")
    result = download_model(settings)
    post_offline = os.environ.get("HF_HUB_OFFLINE")
    report["download"] = {
        "model_id": result.model_id,
        "dimensions": result.dimensions,
        "offline_restored": result.offline_restored,
        "hf_hub_offline_before": pre_offline,
        "hf_hub_offline_after": post_offline,
    }
    print("[3] download result:", json.dumps(report["download"]))
    if result.dimensions != DIM:
        print(f"REFUSED: dimension mismatch {result.dimensions} != {DIM}", file=sys.stderr)
        return 2
    if not result.offline_restored:
        print("REFUSED: HF_HUB_OFFLINE was not restored to offline after download", file=sys.stderr)
        return 2

    print("\nRESULT: OK — model", result.model_id, "dim", result.dimensions,
          "downloaded via guarded path; serving restored OFFLINE.")
    print("JSON:", json.dumps(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
