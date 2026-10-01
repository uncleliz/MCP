"""T-054 / TC-071: the embedding bake-off harness (scripts/bakeoff_embedding.py).

Uses only the deterministic fake provider: no model is downloaded, no network is touched. The
real measurement (bge-m3 vs multilingual-e5-large) is a manual step, see docs/spikes/S2.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from mcp_ingest.embedding.fake import DeterministicFakeProvider

from mcp_ingest import bakeoff

REPO = Path(__file__).resolve().parents[3]
SAMPLE = REPO / "eval" / "embedding_bakeoff.sample.yaml"
SCRIPT = REPO / "scripts" / "bakeoff_embedding.py"


def test_recall_at_k_is_the_fraction_of_relevant_documents_in_the_top_k() -> None:
    ranked = ["a", "b", "c", "d"]
    assert bakeoff.recall_at_k(ranked, {"a", "d"}, 1) == 0.5
    assert bakeoff.recall_at_k(ranked, {"a", "d"}, 4) == 1.0
    assert bakeoff.recall_at_k(ranked, {"z"}, 4) == 0.0
    assert bakeoff.recall_at_k(ranked, set(), 4) == 0.0


def test_reciprocal_rank_uses_the_first_relevant_hit() -> None:
    assert bakeoff.reciprocal_rank(["x", "a", "b"], {"a", "b"}) == 0.5
    assert bakeoff.reciprocal_rank(["x", "y"], {"a"}) == 0.0


def test_load_dataset_reads_corpus_and_queries() -> None:
    dataset = bakeoff.load_dataset(SAMPLE)
    assert len(dataset.corpus) == 12 and len(dataset.queries) == 10
    assert dataset.synthetic is True
    assert all(q.relevant and set(q.relevant) <= set(dataset.corpus) for q in dataset.queries)


def test_load_dataset_rejects_a_query_pointing_at_an_unknown_document(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "corpus: [{id: a, text: hello}]\nqueries: [{id: q, text: hi, relevant: [zzz]}]\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="zzz"):
        bakeoff.load_dataset(bad)


def test_evaluate_ranks_the_sample_set_with_the_fake_provider() -> None:
    dataset = bakeoff.load_dataset(SAMPLE)
    metrics = bakeoff.evaluate(DeterministicFakeProvider(), dataset, ks=(1, 5, 10))
    assert set(metrics.recall) == {1, 5, 10}
    assert metrics.recall[1] <= metrics.recall[5] <= metrics.recall[10] <= 1.0
    assert metrics.recall[10] > 0.5  # lexical overlap is enough for the synthetic set
    assert 0.0 < metrics.mrr <= 1.0
    assert metrics.doc_ms_mean >= 0 and metrics.query_ms_p95 >= metrics.query_ms_mean >= 0


def test_measure_model_reports_shape_load_time_and_memory() -> None:
    dataset = bakeoff.load_dataset(SAMPLE)
    result = bakeoff.measure_model("fake/hashed-bow", dataset, ks=(1, 5))
    assert result.model_id == "fake/hashed-bow" and result.dimensions == 1024
    assert result.n_docs == 12 and result.n_queries == 10
    assert result.load_time_s >= 0 and result.rss_delta_mb >= 0 and result.peak_rss_mb > 0


def test_render_markdown_has_one_row_per_model_and_flags_synthetic_data() -> None:
    dataset = bakeoff.load_dataset(SAMPLE)
    results = [bakeoff.measure_model(m, dataset, ks=(1, 5)) for m in ("fake/a", "fake/b")]
    table = bakeoff.render_markdown(results, ks=(1, 5), synthetic=True)
    assert "fake/a" in table and "fake/b" in table
    assert "recall@1" in table and "recall@5" in table and "MRR" in table
    assert "SYNTHETIC" in table


def test_main_writes_json_and_prints_a_table(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "result.json"
    code = bakeoff.main(
        ["--models", "fake/a,fake/b", "--dataset", str(SAMPLE), "--out", str(out), "--no-isolate"]
    )
    assert code == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert [r["model_id"] for r in payload["results"]] == ["fake/a", "fake/b"]
    assert payload["synthetic"] is True
    assert "fake/a" in capsys.readouterr().out


def test_script_runs_each_model_in_its_own_process(tmp_path: Path) -> None:
    out = tmp_path / "isolated.json"
    done = subprocess.run(
        [sys.executable, str(SCRIPT), "--models", "fake/x,fake/y", "--dataset", str(SAMPLE),
         "--out", str(out)],
        capture_output=True, text=True, timeout=120,
    )  # fmt: skip
    assert done.returncode == 0, done.stderr
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert [r["model_id"] for r in payload["results"]] == ["fake/x", "fake/y"]


def test_main_reports_a_missing_dataset_with_exit_2(capsys: pytest.CaptureFixture[str]) -> None:
    assert bakeoff.main(["--models", "fake/a", "--dataset", "/nonexistent.yaml"]) == 2
    assert "dataset" in capsys.readouterr().err


def test_main_reports_a_model_failure_with_exit_1(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def boom(model_id: str, *_a: object, **_k: object):
        raise bakeoff.EmbeddingError(f"cannot load {model_id}")

    monkeypatch.setattr(bakeoff, "measure_model", boom)
    code = bakeoff.main(["--models", "BAAI/bge-m3", "--dataset", str(SAMPLE), "--no-isolate"])
    assert code == 1
    assert "cannot load BAAI/bge-m3" in capsys.readouterr().err
