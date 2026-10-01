"""T-080: `reembed --model ...`.

AC: FR-012/AC-003, FR-011/AC-001. Switching model must not require a re-crawl and must leave a store
that `mcp-pgvector` accepts (its startup check compares model + dimension with the stored data).
"""

from __future__ import annotations

import psycopg
import pytest
from ingest_helpers import FakeConnector, assert_valid, doc, provider, q, run, scalar
from mcp_ingest.commands.reembed import reembed
from mcp_ingest.pipeline.run import ConfigurationError

PAGE = "<h1>Payments</h1><p>Retry three times.</p><h2>DLQ</h2><p>Then park the message.</p>"
NEW = "fake/model-b"


@pytest.fixture
def loaded(rw_dsn: str) -> str:
    docs = [doc(str(i), minutes=i, content=PAGE.replace("Retry", f"Retry{i}")) for i in range(6)]
    run(rw_dsn, {"confluence": FakeConnector("confluence", docs)})
    assert scalar(rw_dsn, "SELECT count(*) FROM kb.chunks") == 12
    return rw_dsn


def models(dsn: str) -> set[str]:
    return {r[0] for r in q(dsn, "SELECT DISTINCT embedding_model FROM kb.chunks")}


def test_FR_012_AC_003_every_chunk_gets_the_new_model_and_documents_are_untouched(
    loaded: str,
) -> None:
    documents_before = q(
        loaded, "SELECT id, ingested_at, content_hash, title FROM kb.documents ORDER BY id"
    )
    embedded_before = scalar(loaded, "SELECT min(embedded_at) FROM kb.chunks")
    with psycopg.connect(loaded, autocommit=True) as conn:
        report = reembed(conn, provider(NEW), sources=["confluence"], batch_size=5)
    assert models(loaded) == {NEW}
    assert report.mode == "reembed" and report.embedding_model == NEW and report.exit_code == 0
    assert report.sources[0].chunks_written == 12 and report.sources[0].documents_seen == 6
    assert_valid(report.model_dump(mode="json"), schema="IngestRunReport")
    assert (
        q(loaded, "SELECT id, ingested_at, content_hash, title FROM kb.documents ORDER BY id")
        == documents_before
    )
    assert scalar(loaded, "SELECT min(embedded_at) FROM kb.chunks") > embedded_before


def test_FR_012_AC_003_an_interrupted_reembed_resumes_where_it_stopped(loaded: str) -> None:
    with psycopg.connect(loaded, autocommit=True) as conn:
        reembed(conn, provider(NEW), sources=["confluence"], batch_size=5, max_batches=1)
        assert (
            scalar(loaded, "SELECT count(*) FROM kb.chunks WHERE embedding_model = %s", (NEW,)) == 5
        )
        assert len(models(loaded)) == 2  # half-migrated, which mcp-pgvector refuses to serve
        report = reembed(conn, provider(NEW), sources=["confluence"], batch_size=5)
    assert report.sources[0].chunks_written == 7, "only the remaining chunks are re-embedded"
    assert models(loaded) == {NEW}


def test_reembed_of_one_source_leaves_the_other_alone(loaded: str) -> None:
    run(
        loaded,
        {
            "gitlab": FakeConnector(
                "gitlab", [doc("42:mr:1", source_type="gitlab", container="p/a")]
            )
        },
    )
    with psycopg.connect(loaded, autocommit=True) as conn:
        reembed(conn, provider(NEW), sources=["gitlab"])
    assert q(
        loaded,
        "SELECT DISTINCT d.source_type FROM kb.chunks c JOIN kb.documents d ON d.id = c.document_id WHERE c.embedding_model = %s",  # noqa: E501
        (NEW,),
    ) == [("gitlab",)]


def test_a_second_reembed_with_the_same_model_is_a_noop(loaded: str) -> None:
    with psycopg.connect(loaded, autocommit=True) as conn:
        reembed(conn, provider(NEW), sources=["confluence"])
        report = reembed(conn, provider(NEW), sources=["confluence"])
    assert report.sources[0].chunks_written == 0 and report.status == "success"


def test_reembed_refuses_a_model_with_another_dimension(loaded: str) -> None:
    with (
        psycopg.connect(loaded, autocommit=True) as conn,
        pytest.raises(ConfigurationError, match="migration"),
    ):
        reembed(conn, provider(NEW, dimensions=768), sources=["confluence"])
    assert models(loaded) == {"fake/hashed-bow"}


def test_a_failing_provider_reports_the_source_failed_and_keeps_progress(loaded: str) -> None:
    class Flaky:
        model_id, dimensions, max_input_tokens, normalize = NEW, 1024, 512, True
        calls = 0

        def embed_documents(self, texts):
            Flaky.calls += 1
            if Flaky.calls > 1:
                raise TimeoutError("down")
            return provider(NEW).embed_documents(texts)

        def embed_query(self, text):  # pragma: no cover
            raise AssertionError

    with psycopg.connect(loaded, autocommit=True) as conn:
        report = reembed(conn, Flaky(), sources=["confluence"], batch_size=5)  # type: ignore[arg-type]
    assert report.status == "partial" and report.exit_code == 1
    assert report.sources[0].errors[0].stage == "embed"
    assert scalar(loaded, "SELECT count(*) FROM kb.chunks WHERE embedding_model = %s", (NEW,)) == 5


@pytest.mark.asyncio
async def test_after_reembed_mcp_pgvector_accepts_the_store_with_the_new_model(loaded: str) -> None:
    from mcp_common.config import CommonSettings
    from mcp_pgvector.client import PgVectorClient
    from mcp_pgvector.settings import Settings
    from pydantic import SecretStr

    ro = loaded.replace("mcp_ingest_rw@", "mcp_query_ro@")

    async def check(model: str):
        client = PgVectorClient(Settings(dsn=SecretStr(ro)), common=CommonSettings())
        try:
            return await client.verify_credentials(provider(model))
        finally:
            await client.aclose()

    assert (await check(NEW)).fatal  # before: stored chunks are another model
    with psycopg.connect(loaded, autocommit=True) as conn:
        reembed(conn, provider(NEW), sources=["confluence"])
    report = await check(NEW)
    assert report.ok and not report.fatal, report.reasons
    assert (await check("fake/hashed-bow")).fatal  # and the old model is now the mismatch
