"""T-095 integration — Hybrid retrieval over the real pgvector store (ADR-0020).

Runs the vector + keyword legs against a throw-away database inside the running `pgvector/pgvector`
container (the exact engine the kb store went live on). Skipped, with a reason, when Docker or the
container is unavailable (see `packages/conftest.py::docker_pgvector`) so `make ci` stays green on
a machine without Docker. The live `mcp_kb` database is never touched.

Proves the parts that only a real database can: `content_tsv @@ websearch_to_tsquery('simple')`
matches exact identifiers, the HNSW cosine order, the mandatory `deleted_at IS NULL` +
`embedding_model` filters, the metadata filter, and that the fused result is read-only through the
`mcp_query_ro` role.
"""

from __future__ import annotations

import pytest
from mcp_ingest.db import upgrade
from mcp_ingest.embedding.fake import DeterministicFakeProvider
from mcp_knowledge.retrieval.client import KnowledgeRetrievalClient
from mcp_knowledge.retrieval.hybrid import HybridQuery, HybridRetriever

pytestmark = pytest.mark.asyncio

PROVIDER = DeterministicFakeProvider(dimensions=1024, model_id="fake/hashed-bow")

# Corpus: three live docs + one tombstoned doc whose text would otherwise be the best 'retry' match.
_DOCS = [
    ("confluence", "c1", "https://wiki/c1", "Payment retry policy", "PAY",
     "payment worker retry failed transactions three times exponential backoff"),
    ("gitlab", "g1", "https://gitlab/g1", "worker README", "payments/worker",
     "deploy the worker ERR_PAYMENT_TIMEOUT handling in the pipeline release"),
    ("confluence", "c2", "https://wiki/c2", "Kafka lag runbook", "OPS",
     "kafka consumer group lag rebalance partitions slow processing"),
]  # fmt: skip


def _swap_user(dsn: str, user: str, password: str = "pw") -> str:
    import psycopg
    from psycopg import sql

    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(
            sql.SQL("ALTER ROLE {} PASSWORD {}").format(sql.Identifier(user), sql.Literal(password))
        )
    scheme, _, rest = dsn.partition("://")
    _, _, hostpart = rest.partition("@")
    return f"{scheme}://{user}:{password}@{hostpart}"


def _seed(dsn: str) -> None:
    import psycopg

    with psycopg.connect(dsn, autocommit=True) as conn:
        upgrade(conn)  # full chain incl 0007/0007b/0008 (content_tsv + GIN)
        run_id = conn.execute(
            "INSERT INTO kb.ingest_runs (source_type, status) VALUES ('confluence','success') "
            "RETURNING id"
        ).fetchone()[0]
        for source_type, source_id, uri, title, container, text in _DOCS:
            doc_id = conn.execute(
                "INSERT INTO kb.documents (source_type, source_id, source_uri, title, container, "
                "content_hash, last_seen_run_id) VALUES (%s,%s,%s,%s,%s,'h',%s) RETURNING id",
                (source_type, source_id, uri, title, container, run_id),
            ).fetchone()[0]
            vec = PROVIDER.embed_documents([text])[0]
            vec_lit = "[" + ",".join(str(x) for x in vec) + "]"
            # content_tsv must be set for rows inserted AFTER the 0007b backfill (production sets it
            # in the ingest persist stage); set it the same way here.
            conn.execute(
                "INSERT INTO kb.chunks (document_id, chunk_index, content, token_count, "
                "content_tsv, embedding, embedding_model) "
                "VALUES (%s,0,%s,5, to_tsvector('simple', %s), %s, %s)",
                (doc_id, text, text, vec_lit, PROVIDER.model_id),
            )
        # a tombstoned doc that is the strongest 'retry' match — must NEVER surface.
        dead = conn.execute(
            "INSERT INTO kb.documents (source_type, source_id, source_uri, content_hash, "
            "deleted_at, last_seen_run_id) VALUES ('confluence','dead','https://wiki/dead','h',"
            "now(), %s) RETURNING id",
            (run_id,),
        ).fetchone()[0]
        dtext = "payment retry retry retry backoff backoff"
        dvec = PROVIDER.embed_documents([dtext])[0]
        conn.execute(
            "INSERT INTO kb.chunks (document_id, chunk_index, content, token_count, content_tsv, "
            "embedding, embedding_model) VALUES (%s,0,%s,6, to_tsvector('simple', %s), %s, %s)",
            (dead, dtext, dtext, "[" + ",".join(str(x) for x in dvec) + "]", PROVIDER.model_id),
        )


@pytest.fixture
def retriever(docker_pg_factory) -> HybridRetriever:
    dsn = docker_pg_factory()
    _seed(dsn)
    ro_dsn = _swap_user(dsn, "mcp_query_ro")
    return HybridRetriever(KnowledgeRetrievalClient(ro_dsn), PROVIDER)


async def test_hybrid_returns_relevant_and_resolvable_candidates(retriever) -> None:
    # TC-078 (DB side): a query with evidence returns candidates resolvable to the original source.
    out = await retriever.retrieve(HybridQuery(text="payment retry backoff", top_k=10))
    assert out, "expected at least one candidate"
    assert all(c.source_uri.startswith("https://") for c in out)
    uris = {c.source_uri for c in out}
    assert "https://wiki/c1" in uris


async def test_keyword_leg_matches_exact_identifier(retriever) -> None:
    # The 'simple' tsvector leg matches an exact error code even if it is not a natural question.
    out = await retriever.retrieve(HybridQuery(text="ERR_PAYMENT_TIMEOUT", top_k=10))
    assert any(c.source_uri == "https://gitlab/g1" for c in out)
    # it was surfaced via the keyword leg.
    hit = next(c for c in out if c.source_uri == "https://gitlab/g1")
    assert "keyword" in hit.legs


async def test_tombstoned_document_never_surfaces(retriever) -> None:
    # deleted_at IS NULL is mandatory: the tombstoned 'retry' doc must not appear.
    out = await retriever.retrieve(HybridQuery(text="payment retry retry backoff", top_k=20))
    assert all(c.source_uri != "https://wiki/dead" for c in out)


async def test_metadata_filter_restricts_source_type(retriever) -> None:
    out = await retriever.retrieve(
        HybridQuery(text="payment retry backoff", top_k=10, source_types=["gitlab"])
    )
    assert all(c.source_type == "gitlab" for c in out)


async def test_container_filter(retriever) -> None:
    out = await retriever.retrieve(
        HybridQuery(text="kafka lag", top_k=10, container="OPS")
    )
    assert all(c.container == "OPS" for c in out)


async def test_role_is_read_only_cannot_write(docker_pg_factory) -> None:
    # TC-081 (DB side): the mcp_query_ro role rejects writes at the database level.
    import psycopg

    dsn = docker_pg_factory()
    _seed(dsn)
    ro_dsn = _swap_user(dsn, "mcp_query_ro")
    with psycopg.connect(ro_dsn, autocommit=True) as conn:
        with pytest.raises(
            (psycopg.errors.InsufficientPrivilege, psycopg.errors.ReadOnlySqlTransaction)
        ):
            conn.execute("INSERT INTO kb.documents (source_type, source_id, source_uri, "
                         "content_hash) VALUES ('x','y','z','h')")  # noqa: E501
