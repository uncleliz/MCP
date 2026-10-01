"""T-062..T-064 against a real PostgreSQL + pgvector (local throw-away cluster; skipped with a
reason when the server binaries or the extension are missing — see packages/conftest.py).

The installed pgvector may be < 0.8 (the distro package is 0.6.0): then these tests exercise the
over-fetch fallback; the `hnsw.iterative_scan` path is covered by the scripted-client unit tests
and by the compose-based `test_integration.py` (marker `live`, pgvector >= 0.8 image).

Roles: `mcp_query_ro` is the server's credential; seeding and negative tests use the superuser DSN.
"""

from __future__ import annotations

from datetime import UTC, datetime

import psycopg
import pytest
from mcp_common.config import CommonSettings
from mcp_common.errors import NotPermittedError
from mcp_pgvector.client import PgVectorClient
from mcp_pgvector.settings import Settings
from pg_helpers import (
    CONFLUENCE_URI,
    GITLAB_URI,
    KAFKA_URI,
    MODEL,
    NOW,
    as_user,
    provider,
    real_api,
    seed,
)
from pydantic import SecretStr


@pytest.fixture
def api(ro_dsn: str, seeded_ids: dict[str, str], common: CommonSettings):
    return real_api(ro_dsn, common)


# -- kb_semantic_search --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_TC_039_search_returns_chunks_with_the_original_uri(api) -> None:
    outcome = await api.semantic_search(query="payment worker retry backoff", top_k=5)
    result = outcome.result
    assert result.status.value == "ok"
    top = result.items[0]
    assert top["source_type"] == "confluence" and top["source_uri"] == CONFLUENCE_URI
    assert top["container"] == "PAY" and top["embedding_model"] == MODEL
    assert result.citations[0].uri == CONFLUENCE_URI
    assert all(0.3 <= i["similarity"] <= 1 for i in result.items)
    sims = [i["similarity"] for i in result.items]
    assert sims == sorted(sims, reverse=True)
    assert result.meta.data_freshness is not None
    assert result.meta.data_freshness.embedding_model == MODEL
    assert result.meta.data_freshness.staleness_hours == 3.0  # confluence ingested 3h before NOW
    assert "Deleted page" not in str(result.items)


@pytest.mark.asyncio
async def test_a_tombstoned_document_never_reaches_a_result(api, seeded_ids) -> None:
    # The tombstoned chunk is a perfect match for this text; it must still not be returned.
    result = (
        await api.semantic_search(
            query="payment worker retry failed transactions three times", top_k=50,
            min_similarity=0.0,
        )
    ).result  # fmt: skip
    assert seeded_ids["dead"] not in {i["document_id"] for i in result.items}
    assert all(i["source_id"] != "dead" for i in result.items)


@pytest.mark.asyncio
async def test_chunks_embedded_by_another_model_are_invisible(
    api, admin_dsn: str, seeded_ids
) -> None:
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        conn.execute(
            "UPDATE kb.chunks SET embedding_model = 'old/model' "
            "WHERE document_id = %s", (seeded_ids["123456"],)
        )  # fmt: skip
    result = (await api.semantic_search(query="payment worker retry backoff")).result
    assert "123456" not in {i["source_id"] for i in result.items}


@pytest.mark.asyncio
async def test_TC_041_a_query_far_from_everything_is_empty_with_best_similarity(api) -> None:
    outcome = await api.semantic_search(query="zzqx quuxbar vlorp")
    result = outcome.result
    assert result.status.value == "empty"
    assert "best_similarity=" in result.meta.warnings[0]
    assert "không có dữ liệu index phù hợp" in result.meta.warnings[0]


@pytest.mark.asyncio
async def test_TC_042_filters_excluding_the_best_match_are_distinguishable_from_no_match(
    api,
) -> None:
    nothing = (await api.semantic_search(query="zzqx quuxbar vlorp")).result
    excluded = (
        await api.semantic_search(query="payment worker retry backoff", source_types=["gitlab"])
    ).result
    assert nothing.status.value == excluded.status.value == "empty"
    assert "filters may have excluded matches" in excluded.meta.warnings[0]
    assert "đã loại" in excluded.meta.warnings[0]
    assert "filters may have excluded matches" not in nothing.meta.warnings[0]
    container = (
        await api.semantic_search(query="payment worker retry backoff", container="OPS")
    ).result
    assert "filters may have excluded matches" in container.meta.warnings[0]
    recent = (
        await api.semantic_search(
            query="kafka consumer group lag", updated_after=datetime(2026, 9, 25, tzinfo=UTC)
        )
    ).result  # the Kafka runbook was last updated 40 days before NOW (and NOW > cutoff)
    assert recent.status.value == "empty"
    assert "filters may have excluded matches" in recent.meta.warnings[0]


@pytest.mark.asyncio
async def test_filters_that_match_return_only_matching_documents(api) -> None:
    gitlab = (
        await api.semantic_search(query="deploy the worker release", source_types=["gitlab"])
    ).result
    assert [i["source_uri"] for i in gitlab.items] == [GITLAB_URI]
    ops = (await api.semantic_search(query="kafka consumer lag", container="OPS")).result
    assert [i["source_uri"] for i in ops.items] == [KAFKA_URI]
    both = (
        await api.semantic_search(
            query="kafka consumer lag", source_types=["confluence"],
            updated_after=datetime(2026, 7, 1, tzinfo=UTC),
        )
    ).result  # fmt: skip
    assert {i["source_id"] for i in both.items} == {"777"}


@pytest.mark.asyncio
async def test_top_k_is_a_hard_cap(admin_dsn: str, ro_dsn: str, common: CommonSettings) -> None:
    corpus = [
        {
            "source_type": "confluence", "source_id": str(n), "title": f"Doc {n}",
            "source_uri": f"https://wiki.example.com/p/{n}", "container": "PAY", "author": None,
            "updated": NOW, "chunks": [("h", f"payment retry policy chunk number {n}")],
        }
        for n in range(60)
    ]  # fmt: skip
    seed(admin_dsn, provider(1024), corpus=corpus)
    api = real_api(ro_dsn, common)
    result = (await api.semantic_search(query="payment retry policy", top_k=50)).result
    assert len(result.items) == 50 and result.meta.returned == 50
    assert len((await api.semantic_search(query="payment retry policy", top_k=3)).result.items) == 3


@pytest.mark.asyncio
async def test_search_runs_inside_a_read_only_transaction_with_hnsw_settings(
    ro_dsn: str, seeded_ids, common: CommonSettings
) -> None:
    client = PgVectorClient(Settings(dsn=SecretStr(ro_dsn)), common=common)
    try:
        async with client.read_tx() as tx:
            assert (await tx.fetch("show_read_only"))[0]["transaction_read_only"] == "on"
            await tx.set_local("hnsw.ef_search", "123")
            row = await tx._conn.execute("SHOW hnsw.ef_search")  # noqa: SLF001
            assert (await row.fetchone())[0] == "123"
        async with client.read_tx() as tx:  # SET LOCAL ended with the transaction
            row = await tx._conn.execute("SHOW hnsw.ef_search")  # noqa: SLF001
            assert (await row.fetchone())[0] != "123"
            assert (await tx._conn.execute("SHOW statement_timeout")).fetchone  # noqa: SLF001
            timeout = await (await tx._conn.execute("SHOW statement_timeout")).fetchone()  # noqa: SLF001
            assert timeout[0] == "15s"
    finally:
        await client.aclose()


# -- TC-044: nothing can write through this server ------------------------------------------------


@pytest.mark.asyncio
async def test_TC_044_a_write_capable_role_still_cannot_write_inside_read_tx(
    rw_dsn: str, seeded_ids, common: CommonSettings
) -> None:
    """BEGIN READ ONLY (layer 2b) stops writes even for a role that holds the privileges."""
    client = PgVectorClient(Settings(dsn=SecretStr(rw_dsn)), common=common)
    try:
        async with client.read_tx() as tx:
            assert (await tx.fetch("show_read_only"))[0]["transaction_read_only"] == "on"
            with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
                await tx._conn.execute("DELETE FROM kb.chunks")  # noqa: SLF001
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_TC_044_mcp_query_ro_is_rejected_at_the_database_role_level(
    ro_dsn: str, seeded_ids
) -> None:
    async with await psycopg.AsyncConnection.connect(ro_dsn, autocommit=True) as conn:
        await conn.execute("SET default_transaction_read_only = off")
        for statement in (
            "INSERT INTO kb.documents (source_type, source_id, source_uri, content_hash) "
            "VALUES ('x', 'y', 'https://z', 'h')",
            "UPDATE kb.documents SET title = 'x'",
            "DELETE FROM kb.chunks",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                await conn.execute(statement)


@pytest.mark.asyncio
async def test_client_only_runs_named_statements_never_arbitrary_sql(
    ro_dsn: str, seeded_ids, common: CommonSettings
) -> None:
    client = PgVectorClient(Settings(dsn=SecretStr(ro_dsn)), common=common)
    try:
        async with client.read_tx() as tx:
            with pytest.raises(NotPermittedError) as exc:
                await tx.fetch("DELETE FROM kb.chunks")
            assert exc.value.details["operation"] == "DELETE FROM kb.chunks"
            with pytest.raises(NotPermittedError):
                await tx.fetch("anything_else")
            with pytest.raises(NotPermittedError):
                await tx.set_local("statement_timeout", "0")
        async with client.read_tx() as tx:  # the transaction is still usable afterwards
            assert (await tx.fetch("schema_info"))[0]["has_chunks"] is True
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_a_failed_statement_leaves_the_connection_usable(
    ro_dsn: str, seeded_ids, common: CommonSettings
) -> None:
    client = PgVectorClient(Settings(dsn=SecretStr(ro_dsn)), common=common)
    try:
        with pytest.raises(Exception):  # noqa: B017, PT011 - mapped ToolError
            async with client.read_tx() as tx:
                await tx._conn.execute("SELECT * FROM kb.no_such_table")  # noqa: SLF001
        async with client.read_tx() as tx:
            assert (await tx.fetch("schema_info"))[0]["has_documents"] is True
    finally:
        await client.aclose()


# -- startup check (TC-043, ADR-0003 A1, ADR-0010) ------------------------------------------


@pytest.mark.asyncio
async def test_startup_check_accepts_the_read_only_role(
    ro_dsn: str, seeded_ids, common: CommonSettings
) -> None:
    client = PgVectorClient(Settings(dsn=SecretStr(ro_dsn)), common=common)
    try:
        report = await client.verify_credentials(provider(1024))
        assert report.ok and not report.fatal and report.reasons == []
        assert report.role == "mcp_query_ro" and report.pgvector_version
        assert await client.credential_check(provider(1024)) is True
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_TC_043_the_ingest_rw_dsn_is_refused(
    rw_dsn: str, seeded_ids, common: CommonSettings
) -> None:
    client = PgVectorClient(Settings(dsn=SecretStr(rw_dsn)), common=common)
    try:
        report = await client.verify_credentials(provider(1024))
        assert not report.ok and report.fatal
        text = " ".join(report.reasons)
        assert "mcp_ingest_rw" in text and "INSERT on kb.chunks" in text
        assert "not read-only by default" in text
        assert await client.credential_check(provider(1024)) is False
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_a_superuser_dsn_is_refused(
    admin_dsn: str, seeded_ids, common: CommonSettings
) -> None:
    client = PgVectorClient(Settings(dsn=SecretStr(admin_dsn)), common=common)
    try:
        report = await client.verify_credentials(provider(1024))
        assert report.fatal and any("superuser" in r for r in report.reasons)
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_a_role_that_can_insert_but_is_read_only_by_default_is_still_refused(
    admin_dsn: str, seeded_ids, common: CommonSettings
) -> None:
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        conn.execute("GRANT INSERT ON kb.chunks TO mcp_query_ro")
    client = PgVectorClient(Settings(dsn=SecretStr(as_user(admin_dsn, "mcp_query_ro"))),
                            common=common)  # fmt: skip
    try:
        report = await client.verify_credentials(provider(1024))
        assert report.fatal and "INSERT on kb.chunks" in " ".join(report.reasons)
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_embedding_model_mismatch_refuses_to_serve_and_asks_for_reembed(
    ro_dsn: str, seeded_ids, common: CommonSettings
) -> None:
    client = PgVectorClient(Settings(dsn=SecretStr(ro_dsn)), common=common)
    try:
        report = await client.verify_credentials(provider(1024, model_id="other/model"))
        assert not report.ok and report.fatal
        text = " ".join(report.reasons)
        assert "reembed" in text and "other/model" in text and MODEL in text
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_a_mixed_model_store_is_refused(
    admin_dsn: str, ro_dsn: str, seeded_ids, common: CommonSettings
) -> None:
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        conn.execute(
            "UPDATE kb.chunks SET embedding_model = 'half/migrated' "
            "WHERE document_id = %s", (seeded_ids["777"],)
        )  # fmt: skip
    client = PgVectorClient(Settings(dsn=SecretStr(ro_dsn)), common=common)
    try:
        report = await client.verify_credentials(provider(1024))
        assert report.fatal and "half/migrated" in " ".join(report.reasons)
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_a_dimension_mismatch_with_the_schema_is_refused(
    ro_dsn: str, seeded_ids, common: CommonSettings
) -> None:
    client = PgVectorClient(Settings(dsn=SecretStr(ro_dsn)), common=common)
    try:
        report = await client.verify_credentials(provider(8))
        assert report.fatal and "dimension" in " ".join(report.reasons)
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_an_empty_store_passes_with_a_warning(ro_dsn: str, common: CommonSettings) -> None:
    client = PgVectorClient(Settings(dsn=SecretStr(ro_dsn)), common=common)
    try:
        report = await client.verify_credentials(provider(1024))
        assert report.ok and any("empty" in w for w in report.warnings)
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_an_unmigrated_database_is_reported_not_fatal(
    pg_database_factory, common: CommonSettings
) -> None:
    dsn = as_user(pg_database_factory(), "postgres")
    client = PgVectorClient(Settings(dsn=SecretStr(dsn)), common=common)
    try:
        report = await client.verify_credentials(provider(1024))
        assert not report.ok and not report.fatal
        assert "mcp-ingest db upgrade" in report.reasons[0]
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_a_database_with_the_extension_but_no_kb_schema_is_reported_not_fatal(
    pg_database_factory, common: CommonSettings
) -> None:
    dsn = as_user(pg_database_factory(), "postgres")
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("CREATE EXTENSION vector")
    client = PgVectorClient(Settings(dsn=SecretStr(dsn)), common=common)
    try:
        report = await client.verify_credentials(provider(1024))
        assert not report.ok and not report.fatal
        assert "schema kb is not migrated" in report.reasons[0]
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_an_unreachable_database_is_reported_without_leaking_the_dsn(
    common: CommonSettings,
) -> None:
    dsn = "postgresql://mcp_query_ro:s3cr3tpw@127.0.0.1:1/none"
    client = PgVectorClient(Settings(dsn=SecretStr(dsn)), common=common)
    report = await client.verify_credentials(provider(1024))
    assert not report.ok and not report.fatal
    assert "upstream_unavailable" in report.reasons[0]
    assert "s3cr3tpw" not in " ".join(report.reasons)
    from mcp_common.errors import ToolError

    with pytest.raises(ToolError) as exc:
        await client.capabilities()
    assert exc.value.code.value == "upstream_unavailable"
    assert "VPN" in exc.value.details["hint"] and "s3cr3tpw" not in str(exc.value.details)
    await client.aclose()


@pytest.mark.asyncio
async def test_capabilities_reads_the_installed_pgvector_version(
    ro_dsn: str, seeded_ids, common: CommonSettings, pg_server
) -> None:
    client = PgVectorClient(Settings(dsn=SecretStr(ro_dsn)), common=common)
    try:
        caps = await client.capabilities()
        assert caps is await client.capabilities()  # cached
        assert caps.version is not None
        assert caps.supports_iterative_scan == (caps.version >= (0, 8, 0))
        assert ".".join(map(str, caps.version)).startswith(pg_server.pgvector_version[:3])
    finally:
        await client.aclose()


# -- kb_get_document -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_document_by_id_and_uri_over_the_real_schema(api, seeded_ids) -> None:
    by_id = (await api.get_document(document_id=seeded_ids["123456"])).result
    item = by_id.items[0]
    assert by_id.status.value == "ok" and item["chunk_count"] == 3
    text = item["content"]
    assert text.index("three times") < text.index("exponential") < text.index("dead letter")
    by_uri = (await api.get_document(source_uri=CONFLUENCE_URI)).result
    assert by_uri.items[0]["document_id"] == seeded_ids["123456"]
    assert by_uri.citations[0].uri == CONFLUENCE_URI


@pytest.mark.asyncio
async def test_get_document_tombstoned_and_unknown_are_not_found(api, seeded_ids) -> None:
    dead = await api.get_document(document_id=seeded_ids["dead"])
    assert dead.result.status.value == "not_found"
    ghost = await api.get_document(document_id="00000000-0000-0000-0000-000000000000")
    assert ghost.result.status.value == "not_found"
    gone = await api.get_document(
        source_uri="https://wiki.example.com/pages/viewpage.action?pageId=999"
    )
    assert gone.result.status.value == "not_found"  # the tombstone's URI


# -- kb_list_sources -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_NFR_004_list_sources_over_the_real_schema(api) -> None:
    result = (await api.list_sources()).result
    by_type = {i["source_type"]: i for i in result.items}
    assert set(by_type) == {"confluence", "gitlab"}
    confluence = by_type["confluence"]
    assert (
        confluence["document_count"] == 2 and confluence["chunk_count"] == 4
    )  # tombstone excluded
    assert confluence["last_run_status"] == "success" and confluence["staleness_hours"] == 3.0
    assert confluence["embedding_model"] == MODEL
    assert by_type["gitlab"]["last_success_at"] is None
    assert any("gitlab: chưa có lần ingest nào thành công" in w for w in result.meta.warnings)
    assert result.meta.data_freshness is not None
    only = (await api.list_sources(source_types=["gitlab"])).result
    assert [i["source_type"] for i in only.items] == ["gitlab"]


@pytest.mark.asyncio
async def test_TC_068_list_sources_on_an_empty_database_is_empty_not_an_error(
    ro_dsn: str, common: CommonSettings
) -> None:
    result = (await real_api(ro_dsn, common).list_sources()).result
    assert result.status.value == "empty" and result.items == []


@pytest.mark.asyncio
async def test_list_sources_reports_the_latest_run_status(
    admin_dsn: str, ro_dsn: str, seeded_ids, common: CommonSettings
) -> None:
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO kb.ingest_runs (source_type, status, started_at) "
            "VALUES ('confluence', 'failed', %s)", (NOW,)
        )  # fmt: skip
    result = (await real_api(ro_dsn, common).list_sources()).result
    by_type = {i["source_type"]: i for i in result.items}
    assert by_type["confluence"]["last_run_status"] == "failed"
    assert any("confluence: run gần nhất failed" in w for w in result.meta.warnings)
