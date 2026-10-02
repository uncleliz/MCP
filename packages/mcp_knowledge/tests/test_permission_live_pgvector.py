"""R-C-001 / R-C-004 (E-mcp-data-platform-007) — the single permission choke point on REAL pgvector.

The unit/integration adversarial tests (``test_permission_adversarial.py``) prove the choke point
with a fake grant store. This re-proves it end to end over a REAL pgvector database read through the
read-only ``mcp_query_ro`` role, for ALL EIGHT knowledge-tier content tools — the gap R-028 found
(the choke point was wired on only 2 of 8). Skipped, with a reason, when the Docker pgvector
container is unavailable (``packages/conftest.py::docker_pgvector``); the live ``mcp_kb`` is never
touched (each run uses a throw-away database that is dropped on teardown).

Scenario: two SOURCED documents whose content matches the query —

* a PERMITTED document granted ``'*team*'`` (the v1 team-only grant); and
* a RESTRICTED document granted ONLY to a non-team principal (``'user:cfo'``), never ``'*team*'``.

The v1 :class:`CallerContext` is a team member (holds ``'*team*'`` and nothing else), so the
default-deny intersection resolves the permitted document and drops the restricted one. Every one of
the 8 tools must surface the permitted document's content where relevant and the restricted
document's content NOWHERE — not as a chunk, entity, edge, summary, version, claim or citation.
"""

from __future__ import annotations

import pytest
from mcp_ingest.db import upgrade
from mcp_ingest.embedding.fake import DeterministicFakeProvider
from mcp_knowledge.client import KnowledgeClient
from mcp_knowledge.permission.enforce import CallerContext
from mcp_knowledge.retrieval.hybrid import HybridRetriever
from mcp_knowledge.tools.read_api import KnowledgeReadApi

pytestmark = pytest.mark.asyncio

PROVIDER = DeterministicFakeProvider(dimensions=1024, model_id="fake/hashed-bow")

_PERMITTED_URI = "https://wiki.example.test/ok/payment-retry"
_RESTRICTED_URI = "https://wiki.example.test/sec/exec-salary"
_SECRET = "executive salary band L7 confidential"
_QUERY = "payment worker retry failed transactions executive salary"


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


class _Seeded:
    def __init__(self, permitted_doc: str, restricted_doc: str) -> None:
        self.permitted_doc = permitted_doc
        self.restricted_doc = restricted_doc


def _seed(dsn: str) -> _Seeded:
    """Seed one permitted + one restricted document with full domain rows referencing each."""
    import psycopg

    with psycopg.connect(dsn, autocommit=True) as conn:
        upgrade(conn)  # full chain incl 0007/0007b/0008 (entities/relationships/summaries/perms)
        run_id = conn.execute(
            "INSERT INTO kb.ingest_runs (source_type, status) VALUES ('confluence','success') "
            "RETURNING id"
        ).fetchone()[0]

        def _doc(source_id: str, uri: str, title: str, container: str) -> str:
            return conn.execute(
                "INSERT INTO kb.documents (source_type, source_id, source_uri, title, container, "
                "content_hash, last_seen_run_id) VALUES ('confluence',%s,%s,%s,%s,'h',%s) "
                "RETURNING id",
                (source_id, uri, title, container, run_id),
            ).fetchone()[0]

        def _chunk(doc: str, text: str) -> None:
            vec = PROVIDER.embed_documents([text])[0]
            vec_lit = "[" + ",".join(str(x) for x in vec) + "]"
            conn.execute(
                "INSERT INTO kb.chunks (document_id, chunk_index, content, token_count, "
                "content_tsv, embedding, embedding_model) "
                "VALUES (%s,0,%s,10, to_tsvector('simple', %s), %s, %s)",
                (doc, text, text, vec_lit, PROVIDER.model_id),
            )

        permitted = _doc("ok1", _PERMITTED_URI, "Payment retry policy", "PAY")
        restricted = _doc("sec1", _RESTRICTED_URI, "Executive salary bands", "SEC")
        _chunk(
            permitted,
            "payment worker retry failed transactions three times exponential backoff",
        )
        _chunk(restricted, f"payment worker retry and the {_SECRET} for the payments lead")

        # Grants: permitted -> '*team*' (the v1 caller holds it); restricted -> non-team only.
        conn.execute(
            "INSERT INTO kb.document_permissions (document_id, principal, grant_type) "
            "VALUES (%s,'*team*','read')",
            (permitted,),
        )
        conn.execute(
            "INSERT INTO kb.document_permissions (document_id, principal, grant_type) "
            "VALUES (%s,'user:cfo','read')",
            (restricted,),
        )

        # Entities (get_service / get_repository / find_related_knowledge).
        ok_ent = conn.execute(
            "INSERT INTO kb.entities (entity_type, name, display_name, document_id) "
            "VALUES ('service','payment-service','Payment Service',%s) RETURNING id",
            (permitted,),
        ).fetchone()[0]
        sec_ent = conn.execute(
            "INSERT INTO kb.entities (entity_type, name, display_name, document_id) "
            "VALUES ('service','exec-comp','Executive Compensation',%s) RETURNING id",
            (restricted,),
        ).fetchone()[0]
        # A repository entity on each document too (get_repository).
        conn.execute(
            "INSERT INTO kb.entities (entity_type, name, document_id) "
            "VALUES ('repository','payment-repo',%s)",
            (permitted,),
        )
        conn.execute(
            "INSERT INTO kb.entities (entity_type, name, document_id) "
            "VALUES ('repository','exec-repo',%s)",
            (restricted,),
        )
        # A neighbour of the permitted entity, backed by the RESTRICTED document: the edge to it
        # must be dropped by the choke point even though the root (permitted) survives.
        conn.execute(
            "INSERT INTO kb.relationships (src_entity_id, dst_entity_id, rel_type) "
            "VALUES (%s,%s,'related_to')",
            (ok_ent, sec_ent),
        )

        # Summaries (get_knowledge_summary): one per entity, provenance -> its backing document.
        conn.execute(
            "INSERT INTO kb.knowledge_summaries (subject_type, subject_id, summary, provenance) "
            "VALUES ('entity',%s,'payment-service orchestrates payments',"
            "jsonb_build_array(jsonb_build_object('document_id',%s::text)))",
            (str(ok_ent), str(permitted)),
        )
        conn.execute(
            "INSERT INTO kb.knowledge_summaries (subject_type, subject_id, summary, provenance) "
            "VALUES ('entity',%s,%s,"
            "jsonb_build_array(jsonb_build_object('document_id',%s::text)))",
            (str(sec_ent), _SECRET, str(restricted)),
        )

        # Versions (get_document_version).
        for doc in (permitted, restricted):
            conn.execute(
                "INSERT INTO kb.document_versions (document_id, version, content_hash, status) "
                "VALUES (%s,1,'h1','current')",
                (doc,),
            )
    return _Seeded(str(permitted), str(restricted))


@pytest.fixture
def live_api(docker_pg_factory):
    dsn = docker_pg_factory()
    seeded = _seed(dsn)
    ro_dsn = _swap_user(dsn, "mcp_query_ro")
    client = KnowledgeClient(ro_dsn)
    retriever = HybridRetriever(client.retrieval_client(), PROVIDER)
    api = KnowledgeReadApi(client, retriever, caller=CallerContext())
    return api, seeded


def _no_restricted_leak(blob: str, restricted_doc: str) -> None:
    assert _RESTRICTED_URI not in blob, "restricted source_uri leaked"
    assert _SECRET not in blob, "restricted secret content leaked"
    assert restricted_doc not in blob, "restricted document_id leaked into content"


async def test_search_code_live_hides_restricted(live_api) -> None:
    api, seeded = live_api
    outcome = await api.search_code(query=_QUERY, top_k=20)
    blob = str(outcome.result.model_dump(mode="json"))
    _no_restricted_leak(blob, seeded.restricted_doc)


async def test_search_company_knowledge_live_hides_restricted(live_api) -> None:
    api, seeded = live_api
    outcome = await api.search_company_knowledge(query=_QUERY, top_k=20)
    blob = str(outcome.result.model_dump(mode="json"))
    _no_restricted_leak(blob, seeded.restricted_doc)


async def test_get_jira_context_live_hides_restricted(live_api) -> None:
    api, seeded = live_api
    outcome = await api.get_jira_context(subject="payment worker retry", top_k=20)
    blob = str(outcome.result.model_dump(mode="json"))
    _no_restricted_leak(blob, seeded.restricted_doc)


async def test_get_service_live_denies_restricted_entity(live_api) -> None:
    api, seeded = live_api
    # The permitted service resolves; the restricted service is not_found (its doc is denied).
    ok = await api.get_service(name="payment-service")
    assert ok.result.status.value == "ok"
    denied = await api.get_service(name="exec-comp")
    assert denied.result.status.value == "not_found"
    _no_restricted_leak(str(denied.result.model_dump(mode="json")), seeded.restricted_doc)


async def test_get_repository_live_denies_restricted_entity(live_api) -> None:
    api, seeded = live_api
    ok = await api.get_repository(name="payment-repo")
    assert ok.result.status.value == "ok"
    denied = await api.get_repository(name="exec-repo")
    assert denied.result.status.value == "not_found"
    _no_restricted_leak(str(denied.result.model_dump(mode="json")), seeded.restricted_doc)


async def test_find_related_knowledge_live_drops_restricted_edge(live_api) -> None:
    api, seeded = live_api
    # Root is the permitted service; its only neighbour is backed by the restricted document, so
    # the edge must be dropped (no leak), while the permitted root still resolves.
    outcome = await api.find_related_knowledge(entity="payment-service", max_depth=2)
    blob = str(outcome.result.model_dump(mode="json"))
    _no_restricted_leak(blob, seeded.restricted_doc)
    assert all("exec-comp" != item.get("dst") for item in outcome.result.items)


async def test_get_knowledge_summary_live_denies_restricted(live_api) -> None:
    api, seeded = live_api
    denied = await api.get_knowledge_summary(subject="exec-comp")
    assert denied.result.status.value == "not_found"
    _no_restricted_leak(str(denied.result.model_dump(mode="json")), seeded.restricted_doc)


async def test_get_document_version_live_denies_restricted(live_api) -> None:
    api, seeded = live_api
    denied = await api.get_document_version(document_id=seeded.restricted_doc)
    assert denied.result.status.value == "not_found"
    assert denied.result.items == []
    blob = str(denied.result.model_dump(mode="json"))
    # The caller supplied the restricted document_id, so it is echoed in query_echo; content must
    # not be — assert no source_uri/secret leak.
    assert _RESTRICTED_URI not in blob
    assert _SECRET not in blob
    # The permitted document's versions DO resolve for a granted caller.
    ok = await api.get_document_version(document_id=seeded.permitted_doc)
    assert ok.result.status.value == "ok"
