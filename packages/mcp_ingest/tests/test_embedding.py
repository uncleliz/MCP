"""T-058: `EmbeddingProvider` port + adapters (FR-011/AC-001, FR-012/AC-001, ADR-0010).

Every adapter is driven through the *same* contract test; no test downloads a model or touches
the network (a stub model object and `httpx.MockTransport` stand in for the real backends).
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from mcp_ingest.embedding import (
    EmbeddingError,
    EmbeddingModelMismatchError,
    EmbeddingSettings,
    build_provider,
    validate_stored_embeddings,
)
from mcp_ingest.embedding.fake import DeterministicFakeProvider
from mcp_ingest.embedding.http import HttpEmbeddingProvider
from mcp_ingest.embedding.local import LocalSentenceTransformerProvider
from mcp_ingest.ports import EmbeddingProvider
from pydantic import SecretStr

DIM = 8


class StubSentenceTransformer:
    """Quacks like `sentence_transformers.SentenceTransformer` (hash-based, deterministic)."""

    def __init__(self, dim: int = DIM) -> None:
        self.dim = dim
        self.max_seq_length = 512
        self.encoded: list[list[str]] = []
        self.kwargs: list[dict[str, Any]] = []

    def get_sentence_embedding_dimension(self) -> int:
        return self.dim

    def encode(self, texts: list[str], **kwargs: Any) -> list[list[float]]:
        self.encoded.append(list(texts))
        self.kwargs.append(kwargs)
        out = []
        for text in texts:
            vec = [0.0] * self.dim
            for index, char in enumerate(text):
                vec[(index + ord(char)) % self.dim] += 1.0
            out.append(vec)
        return out


def _http_handler(dim: int = DIM, *, shuffle: bool = False, calls: list[Any] | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if calls is not None:
            calls.append((request, body))
        inputs = body["input"]
        data = []
        for index, text in enumerate(inputs):
            vec = [0.0] * dim
            for pos, char in enumerate(text):
                vec[(pos + ord(char)) % dim] += 1.0
            data.append({"index": index, "embedding": vec, "object": "embedding"})
        if shuffle:
            data.reverse()
        return httpx.Response(200, json={"data": data, "model": body["model"]})

    return handler


def make_local(model_id: str = "BAAI/bge-m3", dim: int = DIM, **kw: Any):
    stub = StubSentenceTransformer(dim)
    settings = EmbeddingSettings(model=model_id, dimensions=dim, **kw)
    return LocalSentenceTransformerProvider(settings, model_factory=lambda *_a, **_k: stub), stub


def make_http(model_id: str = "BAAI/bge-m3", dim: int = DIM, **kw: Any):
    calls: list[Any] = []
    settings = EmbeddingSettings(
        provider="http", model=model_id, dimensions=dim, url="http://emb.test/v1",
        api_key=SecretStr("k"), **kw,
    )  # fmt: skip
    transport = httpx.MockTransport(_http_handler(dim, shuffle=True, calls=calls))
    return HttpEmbeddingProvider(settings, transport=transport), calls


def make_fake(model_id: str = "fake/hash", dim: int = DIM, **_kw: Any):
    return DeterministicFakeProvider(dimensions=dim, model_id=model_id), None


FACTORIES: list[Callable[..., Any]] = [make_local, make_http, make_fake]


@pytest.fixture(params=FACTORIES, ids=["local", "http", "fake"])
def provider(request: pytest.FixtureRequest) -> EmbeddingProvider:
    return request.param()[0]


def _norm(vec: list[float]) -> float:
    return math.sqrt(sum(x * x for x in vec))


def test_FR_012_AC_001_adapters_satisfy_the_port(provider: EmbeddingProvider) -> None:
    assert isinstance(provider, EmbeddingProvider)
    assert provider.dimensions == DIM
    assert provider.model_id
    assert provider.max_input_tokens > 0
    assert provider.normalize is True


def test_FR_011_AC_001_documents_come_back_in_input_order_with_declared_dimensions(
    provider: EmbeddingProvider,
) -> None:
    texts = ["alpha", "beta beta", "gamma gamma gamma"]
    vectors = provider.embed_documents(texts)
    assert len(vectors) == 3 and all(len(v) == DIM for v in vectors)
    single = [provider.embed_documents([t])[0] for t in texts]
    for batched, alone in zip(vectors, single, strict=True):
        assert batched == pytest.approx(alone)


def test_vectors_are_unit_length_when_normalize_is_true(provider: EmbeddingProvider) -> None:
    for vec in [*provider.embed_documents(["một hai ba", "x"]), provider.embed_query("câu hỏi")]:
        assert _norm(vec) == pytest.approx(1.0, abs=1e-6)


def test_embedding_is_deterministic(provider: EmbeddingProvider) -> None:
    assert provider.embed_query("xin chào") == provider.embed_query("xin chào")


def test_empty_batch_returns_empty_list_without_calling_the_backend(
    provider: EmbeddingProvider,
) -> None:
    assert provider.embed_documents([]) == []


def test_local_loads_the_model_lazily_and_once() -> None:
    loads: list[str] = []
    stub = StubSentenceTransformer()

    def factory(name: str, **_kw: Any) -> StubSentenceTransformer:
        loads.append(name)
        return stub

    provider = LocalSentenceTransformerProvider(
        EmbeddingSettings(model="BAAI/bge-m3", dimensions=DIM), model_factory=factory
    )
    assert loads == []  # constructing the provider must not load ~2 GB of weights
    provider.embed_query("a")
    provider.embed_documents(["b", "c"])
    provider.embed_query("d")
    assert loads == ["BAAI/bge-m3"]


def test_local_rejects_a_model_whose_dimension_differs_from_config() -> None:
    provider = LocalSentenceTransformerProvider(
        EmbeddingSettings(model="BAAI/bge-m3", dimensions=DIM),
        model_factory=lambda *_a, **_k: StubSentenceTransformer(dim=DIM + 1),
    )
    with pytest.raises(EmbeddingError, match="dimension"):
        provider.embed_query("x")


def test_local_clamps_max_seq_length_to_max_input_tokens() -> None:
    provider, stub = make_local(max_input_tokens=128)
    provider.embed_query("x")
    assert stub.max_seq_length == 128


def test_e5_models_get_query_and_passage_prefixes_but_bge_does_not() -> None:
    e5, e5_stub = make_local("intfloat/multilingual-e5-large")
    e5.embed_query("hỏi")
    e5.embed_documents(["đáp"])
    assert e5_stub.encoded == [["query: hỏi"], ["passage: đáp"]]
    bge, bge_stub = make_local("BAAI/bge-m3")
    bge.embed_query("hỏi")
    bge.embed_documents(["đáp"])
    assert bge_stub.encoded == [["hỏi"], ["đáp"]]


def test_http_sends_openai_compatible_request_with_bearer_and_prefix() -> None:
    provider, calls = make_http("intfloat/multilingual-e5-large")
    provider.embed_query("q")
    request, body = calls[0]
    assert str(request.url) == "http://emb.test/v1/embeddings"
    assert request.headers["authorization"] == "Bearer k"
    assert body == {"model": "intfloat/multilingual-e5-large", "input": ["query: q"]}


def test_http_batches_by_batch_size() -> None:
    provider, calls = make_http(batch_size=2)
    assert len(provider.embed_documents(["a", "b", "c", "d", "e"])) == 5
    assert [len(body["input"]) for _r, body in calls] == [2, 2, 1]


def test_http_rejects_a_wrong_dimension_response() -> None:
    settings = EmbeddingSettings(provider="http", model="m", dimensions=DIM, url="http://e/v1")
    provider = HttpEmbeddingProvider(
        settings, transport=httpx.MockTransport(_http_handler(DIM + 3))
    )
    with pytest.raises(EmbeddingError, match="dimension"):
        provider.embed_query("x")


def test_http_rejects_a_response_with_the_wrong_item_count() -> None:
    def handler(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": []})

    settings = EmbeddingSettings(provider="http", model="m", dimensions=DIM, url="http://e/v1")
    provider = HttpEmbeddingProvider(settings, transport=httpx.MockTransport(handler))
    with pytest.raises(EmbeddingError, match="1"):
        provider.embed_documents(["x"])


def test_http_retries_a_transient_5xx_then_succeeds() -> None:
    attempts: list[int] = []
    good = _http_handler()

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) == 1:
            return httpx.Response(503, text="busy")
        return good(request)

    sleeps: list[float] = []
    settings = EmbeddingSettings(provider="http", model="m", dimensions=DIM, url="http://e/v1")
    provider = HttpEmbeddingProvider(
        settings, transport=httpx.MockTransport(handler), sleep=sleeps.append
    )
    assert len(provider.embed_query("x")) == DIM
    assert len(attempts) == 2 and len(sleeps) == 1


def test_http_gives_up_after_max_retries_without_leaking_the_api_key() -> None:
    def handler(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    settings = EmbeddingSettings(
        provider="http", model="m", dimensions=DIM, url="http://e/v1", api_key=SecretStr("sekret")
    )
    provider = HttpEmbeddingProvider(
        settings, transport=httpx.MockTransport(handler), sleep=lambda _s: None
    )
    with pytest.raises(EmbeddingError) as exc:
        provider.embed_query("x")
    assert "sekret" not in str(exc.value)


def test_http_client_error_is_not_retried() -> None:
    attempts: list[int] = []

    def handler(_r: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(401, text="no")

    settings = EmbeddingSettings(provider="http", model="m", dimensions=DIM, url="http://e/v1")
    provider = HttpEmbeddingProvider(settings, transport=httpx.MockTransport(handler))
    with pytest.raises(EmbeddingError, match="401"):
        provider.embed_query("x")
    assert len(attempts) == 1


def test_http_network_error_becomes_embedding_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    settings = EmbeddingSettings(provider="http", model="m", dimensions=DIM, url="http://e/v1")
    provider = HttpEmbeddingProvider(
        settings, transport=httpx.MockTransport(handler), sleep=lambda _s: None
    )
    with pytest.raises(EmbeddingError, match="unreachable"):
        provider.embed_query("x")


def test_http_requires_a_url() -> None:
    with pytest.raises(EmbeddingError, match="MCP_INGEST_EMBEDDING_URL"):
        HttpEmbeddingProvider(EmbeddingSettings(provider="http", model="m", dimensions=DIM))


def test_fake_provider_ranks_overlapping_text_closer() -> None:
    fake = DeterministicFakeProvider()
    assert fake.dimensions == 1024
    near = fake.embed_documents(["payment retry backoff policy", "kafka consumer lag runbook"])
    query = fake.embed_query("payment retry policy")
    sims = [sum(a * b for a, b in zip(query, vec, strict=True)) for vec in near]
    assert sims[0] > sims[1]


def test_fake_provider_handles_empty_text() -> None:
    vec = DeterministicFakeProvider(dimensions=DIM).embed_query("")
    assert len(vec) == DIM and _norm(vec) == pytest.approx(1.0)


def test_build_provider_selects_adapter_from_settings() -> None:
    http = build_provider(
        EmbeddingSettings(provider="http", model="m", dimensions=DIM, url="http://e/v1")
    )
    assert isinstance(http, HttpEmbeddingProvider)
    local = build_provider(EmbeddingSettings(provider="local", model="m", dimensions=DIM))
    assert isinstance(local, LocalSentenceTransformerProvider)


def test_default_settings_are_the_provisional_1024d_choice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for var in ("MODEL", "PROVIDER", "DIMENSIONS", "NORMALIZE"):
        monkeypatch.delenv(f"MCP_INGEST_EMBEDDING_{var}", raising=False)
    settings = EmbeddingSettings()
    assert (settings.provider, settings.dimensions, settings.normalize) == ("local", 1024, True)
    assert settings.model in {"BAAI/bge-m3", "intfloat/multilingual-e5-large"}


def test_settings_read_the_shared_env_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_INGEST_EMBEDDING_MODEL", "intfloat/multilingual-e5-large")
    monkeypatch.setenv("MCP_INGEST_EMBEDDING_PROVIDER", "http")
    monkeypatch.setenv("MCP_INGEST_EMBEDDING_URL", "http://e/v1")
    settings = EmbeddingSettings()
    assert settings.model == "intfloat/multilingual-e5-large" and settings.provider == "http"


# -- stored-data validation (ADR-0010: "model/dimension khác dữ liệu → yêu cầu re-embed") --------


def test_validate_passes_for_empty_store_and_matching_model() -> None:
    provider = DeterministicFakeProvider(dimensions=DIM, model_id="m1")
    validate_stored_embeddings(provider, stored_models=set(), stored_dimensions=None)
    validate_stored_embeddings(provider, stored_models={"m1"}, stored_dimensions=DIM)


def test_validate_rejects_a_different_stored_model_and_says_reembed() -> None:
    provider = DeterministicFakeProvider(dimensions=DIM, model_id="m2")
    with pytest.raises(EmbeddingModelMismatchError, match="reembed") as exc:
        validate_stored_embeddings(provider, stored_models={"m1"}, stored_dimensions=DIM)
    assert "m1" in str(exc.value) and "m2" in str(exc.value)


def test_validate_rejects_a_mixed_store_even_if_it_contains_the_configured_model() -> None:
    provider = DeterministicFakeProvider(dimensions=DIM, model_id="m1")
    with pytest.raises(EmbeddingModelMismatchError, match="reembed"):
        validate_stored_embeddings(provider, stored_models={"m1", "m0"}, stored_dimensions=DIM)


def test_validate_rejects_a_dimension_mismatch() -> None:
    provider = DeterministicFakeProvider(dimensions=DIM, model_id="m1")
    with pytest.raises(EmbeddingModelMismatchError, match="dimension"):
        validate_stored_embeddings(provider, stored_models={"m1"}, stored_dimensions=1024)
