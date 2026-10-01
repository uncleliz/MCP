"""Embedding bake-off (spike S2 / T-054): bge-m3 vs multilingual-e5-large.

    uv sync --package mcp-ingest --extra local-embeddings     # sentence-transformers + torch
    uv run python scripts/bakeoff_embedding.py --dataset eval/embedding_bakeoff.yaml \
        --out docs/spikes/S2-embedding-bakeoff.result.json

Prints a Markdown table (recall@k, MRR, per-doc and per-query CPU latency, load time, RSS).
Each model runs in its own process so peak RSS is meaningful. The logic lives in
`mcp_ingest.bakeoff` (unit-tested with a deterministic fake provider); this file is only the
entry point. See docs/spikes/S2-embedding-bakeoff.md for the procedure and for what is still
pending.
"""

from __future__ import annotations

import sys

from mcp_ingest.bakeoff import main

if __name__ == "__main__":
    sys.exit(main())
