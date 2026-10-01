"""T-075: stages `embed` + `persist` — idempotent upserts and the two skip keys.

AC: FR-012/AC-001, FR-012/AC-003 (re-ingest never duplicates), BR-005 (citation metadata stays
current even when the content hash does not change, ADR-0012 A4).
"""

from __future__ import annotations

import pytest
from ingest_helpers import FakeConnector, doc, make_ctx, provider, q, run, scalar
from mcp_ingest.pipeline.chunk import ChunkConfig
from mcp_ingest.pipeline.embed import embed_texts, vector_literal

PAGE = "<h1>Payments</h1><p>Retry three times.</p><h2>DLQ</h2><p>Then park the message.</p>"


def counts(dsn: str) -> tuple[int, int]:
    return (
        scalar(dsn, "SELECT count(*) FROM kb.documents"),
        scalar(dsn, "SELECT count(*) FROM kb.chunks"),
    )


def test_FR_012_AC_003_running_the_pipeline_twice_changes_neither_documents_nor_chunks(
    rw_dsn: str,
) -> None:
    docs = [doc("1", content=PAGE), doc("2", content=PAGE.replace("Retry", "Redrive"))]
    first = run(rw_dsn, {"confluence": FakeConnector("confluence", docs)})
    after_first = counts(rw_dsn)
    assert first.sources[0].documents_upserted == 2 and after_first[1] >= 4
    ids = q(rw_dsn, "SELECT id FROM kb.documents ORDER BY source_id")

    second = run(rw_dsn, {"confluence": FakeConnector("confluence", docs)})
    assert counts(rw_dsn) == after_first
    assert second.sources[0].documents_skipped == 2 and second.sources[0].documents_upserted == 0
    assert second.sources[0].chunks_written == 0
    assert (
        q(rw_dsn, "SELECT id FROM kb.documents ORDER BY source_id") == ids
    )  # UPDATE, not new rows


def test_BR_005_a_rename_without_a_content_change_updates_the_citation_metadata(
    rw_dsn: str,
) -> None:
    run(rw_dsn, {"confluence": FakeConnector("confluence", [doc("1", content=PAGE)])})
    chunk_ids = q(rw_dsn, "SELECT id FROM kb.chunks ORDER BY id")

    moved = doc(
        "1", content=PAGE, title="Payments retry (renamed)", container="OPS",
        uri="https://wiki.example.com/spaces/OPS/pages/1/Payments-retry",
    )  # fmt: skip
    report = run(rw_dsn, {"confluence": FakeConnector("confluence", [moved])})

    assert report.sources[0].documents_skipped == 1  # chunks + embeddings were skipped...
    assert q(rw_dsn, "SELECT id FROM kb.chunks ORDER BY id") == chunk_ids  # ...really untouched
    row = q(rw_dsn, "SELECT title, container, source_uri FROM kb.documents")[0]
    assert row == (
        "Payments retry (renamed)",
        "OPS",
        "https://wiki.example.com/spaces/OPS/pages/1/Payments-retry",
    )  # ...but the citation is current
    assert (
        scalar(rw_dsn, "SELECT last_seen_run_id::text FROM kb.documents")
        == report.sources[0].run_id
    )


def test_FR_012_AC_003_a_changed_document_replaces_its_chunks_in_one_step(rw_dsn: str) -> None:
    run(rw_dsn, {"confluence": FakeConnector("confluence", [doc("1", content=PAGE)])})
    changed = PAGE.replace("Retry three times.", "Retry five times, no more.")
    report = run(rw_dsn, {"confluence": FakeConnector("confluence", [doc("1", content=changed)])})
    assert report.sources[0].documents_upserted == 1
    assert counts(rw_dsn)[0] == 1
    text = " ".join(r[0] for r in q(rw_dsn, "SELECT content FROM kb.chunks"))
    assert "five times" in text and "three times" not in text
    assert (
        scalar(
            rw_dsn,
            "SELECT count(*) FROM (SELECT chunk_index FROM kb.chunks GROUP BY 1 HAVING count(*) > 1) x",  # noqa: E501
        )
        == 0
    )


def test_FR_012_AC_003_changing_the_chunk_config_rechunks_instead_of_skipping(rw_dsn: str) -> None:
    long_page = "<h1>Big</h1>" + "".join(
        f"<p>{' '.join(f'w{p}x{i}' for i in range(40))}</p>" for p in range(6)
    )
    connector = FakeConnector("confluence", [doc("1", content=long_page)])
    run(rw_dsn, {"confluence": connector}, ctx=make_ctx(rw_dsn, chunk=ChunkConfig(200, 20)))
    before = scalar(rw_dsn, "SELECT count(*) FROM kb.chunks")
    hash_before = scalar(rw_dsn, "SELECT chunk_config_hash FROM kb.documents")

    report = run(rw_dsn, {"confluence": connector}, ctx=make_ctx(rw_dsn, chunk=ChunkConfig(50, 5)))
    assert report.sources[0].documents_upserted == 1 and report.sources[0].documents_skipped == 0
    assert scalar(rw_dsn, "SELECT chunk_config_hash FROM kb.documents") != hash_before
    assert scalar(rw_dsn, "SELECT count(*) FROM kb.chunks") > before


def test_chunks_carry_model_dimension_heading_and_token_count(rw_dsn: str) -> None:
    run(rw_dsn, {"confluence": FakeConnector("confluence", [doc("1", content=PAGE)])})
    rows = q(
        rw_dsn,
        "SELECT heading_path, token_count, embedding_model, vector_dims(embedding) FROM kb.chunks ORDER BY chunk_index",  # noqa: E501
    )
    assert [r[0] for r in rows] == ["Payments", "Payments > DLQ"]
    assert all(r[1] > 0 and r[2] == "fake/hashed-bow" and r[3] == 1024 for r in rows)


def test_a_tombstoned_document_that_reappears_is_revived(rw_dsn: str) -> None:
    run(rw_dsn, {"confluence": FakeConnector("confluence", [doc("1", content=PAGE)])})
    q(rw_dsn, "UPDATE kb.documents SET deleted_at = now()")
    q(rw_dsn, "DELETE FROM kb.chunks")
    run(rw_dsn, {"confluence": FakeConnector("confluence", [doc("1", content=PAGE)])})
    assert scalar(rw_dsn, "SELECT deleted_at IS NULL FROM kb.documents") is True
    assert scalar(rw_dsn, "SELECT count(*) FROM kb.chunks") >= 2


def test_embedding_batches_and_validates_dimensions() -> None:
    fake = provider(dimensions=16)
    vectors = embed_texts(fake, [f"t{i}" for i in range(10)], batch_size=3)
    assert len(vectors) == 10 and all(len(v) == 16 for v in vectors)

    class Broken:
        model_id, dimensions, max_input_tokens, normalize = "m", 4, 512, True

        def embed_documents(self, texts):
            return [[0.0] * 3 for _ in texts]

        def embed_query(self, text):  # pragma: no cover
            return [0.0] * 4

    with pytest.raises(ValueError, match="dimensions"):
        embed_texts(Broken(), ["a"], 8)  # type: ignore[arg-type]

    class ShortBatch(Broken):
        def embed_documents(self, texts):
            return [[0.0] * 4]

    with pytest.raises(ValueError, match="different number"):
        embed_texts(ShortBatch(), ["a", "b"], 8)  # type: ignore[arg-type]


def test_vector_literal_is_pgvector_text_format() -> None:
    assert vector_literal([1.0, -0.5, 0.25]) == "[1.0,-0.5,0.25]"
