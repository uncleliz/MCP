"""Unit tests for the pure metric logic of the chg003-watch NFR-003 harness.

These cover the DB/model-free scoring + report assembly (the changed code), so coverage of the
changed logic is measurable without the live mcp_query_ro credential (which was rotated after the
measurement ran). The `main()` live runner is marked `# pragma: no cover` (integration, exercised
at measurement time; its output is the committed measurement JSON).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_HARNESS = Path(__file__).with_name("nfr003_measure.py")
_spec = importlib.util.spec_from_file_location("nfr003_measure", _HARNESS)
assert _spec and _spec.loader
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)


def test_score_query_hit_at_rank_one():
    row = m.score_query("GQ-01", "q", [23, 16, 17], {23}, 0.73441)
    assert row["hit_at_k"] is True
    assert row["first_relevant_rank"] == 1
    assert row["relevant_chunk_ids"] == [23]
    assert row["returned_top_k"] == [23, 16, 17]
    assert row["top_similarity"] == 0.7344  # rounded to 4dp


def test_score_query_hit_lower_rank():
    row = m.score_query("GQ-X", "q", [9, 4, 7], {7}, None)
    assert row["hit_at_k"] is True
    assert row["first_relevant_rank"] == 3
    assert row["top_similarity"] is None


def test_score_query_miss():
    row = m.score_query("GQ-Y", "q", [1, 2, 3], {99}, 0.1)
    assert row["hit_at_k"] is False
    assert row["first_relevant_rank"] is None


def test_assemble_report_matches_recorded_measurement():
    """Reproduce the honest two-part report from the real recorded per-query outcomes."""
    # the real returned_top_k rankings recorded on 2026-10-02 (measurement JSON)
    recorded = [
        ("GQ-01", [23, 16, 17, 26, 18], {23}, 0.7344),
        ("GQ-02", [4, 3, 5, 6, 15], {3, 4, 5}, 0.6553),
        ("GQ-03", [6, 4, 9, 14, 10], {6}, 0.7209),
        ("GQ-04", [10, 9, 6, 11, 8], {10}, 0.765),
        ("GQ-05", [26, 27, 18, 17, 14], {26}, 0.6899),
        ("GQ-06", [20, 16, 17, 19, 18], {20}, 0.6828),
        ("GQ-07", [24, 17, 26, 27, 14], {24}, 0.7005),
        ("GQ-08", [11, 8, 7, 9, 6], {11}, 0.8091),
    ]
    sem_rows = [m.score_query(qid, qid, ranked, rel, sim) for qid, ranked, rel, sim in recorded]
    report = m.assemble_report(
        model="BAAI/bge-m3",
        hf_offline="1",
        total_docs=3,
        total_chunks=27,
        top_k=5,
        hnsw_index_used=False,
        ann_per_query={f"GQ-{i:02d}": 1.0 for i in range(1, 9)},
        sem_rows=sem_rows,
        measured_at="2026-10-02T15:05:52+00:00",
    )
    b = report["B_semantic_relevance_NFR003"]
    assert b["sample_size_queries"] == 8
    assert b["hit_at_k_count"] == 8
    assert b["hit_at_k_rate"] == 1.0
    assert b["mrr"] == 1.0  # every relevant chunk ranked #1
    assert b["calibration_status"] == "uncalibrated"
    # (A) and (B) stay separate; A is ANN-correctness only, flagged not-semantic (E-004/L-002)
    a = report["A_ann_index_correctness"]
    assert a["mean_recall"] == 1.0
    assert a["hnsw_index_used"] is False
    assert "NOT a semantic-quality measure" in a["what"]
    assert report["model"] == "BAAI/bge-m3"
    assert "small-sample" in b["what"].lower() or "do not extrapolate" in b["what"].lower()


def test_assemble_report_empty_sample_is_safe():
    report = m.assemble_report(
        model="BAAI/bge-m3",
        hf_offline="1",
        total_docs=0,
        total_chunks=0,
        top_k=5,
        hnsw_index_used=False,
        ann_per_query={},
        sem_rows=[],
    )
    b = report["B_semantic_relevance_NFR003"]
    assert b["sample_size_queries"] == 0
    assert b["hit_at_k_rate"] is None
    assert b["mrr"] is None
    assert report["A_ann_index_correctness"]["mean_recall"] == 0.0


def test_golden_set_is_well_formed():
    ids = [q["id"] for q in m.GOLDEN]
    assert len(set(ids)) == len(ids) == 8
    assert all(q["text"] and q["relevant_idx"] for q in m.GOLDEN)
