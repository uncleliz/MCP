"""T-097 — local offline reranker + transparent RRF-only fallback (ADR-0020, L-002)."""

from __future__ import annotations

from mcp_knowledge.rerank.local import DEFAULT_RERANKER_MODEL, Reranker


class FakeCrossEncoder:
    """A test double for a loaded cross-encoder: scores by passage length (deterministic)."""

    def __init__(self, model_path: str) -> None:
        self.model_path = model_path
        self.load_count = 1
        self.predict_calls = 0

    def predict(self, pairs: list[tuple[str, str]]) -> list[float]:
        self.predict_calls += 1
        return [float(len(passage)) for _query, passage in pairs]


def test_default_model_is_bge_reranker_v2_m3() -> None:
    assert DEFAULT_RERANKER_MODEL == "BAAI/bge-reranker-v2-m3"


def test_enabled_reranker_reorders_by_cross_encoder_score() -> None:
    made: list[FakeCrossEncoder] = []

    def factory(path: str) -> FakeCrossEncoder:
        enc = FakeCrossEncoder(path)
        made.append(enc)
        return enc

    r = Reranker(enabled=True, model_factory=factory, require_offline=False)
    passages = ["short", "a much longer passage", "mid len"]
    outcome = r.rerank("q", passages)
    assert outcome.status == "enabled"
    assert outcome.reason is None
    # longest passage first (highest fake score), stable tie-break by index otherwise.
    assert outcome.order == [1, 2, 0]


def test_fallback_rrf_only_when_weights_absent_does_not_raise() -> None:
    # TC-080: weights unloadable -> RRF-only fallback, status=disabled, no exception.
    def failing_factory(path: str) -> object:
        raise FileNotFoundError("weights not on disk")

    r = Reranker(enabled=True, model_factory=failing_factory, require_offline=False)
    outcome = r.rerank("q", ["a", "b", "c"])
    assert outcome.status == "disabled"
    assert outcome.order == [0, 1, 2]  # input (RRF) order preserved
    assert outcome.reason  # a reason is reported (not silent)


def test_disabled_by_configuration_is_transparent() -> None:
    r = Reranker(enabled=False)
    outcome = r.rerank("q", ["a", "b"])
    assert outcome.status == "disabled"
    assert "configuration" in (outcome.reason or "")
    assert outcome.order == [0, 1]


def test_scoring_failure_falls_back_transparently() -> None:
    class Exploding:
        def predict(self, pairs: list[tuple[str, str]]) -> list[float]:
            raise RuntimeError("cuda oom")

    r = Reranker(enabled=True, model_factory=lambda p: Exploding(), require_offline=False)
    outcome = r.rerank("q", ["a", "b"])
    assert outcome.status == "disabled"
    assert "scoring failed" in (outcome.reason or "")


def test_require_offline_refuses_non_offline_default_load(monkeypatch) -> None:
    # With no factory and HF offline NOT set, a load must refuse rather than risk egress.
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    monkeypatch.delenv("TRANSFORMERS_OFFLINE", raising=False)
    r = Reranker(enabled=True, require_offline=True)  # default factory, offline required
    outcome = r.rerank("q", ["a"])
    assert outcome.status == "disabled"
    assert "HF_HUB_OFFLINE" in (outcome.reason or "")


def test_empty_passages_returns_enabled_empty_order() -> None:
    r = Reranker(enabled=True, model_factory=lambda p: FakeCrossEncoder(p), require_offline=False)
    outcome = r.rerank("q", [])
    assert outcome.order == [] and outcome.status == "enabled"


def test_model_loaded_lazily_once_per_reranker() -> None:
    loads = {"n": 0}

    def factory(path: str) -> FakeCrossEncoder:
        loads["n"] += 1
        return FakeCrossEncoder(path)

    r = Reranker(enabled=True, model_factory=factory, require_offline=False)
    r.rerank("q", ["a"])
    r.rerank("q", ["b"])
    r.rerank("q", ["c"])
    assert loads["n"] == 1  # loaded once, reused
