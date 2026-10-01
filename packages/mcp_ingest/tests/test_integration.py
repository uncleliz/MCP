"""T-083: end-to-end — crawl fixtures -> redact -> chunk -> embed (deterministic fake provider) ->
persist, then query back THROUGH `mcp-pgvector` to prove the citation resolves to the original item.

Real PostgreSQL + pgvector (throw-away local cluster; skipped with a reason when unavailable).
AC: FR-012/AC-001, FR-012/AC-002, FR-012/AC-003, FR-011/AC-001, BR-005.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from ingest_helpers import FakeConnector, doc, provider, q, run, scalar
from mcp_common.config import CommonSettings
from mcp_pgvector.client import PgVectorClient
from mcp_pgvector.read_api import PgVectorReadApi
from mcp_pgvector.settings import Settings as PgSettings
from pydantic import SecretStr

AWS = "AKIAIOSFODNN7EXAMPLE"
RETRY_URI = "https://wiki.example.com/spaces/PAY/pages/123456/Payment-retry-policy"
LAG_URI = "https://wiki.example.com/spaces/OPS/pages/777/Kafka-lag-runbook"
README_URI = "https://gitlab.example.com/payments/worker/-/blob/main/README.md"

RETRY_PAGE = (
    "<h1>Payment retry policy</h1><p>Intro.</p>"
    "<h2>Backoff</h2><p>The payment worker retries failed transactions three times with "
    f"exponential backoff. Support key {AWS} must never be indexed.</p>"
    "<h2>DLQ</h2><p>After the last retry the message moves to the dead letter queue.</p>"
)
LAG_PAGE = (
    "<h1>Kafka lag runbook</h1><p>Check the consumer group lag, then rebalance the partitions "
    "when processing is slow.</p>"
)


def corpus() -> dict[str, FakeConnector]:
    return {
        "confluence": FakeConnector(
            "confluence",
            [
                doc("123456", content=RETRY_PAGE, uri=RETRY_URI, title="Payment retry policy", minutes=1),  # noqa: E501
                doc("777", content=LAG_PAGE, uri=LAG_URI, title="Kafka lag runbook", container="OPS", minutes=2),  # noqa: E501
                doc("999", content="<p>salary table</p>", visibility="restricted", title="HR", minutes=3),  # noqa: E501
            ],
        ),
        "gitlab": FakeConnector(
            "gitlab",
            [
                doc("42:blob:README.md", source_type="gitlab", container="payments/worker",
                    content="# Worker\n\nDeploy the worker with the release pipeline tag.",
                    fmt="markdown", uri=README_URI, title="README.md", path="README.md", minutes=4),
                doc("42:blob:.env", source_type="gitlab", container="payments/worker", content="",
                    path=".env", blocked_reason="deny-glob", minutes=5),
            ],
        ),
    }  # fmt: skip


@pytest.fixture
def api_for(rw_dsn: str):
    """A `PgVectorReadApi` over the read-only role, as the real MCP server wires it."""
    ro = rw_dsn.replace("mcp_ingest_rw@", "mcp_query_ro@")
    clients: list[PgVectorClient] = []

    def make(model: str = "fake/hashed-bow") -> PgVectorReadApi:
        settings = PgSettings(dsn=SecretStr(ro))
        client = PgVectorClient(settings, common=CommonSettings())
        clients.append(client)
        return PgVectorReadApi(
            client, provider(model), CommonSettings(), settings,
            now=lambda: datetime.now(UTC),
        )  # fmt: skip

    yield make


@pytest.mark.asyncio
async def test_FR_012_AC_001_ingested_content_is_found_by_semantic_search_with_the_original_url(
    rw_dsn: str, api_for
) -> None:
    report = run(rw_dsn, corpus())
    assert report.status == "success"

    outcome = await api_for().semantic_search(
        query="payment worker retries failed transactions with exponential backoff", top_k=5
    )
    result = outcome.result
    assert result.status.value == "ok"
    top = result.items[0]
    assert top["source_uri"] == RETRY_URI and top["source_type"] == "confluence"  # BR-005
    assert top["container"] == "PAY" and top["title"] == "Payment retry policy"
    assert top["heading_path"] == "Payment retry policy > Backoff"
    assert result.citations[0].uri == RETRY_URI, "the citation is the ORIGINAL url, not a chunk id"
    assert result.meta.data_freshness is not None
    assert result.meta.data_freshness.embedding_model == "fake/hashed-bow"

    gitlab = await api_for().semantic_search(
        query="deploy the worker with the release pipeline tag", source_types=["gitlab"]
    )
    assert gitlab.result.items[0]["source_uri"] == README_URI


@pytest.mark.asyncio
async def test_citation_resolves_back_to_the_source_item_via_get_document(
    rw_dsn: str, api_for
) -> None:
    run(rw_dsn, corpus())
    api = api_for()
    found = await api.semantic_search(query="kafka consumer group lag rebalance partitions")
    assert found.result.items[0]["source_uri"] == LAG_URI
    fetched = await api.get_document(source_uri=LAG_URI)
    item = fetched.result.items[0]
    assert item["source_uri"] == LAG_URI and item["source_id"] == "777"
    assert "consumer group lag" in str(item)


@pytest.mark.asyncio
async def test_R4_a_secret_and_restricted_content_are_not_retrievable(rw_dsn: str, api_for) -> None:
    run(rw_dsn, corpus())
    api = api_for()
    for query in ("support key must never be indexed", AWS, "salary table"):
        result = (await api.semantic_search(query=query, top_k=10, min_similarity=0.0)).result
        text = str(result.items)
        assert AWS not in text and "salary table" not in text
    assert q(rw_dsn, "SELECT source_id FROM kb.documents ORDER BY 1") == [
        ("123456",),
        ("42:blob:README.md",),
        ("777",),
    ]
    failures = {r[0] for r in q(rw_dsn, "SELECT source_id FROM kb.ingest_failures")}
    assert failures == {"999", "42:blob:.env"}


def test_FR_012_AC_003_re_ingest_does_not_multiply_documents_or_chunks(rw_dsn: str) -> None:
    run(rw_dsn, corpus())
    before = (
        scalar(rw_dsn, "SELECT count(*) FROM kb.documents"),
        scalar(rw_dsn, "SELECT count(*) FROM kb.chunks"),
    )
    for mode in ("incremental", "full", "full"):
        report = run(rw_dsn, corpus(), mode=mode)
        assert report.status == "success"
    after = (
        scalar(rw_dsn, "SELECT count(*) FROM kb.documents"),
        scalar(rw_dsn, "SELECT count(*) FROM kb.chunks"),
    )
    assert before == after and before[0] == 3


def test_FR_012_AC_002_an_unreachable_source_leaves_the_other_one_ingested(rw_dsn: str) -> None:
    connectors = corpus()
    connectors["gitlab"] = FakeConnector("gitlab", connectors["gitlab"].documents, die_after=0)
    report = run(rw_dsn, connectors)
    assert report.status == "partial" and report.exit_code == 1
    assert scalar(rw_dsn, "SELECT count(*) FROM kb.documents WHERE source_type = 'confluence'") == 2
    assert scalar(rw_dsn, "SELECT count(*) FROM kb.documents WHERE source_type = 'gitlab'") == 0
    assert (
        scalar(rw_dsn, "SELECT count(*) FROM kb.ingest_source_state WHERE source_type = 'gitlab'")
        == 0
    )
    # the source comes back: the same run now fills it in and nothing of confluence is duplicated
    healed = run(rw_dsn, corpus())
    assert healed.status == "success"
    assert scalar(rw_dsn, "SELECT count(*) FROM kb.documents") == 3


@pytest.mark.asyncio
async def test_FR_011_AC_001_a_document_removed_at_the_source_disappears_from_search(
    rw_dsn: str, api_for
) -> None:
    connectors = corpus()
    run(rw_dsn, connectors, mode="full")
    api = api_for()
    assert (await api.semantic_search(query="kafka consumer group lag")).result.items[0][
        "source_uri"
    ] == LAG_URI

    gone = corpus()
    gone["confluence"] = FakeConnector("confluence", [connectors["confluence"].documents[0]])
    gone["gitlab"] = FakeConnector("gitlab", connectors["gitlab"].documents)
    report = run(rw_dsn, gone, mode="full")
    by = {s.source_type: s for s in report.sources}
    # 1 of 3 live confluence documents left: the safety valve refuses to tombstone (R18)
    assert by["confluence"].documents_tombstoned == 0 and by["confluence"].status == "partial"
    assert (
        scalar(
            rw_dsn,
            "SELECT count(*) FROM kb.documents WHERE source_type='confluence' AND deleted_at IS NULL",  # noqa: E501
        )
        == 2
    )

    # with enough of the corpus still visible (>= 80%) the removal goes through
    many = FakeConnector(
        "confluence",
        [doc(str(100 + i), content=f"<p>page {i} unique{i}</p>", minutes=10 + i) for i in range(9)],
    )
    run(rw_dsn, {"confluence": many}, mode="full")
    survivors = FakeConnector("confluence", many.documents[:8])
    report = run(rw_dsn, {"confluence": survivors}, mode="full")
    assert report.sources[0].documents_tombstoned == 1
    result = (
        await api.semantic_search(query="page 8 unique8", top_k=20, min_similarity=0.0)
    ).result
    assert "108" not in {i["source_id"] for i in result.items}, "tombstoned content is not served"
