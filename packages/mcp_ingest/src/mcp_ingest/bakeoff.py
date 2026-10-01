"""Embedding bake-off harness (spike S2 / T-054): recall@k, MRR, latency, RAM and load time of
candidate embedding models on a labelled corpus.

Used through `scripts/bakeoff_embedding.py`. Each model is measured in its **own process** by
default, because peak RSS is a per-process high-water mark and two ~2 GB models in one process
would pollute each other's number. No psycopg import (R19).
"""

from __future__ import annotations

import argparse
import json
import math
import resource
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

from mcp_ingest.embedding import EmbeddingError, EmbeddingSettings, build_provider
from mcp_ingest.embedding.fake import DeterministicFakeProvider
from mcp_ingest.ports import EmbeddingProvider

__all__ = [
    "Dataset",
    "EmbeddingError",
    "ModelResult",
    "Query",
    "evaluate",
    "load_dataset",
    "main",
    "measure_model",
    "recall_at_k",
    "reciprocal_rank",
    "render_markdown",
]

DEFAULT_KS = (1, 5, 10)
DEFAULT_MODELS = ("BAAI/bge-m3", "intfloat/multilingual-e5-large")


@dataclass(frozen=True)
class Query:
    id: str
    text: str
    relevant: tuple[str, ...]


@dataclass(frozen=True)
class Dataset:
    corpus: dict[str, str]
    queries: list[Query]
    synthetic: bool = False


@dataclass
class Metrics:
    recall: dict[int, float]
    mrr: float
    doc_ms_mean: float
    query_ms_mean: float
    query_ms_p95: float


@dataclass
class ModelResult:
    model_id: str
    dimensions: int
    n_docs: int
    n_queries: int
    load_time_s: float
    rss_delta_mb: float
    peak_rss_mb: float
    recall: dict[int, float] = field(default_factory=dict)
    mrr: float = 0.0
    doc_ms_mean: float = 0.0
    query_ms_mean: float = 0.0
    query_ms_p95: float = 0.0


# -- metrics -----------------------------------------------------------------------------------


def recall_at_k(ranked: Sequence[str], relevant: set[str] | frozenset[str], k: int) -> float:
    if not relevant:
        return 0.0
    return len(set(ranked[:k]) & set(relevant)) / len(relevant)


def reciprocal_rank(ranked: Sequence[str], relevant: set[str] | frozenset[str]) -> float:
    for position, doc_id in enumerate(ranked, start=1):
        if doc_id in relevant:
            return 1.0 / position
    return 0.0


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(math.ceil(fraction * len(ordered)) - 1, 0)
    return ordered[index]


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


# -- dataset -----------------------------------------------------------------------------------


def load_dataset(path: Path) -> Dataset:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    corpus = {str(item["id"]): str(item["text"]) for item in raw.get("corpus", [])}
    queries = [
        Query(str(q["id"]), str(q["text"]), tuple(str(r) for r in q.get("relevant", [])))
        for q in raw.get("queries", [])
    ]
    for query in queries:
        unknown = [r for r in query.relevant if r not in corpus]
        if unknown:
            raise ValueError(f"query {query.id} references unknown document(s): {unknown}")
    if not corpus or not queries:
        raise ValueError("dataset needs a non-empty corpus and queries")
    return Dataset(corpus, queries, bool(raw.get("synthetic", False)))


# -- measurement -------------------------------------------------------------------------------


def evaluate(
    provider: EmbeddingProvider, dataset: Dataset, *, ks: Sequence[int] = DEFAULT_KS
) -> Metrics:
    ids = list(dataset.corpus)
    started = time.perf_counter()
    vectors = provider.embed_documents([dataset.corpus[i] for i in ids])
    doc_ms_mean = (time.perf_counter() - started) * 1000 / len(ids)
    recalls: dict[int, list[float]] = {k: [] for k in ks}
    reciprocal: list[float] = []
    latencies: list[float] = []
    for query in dataset.queries:
        t0 = time.perf_counter()
        qvec = provider.embed_query(query.text)
        latencies.append((time.perf_counter() - t0) * 1000)
        scores = sorted(
            zip(ids, vectors, strict=True), key=lambda p: _dot(qvec, p[1]), reverse=True
        )
        ranked = [doc_id for doc_id, _ in scores]
        relevant = set(query.relevant)
        for k in ks:
            recalls[k].append(recall_at_k(ranked, relevant, k))
        reciprocal.append(reciprocal_rank(ranked, relevant))
    count = len(dataset.queries)
    return Metrics(
        recall={k: sum(v) / count for k, v in recalls.items()},
        mrr=sum(reciprocal) / count,
        doc_ms_mean=doc_ms_mean,
        query_ms_mean=sum(latencies) / count,
        query_ms_p95=_percentile(latencies, 0.95),
    )


def _rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024  # KiB on Linux


def _make_provider(model_id: str) -> EmbeddingProvider:
    if model_id.startswith("fake/"):
        return DeterministicFakeProvider(model_id=model_id)
    return build_provider(EmbeddingSettings(model=model_id))


def measure_model(model_id: str, dataset: Dataset, ks: Sequence[int] = DEFAULT_KS) -> ModelResult:
    """Load time = constructing the provider + the first (warm-up) embed, which is when the
    lazily-loaded weights are actually read."""
    rss_before = _rss_mb()
    started = time.perf_counter()
    provider = _make_provider(model_id)
    provider.embed_query("warm-up")
    load_time = time.perf_counter() - started
    metrics = evaluate(provider, dataset, ks=ks)
    peak = _rss_mb()
    return ModelResult(
        model_id=provider.model_id,
        dimensions=provider.dimensions,
        n_docs=len(dataset.corpus),
        n_queries=len(dataset.queries),
        load_time_s=load_time,
        rss_delta_mb=max(peak - rss_before, 0.0),
        peak_rss_mb=peak,
        recall=metrics.recall,
        mrr=metrics.mrr,
        doc_ms_mean=metrics.doc_ms_mean,
        query_ms_mean=metrics.query_ms_mean,
        query_ms_p95=metrics.query_ms_p95,
    )


def _run_isolated(model_id: str, dataset_path: Path, ks: Sequence[int]) -> ModelResult:
    command = [
        sys.executable, "-m", "mcp_ingest.bakeoff", "--single", model_id,
        "--dataset", str(dataset_path), "--ks", ",".join(str(k) for k in ks),
    ]  # fmt: skip
    done = subprocess.run(command, capture_output=True, text=True, check=False)
    if done.returncode != 0:
        raise EmbeddingError(done.stderr.strip() or f"measurement of {model_id} failed")
    payload = json.loads(done.stdout)
    payload["recall"] = {int(k): v for k, v in payload["recall"].items()}
    return ModelResult(**payload)


# -- reporting ---------------------------------------------------------------------------------


def render_markdown(
    results: Sequence[ModelResult], *, ks: Sequence[int], synthetic: bool = False
) -> str:
    recall_headers = [f"recall@{k}" for k in ks]
    headers = ["model", "dim", *recall_headers, "MRR", "doc ms (CPU)", "query ms mean",
               "query ms p95", "load s", "RSS +MB", "peak RSS MB"]  # fmt: skip
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    for r in results:
        cells = [
            r.model_id, str(r.dimensions), *[f"{r.recall[k]:.3f}" for k in ks], f"{r.mrr:.3f}",
            f"{r.doc_ms_mean:.1f}", f"{r.query_ms_mean:.1f}", f"{r.query_ms_p95:.1f}",
            f"{r.load_time_s:.1f}", f"{r.rss_delta_mb:.0f}", f"{r.peak_rss_mb:.0f}",
        ]  # fmt: skip
        lines.append("| " + " | ".join(cells) + " |")
    if synthetic:
        lines.append("")
        lines.append(
            "> SYNTHETIC dataset: this run proves the harness works; it is NOT a basis for "
            "choosing a model."
        )
    return "\n".join(lines)


def _parse_ks(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.split(",") if part.strip())


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="bakeoff_embedding",
        description="Compare embedding models: recall@k, MRR, latency, RAM, load time.",
    )
    parser.add_argument("--models", default=",".join(DEFAULT_MODELS),
                        help="comma-separated model ids ('fake/...' = fake provider)")  # fmt: skip
    parser.add_argument(
        "--dataset", required=True, type=Path, help="YAML corpus + labelled queries"
    )
    parser.add_argument("--ks", default=",".join(map(str, DEFAULT_KS)))
    parser.add_argument("--out", type=Path, help="write the full result as JSON")
    parser.add_argument(
        "--no-isolate",
        action="store_true",
        help="measure all models in this process (RSS unreliable)",
    )
    parser.add_argument("--single", metavar="MODEL", help=argparse.SUPPRESS)
    args = parser.parse_args(list(argv) if argv is not None else None)
    ks = _parse_ks(args.ks)
    try:
        dataset = load_dataset(args.dataset)
    except (OSError, ValueError, KeyError) as exc:
        sys.stderr.write(f"error: cannot use dataset {args.dataset}: {exc}\n")
        return 2
    if args.single:
        try:
            result = measure_model(args.single, dataset, ks)
        except EmbeddingError as exc:
            sys.stderr.write(f"error: {exc}\n")
            return 1
        sys.stdout.write(json.dumps(asdict(result)))
        return 0
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    results: list[ModelResult] = []
    for model_id in models:
        try:
            if args.no_isolate:
                results.append(measure_model(model_id, dataset, ks))
            else:
                results.append(_run_isolated(model_id, args.dataset, ks))
        except EmbeddingError as exc:
            sys.stderr.write(f"error: {exc}\n")
            return 1
    if args.out:
        payload: dict[str, Any] = {
            "dataset": str(args.dataset),
            "synthetic": dataset.synthetic,
            "ks": list(ks),
            "results": [asdict(r) for r in results],
        }
        args.out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    sys.stdout.write(render_markdown(results, ks=ks, synthetic=dataset.synthetic) + "\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
