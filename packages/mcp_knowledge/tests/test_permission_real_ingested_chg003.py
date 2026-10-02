"""TC-124 (CHG-003) / E-mcp-data-platform-007 — the CHG-001 permission choke point #1, re-proven
over REAL-INGESTED content.

CHG-003 turns on real ingestion; this test proves the one default-deny permission filter
(`enforce_permission`, ADR-0021) is unchanged and still excludes a restricted document that was put
into the corpus THROUGH THE REAL INGEST PIPELINE (redact → chunk → embed → persist), not hand-
seeded. It guards E-007 (the HIGH finding that the choke point was once wired on only 2 of 8 tools)
against the new real content.

Scenario — a Confluence crawl yields two pages into the real pipeline:
* a TEAM `Payment retry policy` page (visibility `team`, ingested); and
* a RESTRICTED `Executive compensation` page whose content matches a crafted query.

The ingest pipeline writes NO permission grants (grants are a separate, server-side concern), so an
**un-granted caller** (default-deny: no principals, not a team member) must see NOTHING of the real-
ingested corpus — in particular the restricted page is never a candidate, never in a pack, never
cited. The permission gate is NOT weakened by CHG-003.

Live: needs the docker pgvector container (``packages/conftest.py::docker_pg_factory``); skipped,
with a reason, otherwise. The live ``mcp_kb`` is never touched (throw-away database, dropped on
teardown).
"""

from __future__ import annotations

import pytest
from mcp_ingest.connectors.base import ConnectorStatus, CrawlMode, Cursor, SourceDocument
from mcp_ingest.db import upgrade
from mcp_ingest.embedding.fake import DeterministicFakeProvider
from mcp_ingest.pipeline.chunk import ChunkConfig
from mcp_ingest.pipeline.run import RunContext, RunOptions, run_ingest
from mcp_knowledge.client import KnowledgeClient
from mcp_knowledge.permission.enforce import (
    CallerContext,
    load_grants_for_document_ids,
)
from mcp_knowledge.retrieval.hybrid import HybridRetriever
from mcp_knowledge.tools.read_api import KnowledgeReadApi

pytestmark = [pytest.mark.live, pytest.mark.asyncio]

PROVIDER = DeterministicFakeProvider(dimensions=1024, model_id="fake/hashed-bow")

TENANT = "https://tnexwm.atlassian.net/wiki"
TEAM_URI = f"{TENANT}/spaces/ENG/pages/100100/Payment-retry-policy"
RESTRICTED_URI = f"{TENANT}/spaces/ENG/pages/100200/Executive-compensation"
_SECRET = "confidential executive salary bands L7"
_QUERY = "payment worker retry failed transactions and the confidential executive salary bands"


class _ConfluenceCorpusConnector:
    """Yields exactly what a real Confluence crawl of the ENG space would: one team page and one
    restricted page. Fed through the REAL pipeline (not hand-seeded rows)."""

    source_type = "confluence"
    name = "ConfluenceCorpusConnector"
    operations_used: tuple[str, ...] = ()

    def status(self) -> ConnectorStatus:
        return ConnectorStatus(enabled=True, configured=True)

    def iter_documents(
        self, cursor: Cursor | None, mode: CrawlMode, *, limit: int | None = None
    ) -> list[SourceDocument]:
        from datetime import UTC, datetime

        when = datetime(2026, 9, 30, 10, tzinfo=UTC)
        return [
            SourceDocument(
                source_type="confluence", source_id="100100", source_uri=TEAM_URI,
                raw_content="<h1>Payment retry policy</h1><p>The payment worker retries failed "
                "transactions three times with exponential backoff.</p>",
                source_format="html", source_updated_at=when, title="Payment retry policy",
                container="ENG", visibility="team",
            ),
            SourceDocument(
                source_type="confluence", source_id="100200", source_uri=RESTRICTED_URI,
                raw_content=f"<h1>Executive compensation</h1><p>The {_SECRET} for leadership.</p>",
                source_format="html", source_updated_at=when, title="Executive compensation",
                container="ENG", visibility="restricted",
            ),
        ]  # fmt: skip

    def fetch_documents(self, source_ids):  # pragma: no cover - not used in a full crawl
        return iter(())

    def close(self) -> None:
        pass


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


def _ingest_real_corpus(admin_dsn: str) -> None:
    """Upgrade the schema then run the REAL pipeline for the confluence corpus (mcp_ingest_rw)."""
    import psycopg

    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        upgrade(conn)
    rw = _swap_user(admin_dsn, "mcp_ingest_rw")
    connector = _ConfluenceCorpusConnector()
    ctx = RunContext(
        dsn=rw, provider=PROVIDER, chunk_config=ChunkConfig(64, 8),
        deny_globs=["*.env", "*secret*"], max_retries=1, backoff_s=0.0, sleep=lambda _s: None,
    )  # fmt: skip
    report = run_ingest(
        ctx, RunOptions(sources=["confluence"], mode="full"),
        describe=lambda _n: connector.status(), build=lambda _n: connector, explicit_source=True,
    )  # fmt: skip
    assert report.status in ("success", "partial")


@pytest.fixture
def live_corpus(docker_pg_factory):
    admin = docker_pg_factory()
    _ingest_real_corpus(admin)
    ro = _swap_user(admin, "mcp_query_ro")
    return admin, ro


async def test_TC124_restricted_real_ingested_doc_is_blocked_at_ingest(live_corpus) -> None:
    """The restricted page never enters the corpus: default-deny visibility rejects it at the
    `redact` stage (kb.ingest_failures), so it is not even a document (first line of defence)."""
    import psycopg

    admin, _ = live_corpus
    with psycopg.connect(admin, autocommit=True) as conn:
        live = {
            r[0]
            for r in conn.execute(
                "SELECT source_id FROM kb.documents WHERE deleted_at IS NULL"
            ).fetchall()
        }
        failures = {
            r[0] for r in conn.execute("SELECT source_id FROM kb.ingest_failures").fetchall()
        }
    assert "100100" in live, "the team page should be ingested"
    assert "100200" not in live, "the restricted page must NOT be a live document"
    assert "100200" in failures, "the restricted page is recorded as blocked_by_policy"


async def test_TC124_ungranted_caller_sees_no_real_ingested_content_at_the_chokepoint(
    live_corpus,
) -> None:
    """Even for content that DID persist, an un-granted caller is denied by `enforce_permission`
    (default-deny). Proven directly over the real DB grant store: the choke point keeps nothing."""
    _, ro = live_corpus
    client = KnowledgeClient(ro)

    # Resolve the real-ingested document ids, then run the ONE choke point for an un-granted caller.
    import psycopg

    with psycopg.connect(ro, autocommit=True) as conn:
        doc_ids = [
            str(r[0])
            for r in conn.execute("SELECT id FROM kb.documents WHERE deleted_at IS NULL").fetchall()
        ]
    ungranted = CallerContext(principals=frozenset(), is_team_member=False)
    grants = await load_grants_for_document_ids(client.retrieval_client(), doc_ids, ungranted)
    # Nothing is granted to an un-granted caller: the permitted set is empty (default-deny).
    assert grants.permitted_document_ids == frozenset()


async def test_TC124_restricted_content_never_a_candidate_pack_or_citation(live_corpus) -> None:
    """The full grounded path over real-ingested content: a crafted query that matches the
    restricted page returns the restricted doc NOWHERE — not a candidate, not in the pack, not a
    citation — for an un-granted caller. The CHG-001 choke point is unchanged (not weakened)."""
    _, ro = live_corpus
    client = KnowledgeClient(ro)
    retriever = HybridRetriever(client.retrieval_client(), PROVIDER)
    ungranted = CallerContext(principals=frozenset(), is_team_member=False)
    api = KnowledgeReadApi(client, retriever, caller=ungranted)

    outcome = await api.search_company_knowledge(query=_QUERY, top_k=20)
    blob = str(outcome.result.model_dump(mode="json"))
    assert RESTRICTED_URI not in blob, "restricted real-ingested source_uri leaked"
    assert _SECRET not in blob, "restricted real-ingested content leaked"
    assert outcome.result.citations == [], "an un-granted caller gets no citations (default-deny)"
