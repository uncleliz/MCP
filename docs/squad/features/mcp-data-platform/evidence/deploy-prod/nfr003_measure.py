#!/usr/bin/env python3
"""chg003-watch · honest first NFR-003 measurement over the re-embedded live corpus.

Runs TWO SEPARATE measurements over the REAL corpus that was re-embedded with the real model
(BAAI/bge-m3, 1024-d) and is read through the real server code path
(`PgVectorReadApi.semantic_search`, provider=local). The two are kept apart on purpose because
conflating them is exactly error E-004 / lesson L-002:

  (A) ANN-INDEX CORRECTNESS  — recall@k of the server's HNSW search vs exact brute force
      (`enable_indexscan=off`). Proves HNSW does not drop rows brute force finds. NOT a measure
      of semantic relevance. (ADR-0011 A3.)

  (B) SEMANTIC RETRIEVAL RELEVANCE (NFR-003) — for a small set of REAL golden queries written
      against the actual ingested content, does the known-relevant chunk come back in the top-k?
      First real-model measurement of NFR-003. Sample is TINY: a real-but-small-sample
      measurement, NOT a population recall; do not extrapolate.

The pure scoring/report functions (`score_query`, `assemble_report`) take already-retrieved
rankings and carry no DB/model dependency, so they are unit-tested in
`test_nfr003_measure.py`; `main()` is the thin live runner that feeds them real retrieval.

Read-only: connects as mcp_query_ro. HF stays offline (HF_HUB_OFFLINE=1); model loads from cache.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[6]

CORPUS_NOTE = (
    "corpus = 3 docs/27 chunks; of these ONLY 1 doc/25 chunks is the real EA Confluence page "
    "(tnexwm.atlassian.net). The other 2 docs (1 chunk each) are Oct-1 demo seed rows "
    "(wiki.example.test), not real EA content."
)
EA_DOC = "1c274560-ba52-45b7-a084-5aa35ee17da4"
TOP_K = 5

# (B) real golden queries over the real EA (ZNS/MKT) document; relevant_idx = chunk_index values
# whose content is the answer. Labels are conservative, from chunk headings/bodies read 2026-10-02.
GOLDEN = [
    {"id": "GQ-01", "text": "Job retry gửi ZNS chạy lúc mấy giờ?", "relevant_idx": [20]},
    {
        "id": "GQ-02",
        "text": "Cấu hình link OneLink trên Zalo như thế nào?",
        "relevant_idx": [0, 1, 2],
    },
    {"id": "GQ-03", "text": "Tạo OneLink trên AppsFlyer làm sao?", "relevant_idx": [3]},
    {
        "id": "GQ-04",
        "text": "Các tham số bắt buộc để AppsFlyer ghi nhận MMP là gì?",
        "relevant_idx": [7],
    },
    {
        "id": "GQ-05",
        "text": "Luồng tự động gửi ZNS khi khách onboard tài khoản",
        "relevant_idx": [23],
    },
    {
        "id": "GQ-06",
        "text": "Cách kiểm tra logs backend service khi lỗi gửi ZNS",
        "relevant_idx": [17],
    },
    {"id": "GQ-07", "text": "Quản lý ZNS template ở đâu?", "relevant_idx": [21]},
    {
        "id": "GQ-08",
        "text": "Cấu hình API Call trong Insider trên môi trường UAT",
        "relevant_idx": [8],
    },
]


def score_query(
    query_id: str,
    query_text: str,
    ranked_chunk_ids: list[int],
    relevant_ids: set[int],
    top_similarity: float | None,
) -> dict:
    """Pure (no DB/model) scoring of one query's returned ranking against its relevant set.

    hit_at_k = a known-relevant chunk is anywhere in the returned top-k.
    first_relevant_rank = 1-based rank of the first relevant hit, or None.
    """
    hit_at_k = bool(relevant_ids & set(ranked_chunk_ids))
    rank = next((n + 1 for n, cid in enumerate(ranked_chunk_ids) if cid in relevant_ids), None)
    return {
        "id": query_id,
        "query": query_text,
        "relevant_chunk_ids": sorted(relevant_ids),
        "returned_top_k": ranked_chunk_ids,
        "hit_at_k": hit_at_k,
        "first_relevant_rank": rank,
        "top_similarity": round(top_similarity, 4) if top_similarity is not None else None,
    }


def assemble_report(
    *,
    model: str,
    hf_offline: str | None,
    total_docs: int,
    total_chunks: int,
    top_k: int,
    hnsw_index_used: bool,
    ann_per_query: dict[str, float],
    sem_rows: list[dict],
    measured_at: str | None = None,
) -> dict:
    """Pure (no DB/model) assembly of the honest two-part report (A ANN-correctness, B NFR-003)."""
    n = len(sem_rows)
    hits = sum(1 for r in sem_rows if r["hit_at_k"])
    reciprocal = [1.0 / r["first_relevant_rank"] for r in sem_rows if r["first_relevant_rank"]]
    ann_mean = sum(ann_per_query.values()) / len(ann_per_query) if ann_per_query else 0.0
    return {
        "measured_at": measured_at or datetime.now(UTC).isoformat(),
        "model": model,
        "provider": "local",
        "hf_hub_offline": hf_offline,
        "corpus": {
            "documents_total": total_docs,
            "chunks_total": total_chunks,
            "note": CORPUS_NOTE,
        },
        "A_ann_index_correctness": {
            "what": "HNSW top-k vs exact brute force (enable_indexscan=off) — ADR-0011 A3. "
            "NOT a semantic-quality measure (E-004/L-002).",
            "top_k": top_k,
            "hnsw_index_used": hnsw_index_used,
            "mean_recall": round(ann_mean, 4),
            "per_query": {k: round(v, 4) for k, v in ann_per_query.items()},
        },
        "B_semantic_relevance_NFR003": {
            "what": "First real-model (bge-m3) measurement of NFR-003 semantic retrieval over the "
            "real corpus. REAL-BUT-SMALL-SAMPLE; not a population recall; do not extrapolate.",
            "sample_size_queries": n,
            "corpus_scope": "EA document 1c274560 only (the one real Confluence page); demo-seed "
            "docs not probed",
            "top_k": top_k,
            "hit_at_k_count": hits,
            "hit_at_k_rate": round(hits / n, 4) if n else None,
            "mrr": round(sum(reciprocal) / n, 4) if n else None,
            "calibration_status": "uncalibrated",
            "per_query": sem_rows,
        },
    }


def _ro_dsn() -> str:
    """Read-only DSN from the git-ignored credentials/.lookup.env (never hardcode the secret)."""
    env = ROOT / "credentials" / ".lookup.env"
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.startswith("MCP_PGVECTOR_DSN="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("MCP_PGVECTOR_DSN not found in credentials/.lookup.env")


async def main() -> int:  # pragma: no cover - live runner, exercised at measurement time
    from datetime import datetime as dt

    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    sys.path.insert(0, str(ROOT / "scripts"))
    for pkg in ("mcp_common", "mcp_pgvector", "mcp_ingest"):
        sys.path.insert(0, str(ROOT / "packages" / pkg / "src"))

    import psycopg
    from mcp_common.config import CommonSettings
    from mcp_ingest.embedding import EmbeddingSettings, build_provider
    from mcp_pgvector.client import PgVectorClient
    from mcp_pgvector.read_api import PgVectorReadApi
    from mcp_pgvector.settings import Settings
    from pydantic import SecretStr
    from recall_benchmark import brute_force_ids, force_index_dsn, index_is_used

    ro_dsn = _ro_dsn()
    provider = build_provider(EmbeddingSettings())  # provider=local bge-m3 (defaults)
    model = provider.model_id

    with psycopg.connect(ro_dsn, autocommit=True) as conn:
        idx_to_id = {
            int(r[0]): int(r[1])
            for r in conn.execute(
                "SELECT chunk_index, id FROM kb.chunks WHERE document_id = %s", (EA_DOC,)
            ).fetchall()
        }
        total_chunks = conn.execute("SELECT count(*) FROM kb.chunks").fetchone()[0]
        total_docs = conn.execute(
            "SELECT count(*) FROM kb.documents WHERE deleted_at IS NULL"
        ).fetchone()[0]

    ann_dsn = force_index_dsn(ro_dsn)
    settings = Settings(dsn=SecretStr(ann_dsn))
    client = PgVectorClient(settings, common=CommonSettings())
    api = PgVectorReadApi(client, provider, CommonSettings(), settings, now=lambda: dt.now(UTC))

    ann_per_query: dict[str, float] = {}
    sem_rows: list[dict] = []
    try:
        with psycopg.connect(ro_dsn, autocommit=True) as conn:
            probe = provider.embed_query(GOLDEN[0]["text"])
            with psycopg.connect(ann_dsn, autocommit=True) as pc:
                hnsw_used = index_is_used(pc, probe, model, TOP_K)
            for q in GOLDEN:
                vec = provider.embed_query(q["text"])
                exact = set(brute_force_ids(conn, vec, model, TOP_K))
                outcome = await api.semantic_search(
                    query=q["text"], top_k=TOP_K, min_similarity=0.0
                )
                approx = {int(it["chunk_id"]) for it in outcome.result.items}
                ann_per_query[q["id"]] = len(exact & approx) / max(len(exact), 1)
                relevant_ids = {idx_to_id[i] for i in q["relevant_idx"] if i in idx_to_id}
                ranked = [int(it["chunk_id"]) for it in outcome.result.items]
                top_sim = outcome.result.items[0]["similarity"] if outcome.result.items else None
                sem_rows.append(score_query(q["id"], q["text"], ranked, relevant_ids, top_sim))
    finally:
        await client.aclose()

    report = assemble_report(
        model=model,
        hf_offline=os.environ.get("HF_HUB_OFFLINE"),
        total_docs=total_docs,
        total_chunks=total_chunks,
        top_k=TOP_K,
        hnsw_index_used=hnsw_used,
        ann_per_query=ann_per_query,
        sem_rows=sem_rows,
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(asyncio.run(main()))
