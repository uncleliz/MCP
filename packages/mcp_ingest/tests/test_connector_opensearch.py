"""T-072: OpenSearch connector — off by default, allowlist only, `search_after`, no PIT/scroll.

AC: FR-012/AC-001 (ADR-0012 A4/A5).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from mcp_common.errors import NotPermittedError
from mcp_ingest.connectors import opensearch as module
from mcp_ingest.connectors.base import Cursor
from mcp_ingest.connectors.opensearch import PAGE_SIZE, OpenSearchConnector
from mcp_ingest.settings import Settings
from mcp_opensearch.client import ALLOWED_OPERATIONS, assert_body_allowed


def hit(index: str, doc_id: str, ts: str, text: str | None = "runbook text", **extra: Any) -> dict:
    source = {"@timestamp": ts, "title": f"Doc {doc_id}", **extra}
    if text is not None:
        source["content"] = text
    return {"_index": index, "_id": doc_id, "_source": source, "sort": [ts, doc_id]}


class FakeOs:
    """Mimics `OpenSearchClient.search`, including the real body guard."""

    def __init__(self, hits: list[dict]) -> None:
        self.hits = hits
        self.calls: list[tuple[str, dict]] = []
        self.closed = False

    async def search(self, index: str, body: dict, *, request_timeout: float | None = None) -> dict:
        assert_body_allowed(body)  # the real guard: no scroll, no pit, no scripts
        self.calls.append((index, body))
        if "ids" in body["query"]:
            wanted = set(body["query"]["ids"]["values"])
            return {"hits": {"hits": [h for h in self.hits if h["_id"] in wanted]}}
        rows = self.hits
        range_q = body["query"].get("range")
        if range_q:
            gte = next(iter(range_q.values()))["gte"]
            rows = [h for h in rows if h["_source"]["@timestamp"] >= gte]
        after = body.get("search_after")
        if after:
            rows = [h for h in rows if h["sort"] > after]
        return {"hits": {"hits": rows[: body["size"]]}}

    async def aclose(self) -> None:
        self.closed = True


def make(client: FakeOs, indices: list[str] | None = None) -> OpenSearchConnector:
    return OpenSearchConnector(
        client,  # type: ignore[arg-type]
        indices=["postmortems"] if indices is None else indices,
        base_url="https://os.example.com:9200",
    )


def crawl(connector: OpenSearchConnector, cursor: Cursor | None = None, mode: str = "full", **kw):
    try:
        return list(connector.iter_documents(cursor, mode, **kw))  # type: ignore[arg-type]
    finally:
        connector.close()


def test_ADR_0012_A5_without_an_allowlist_the_connector_is_disabled_and_crawls_nothing(
    monkeypatch,
) -> None:
    monkeypatch.delenv("MCP_INGEST_OPENSEARCH_INDICES", raising=False)
    status = module.status(Settings())
    assert not status.enabled and not status.configured
    assert status.missing_env == ["MCP_INGEST_OPENSEARCH_INDICES"]
    client = FakeOs([hit("postmortems-000001", "d1", "2026-09-01T00:00:00Z")])
    assert crawl(make(client, indices=[])) == []
    assert client.calls == []  # not even a request


def test_enabled_by_allowlist_and_needs_the_opensearch_settings(monkeypatch) -> None:
    monkeypatch.setenv("MCP_INGEST_OPENSEARCH_INDICES", "postmortems, runbooks")
    monkeypatch.delenv("MCP_OPENSEARCH_HOSTS", raising=False)
    status = module.status(Settings())
    assert status.enabled and not status.configured and "MCP_OPENSEARCH_HOSTS" in status.missing_env
    monkeypatch.setenv("MCP_OPENSEARCH_HOSTS", "https://os.example.com:9200")
    assert module.status(Settings()).configured
    built = module.build(Settings())
    try:
        assert built._indices == ["postmortems", "runbooks"] and built.status().enabled
    finally:
        built.close()


def test_FR_012_AC_001_only_allowlisted_indices_are_crawled(client_hits=None) -> None:
    client = FakeOs([
        hit("postmortems-000001", "a", "2026-09-01T00:00:00Z"),
        hit("postmortems-v2", "b", "2026-09-01T00:00:01Z"),      # matches the pattern, not the alias  # noqa: E501
        hit("app-logs-000007", "c", "2026-09-01T00:00:02Z"),
    ])  # fmt: skip
    docs = crawl(make(client))
    assert [d.source_id for d in docs] == ["postmortems:a"]
    index, body = client.calls[0]
    assert index == "postmortems,postmortems-*"
    assert body["sort"] == [{"@timestamp": "asc"}, {"_id": "asc"}]
    assert not {"scroll", "pit", "point_in_time"} & set(body)


def test_source_id_is_stable_across_ilm_rollover_and_visibility_is_team_for_the_allowlist() -> None:
    first = crawl(make(FakeOs([hit("postmortems-000001", "doc-abc", "2026-09-01T00:00:00Z")])))
    rolled = crawl(make(FakeOs([hit("postmortems-000002", "doc-abc", "2026-09-01T00:00:00Z")])))
    assert first[0].source_id == rolled[0].source_id == "postmortems:doc-abc"
    assert first[0].visibility == "team" and first[0].container == "postmortems"
    assert first[0].source_uri == "https://os.example.com:9200/postmortems-000001/_doc/doc-abc"
    assert first[0].metadata == {"index": "postmortems-000001"}
    assert first[0].source_updated_at == datetime(2026, 9, 1, tzinfo=UTC)


def test_pagination_uses_search_after_never_a_scroll() -> None:
    hits = [
        hit("postmortems-000001", f"d{i:05d}", f"2026-09-01T00:{i // 60:02d}:{i % 60:02d}Z")
        for i in range(PAGE_SIZE + 10)
    ]
    client = FakeOs(hits)
    assert len(crawl(make(client))) == PAGE_SIZE + 10
    assert len(client.calls) == 2 and "search_after" in client.calls[1][1]
    assert all("scroll" not in body for _, body in client.calls)


def test_the_watermark_is_an_inclusive_range_and_limit_applies() -> None:
    client = FakeOs(
        [
            hit("postmortems-000001", "a", "2026-09-01T00:00:00Z"),
            hit("postmortems-000001", "b", "2026-09-02T00:00:00Z"),
            hit("postmortems-000001", "c", "2026-09-03T00:00:00Z"),
        ]
    )
    cursor = Cursor(datetime(2026, 9, 2, tzinfo=UTC))
    docs = crawl(make(client), cursor, "incremental")
    assert [d.source_id for d in docs] == ["postmortems:b", "postmortems:c"]
    assert client.calls[0][1]["query"] == {"range": {"@timestamp": {"gte": "2026-09-02T00:00:00Z"}}}
    assert len(crawl(make(FakeOs(client.hits)), limit=1)) == 1


def test_hits_without_text_are_skipped() -> None:
    assert (
        crawl(make(FakeOs([hit("postmortems-000001", "a", "2026-09-01T00:00:00Z", text=None)])))
        == []
    )


def test_fetch_documents_for_retry_failed() -> None:
    client = FakeOs([hit("postmortems-000001", "a", "2026-09-01T00:00:00Z")])
    connector = make(client)
    try:
        docs = list(
            connector.fetch_documents(["postmortems:a", "postmortems:zzz", "other:a", "bad"])
        )
    finally:
        connector.close()
    assert [d.source_id for d in docs] == ["postmortems:a"]


def test_the_body_guard_still_refuses_pit_and_scripts() -> None:
    with pytest.raises(NotPermittedError):
        assert_body_allowed({"query": {"match_all": {}}, "pit": {"id": "x"}})
    assert set(OpenSearchConnector.operations_used) <= set(ALLOWED_OPERATIONS)
