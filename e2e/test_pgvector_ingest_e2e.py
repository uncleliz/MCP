"""E2E: `mcp-ingest` CLI (subprocess) -> local Postgres+pgvector -> `mcp-pgvector` stdio,
plus
Journey 3 (FR-013) with `mcp-sqs-sns` over moto.

Covers TC-045, TC-049, TC-051, TC-052, TC-053, TC-067, TC-068, TC-073 (steps 1-3), TC-074, TC-077.
Stubs: Confluence REST (local HTTP), OpenAI-compatible embeddings endpoint (the deterministic hashed
bag-of-words provider behind HTTP; no Hugging Face), throw-away Postgres 16 + pgvector.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from typing import Any

import boto3
import httpx
import pytest
from harness import ROOT, StubHttp, open_server, result_payload
from mcp_ingest.embedding.fake import DeterministicFakeProvider
from moto.server import ThreadedMotoServer

MODEL = "fake/hashed-bow"
AWS_KEY = "AKIAIOSFODNN7EXAMPLE"


def _page(
    pid: str,
    title: str,
    body: str,
    *,
    restricted: bool = False,
    when: str = "2026-09-30T10:00:00.000Z",
) -> dict:
    users = [{"accountId": "u1"}] if restricted else []
    return {
        "id": pid,
        "type": "page",
        "title": title,
        "space": {"key": "PAY", "type": "global"},
        "version": {"number": 1, "when": when, "by": {"displayName": "An Nguyen"}},
        "body": {"storage": {"value": body}},
        "ancestors": [],
        "restrictions": {
            "read": {"restrictions": {"user": {"results": users}, "group": {"results": []}}}
        },
        "_links": {
            "webui": f"/spaces/PAY/pages/{pid}/p{pid}",
            "base": "https://wiki.example.test/wiki",
        },
    }


class Env:
    """One migrated database + stubs + helpers to run the CLI and open the pgvector server."""

    def __init__(self, admin_dsn: str, pages: list[dict], stub: StubHttp, embed: StubHttp):
        self.admin_dsn, self.pages, self.stub, self.embed = admin_dsn, pages, stub, embed
        self.rw = admin_dsn.replace("postgres@", "mcp_ingest_rw@", 1)
        self.ro = admin_dsn.replace("postgres@", "mcp_query_ro@", 1)

    def env(self, **extra: str) -> dict[str, str]:
        import os

        base = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "LANG", "VIRTUAL_ENV")}
        base.update(
            {
                "MCP_INGEST_ADMIN_DSN": self.admin_dsn,
                "MCP_INGEST_PGVECTOR_DSN": self.rw,
                "MCP_INGEST_EMBEDDING_PROVIDER": "http",
                "MCP_INGEST_EMBEDDING_URL": self.embed.url + "/v1",
                "MCP_INGEST_EMBEDDING_MODEL": MODEL,
                "MCP_INGEST_EMBEDDING_DIMENSIONS": "1024",
                "MCP_INGEST_CONFLUENCE_TEAM_SPACES": "PAY",
                "MCP_CONFLUENCE_BASE_URL": self.stub.url,
                "MCP_CONFLUENCE_EMAIL": "a@b.c",
                "MCP_CONFLUENCE_API_TOKEN": "x",
                "MCP_ALLOW_UNVERIFIED_CREDENTIALS": "true",
            }
        )
        base.update(extra)
        return base

    def cli(self, *args: str, expect: tuple[int, ...] = (0,)) -> subprocess.CompletedProcess[str]:
        proc = subprocess.run(
            [sys.executable, "-m", "mcp_ingest", *args],
            capture_output=True,
            text=True,
            timeout=180,
            cwd=ROOT,
            env=self.env(),
        )
        assert proc.returncode in expect, (proc.returncode, proc.stdout, proc.stderr[-2000:])
        return proc

    def ingest(self, *extra: str) -> dict[str, Any]:
        proc = self.cli(
            "run", "--source", "confluence", "--mode", "full", "--json", *extra, expect=(0, 1)
        )
        return json.loads(proc.stdout)

    def sql(self, query: str, params: tuple = ()) -> list[tuple]:
        import psycopg

        with psycopg.connect(self.admin_dsn, autocommit=True) as conn:
            cur = conn.execute(query, params)
            return cur.fetchall() if cur.description else []

    def pg_server(self, **extra: str):
        return open_server(
            "pgvector",
            {
                "MCP_PGVECTOR_DSN": self.ro,
                "MCP_INGEST_EMBEDDING_PROVIDER": "http",
                "MCP_INGEST_EMBEDDING_URL": self.embed.url + "/v1",
                "MCP_INGEST_EMBEDDING_MODEL": MODEL,
                "MCP_INGEST_EMBEDDING_DIMENSIONS": "1024",
                **extra,
            },
        )


@pytest.fixture
def env(pg_server, pg_database_factory, stub_http) -> Env:
    pages = [
        _page(
            "111",
            "Payment retry policy",
            "<h1>Payment retry policy</h1><h2>Backoff</h2><p>The payment worker retries failed "
            f"transactions three times with exponential backoff. "
            f"Support key {AWS_KEY} must never be indexed.</p>",
        ),
        _page(
            "222",
            "HR salary table",
            "<h1>Salary</h1><p>confidential salary bands</p>",
            restricted=True,
        ),
    ]
    provider = DeterministicFakeProvider(dimensions=1024, model_id=MODEL)

    def confluence(method: str, path: str, query: dict, body: bytes):
        if path.endswith("/rest/api/content/search"):
            return 200, {
                "results": env_pages(),
                "start": 0,
                "limit": 25,
                "size": len(env_pages()),
                "_links": {},
            }
        return 404, {}

    def embeddings(method: str, path: str, query: dict, body: bytes):
        texts = json.loads(body)["input"]
        return 200, {
            "data": [
                {"index": i, "embedding": v} for i, v in enumerate(provider.embed_documents(texts))
            ]
        }

    holder: dict[str, Any] = {}

    def env_pages() -> list[dict]:
        return holder["env"].pages

    admin = pg_database_factory()
    e = Env(admin, pages, stub_http(confluence), stub_http(embeddings))
    holder["env"] = e
    e.cli("db", "upgrade")
    return e


async def _search(env: Env, query: str, **args: Any) -> dict[str, Any]:
    async with env.pg_server() as s:
        return result_payload(await s.call_tool("kb_semantic_search", {"query": query, **args}))


async def test_TC_045_ingest_then_semantic_search_roundtrip_with_citation(env):
    report = env.ingest()
    assert report["status"] in ("success", "partial") and report["exit_code"] == 0
    payload = await _search(env, "how many times does the payment worker retry failed transactions")
    assert payload["status"] == "ok"
    top = payload["citations"][0]
    assert top["source_type"] == "confluence"
    assert (
        top["uri"] == "https://wiki.example.test/wiki/spaces/PAY/pages/111/p111"
    )  # original URL, not a document id


async def test_TC_051_secret_never_stored_nor_returned(env):
    env.ingest()
    assert (
        env.sql("SELECT count(*) FROM kb.chunks WHERE content LIKE %s", (f"%{AWS_KEY}%",))[0][0]
        == 0
    )
    payload = await _search(env, f"support key {AWS_KEY}")
    assert AWS_KEY not in json.dumps(payload)


async def test_TC_073_restricted_page_rejected_and_purged_on_relabel(env):
    env.ingest()
    # step 1: restricted page never reaches kb.chunks, recorded as blocked_by_policy
    assert env.sql("SELECT count(*) FROM kb.documents WHERE source_id = '222'")[0][0] == 0
    failures = env.sql("SELECT code FROM kb.ingest_failures WHERE source_id = '222'")
    assert failures and failures[0][0] == "blocked_by_policy"
    # step 2: team page relabelled restricted at the source -> chunks purged, tombstoned
    assert (
        env.sql(
            "SELECT count(*) FROM kb.chunks c JOIN kb.documents d "
            "ON d.id=c.document_id WHERE d.source_id='111'"
        )[0][0]
        > 0
    )
    env.pages[0] = _page(
        "111",
        "Payment retry policy",
        env.pages[0]["body"]["storage"]["value"],
        restricted=True,
        when="2026-10-01T01:00:00.000Z",
    )
    env.ingest()
    assert (
        env.sql(
            "SELECT count(*) FROM kb.chunks c JOIN kb.documents d "
            "ON d.id=c.document_id WHERE d.source_id='111'"
        )[0][0]
        == 0
    )
    assert (
        env.sql("SELECT deleted_at IS NOT NULL FROM kb.documents WHERE source_id='111'")[0][0]
        is True
    )
    # step 3: nothing of it is returned
    payload = await _search(
        env, "payment worker retries failed transactions backoff", min_similarity=0.0
    )
    assert "111" not in json.dumps(payload.get("citations", []))


async def test_TC_049_rerun_unchanged_and_changed_does_not_duplicate(env):
    env.ingest()
    count = env.sql("SELECT count(*) FROM kb.documents WHERE source_id='111'")[0][0]
    chunks = env.sql("SELECT count(*) FROM kb.chunks")[0][0]
    env.ingest()
    assert env.sql("SELECT count(*) FROM kb.documents WHERE source_id='111'")[0][0] == count == 1
    assert env.sql("SELECT count(*) FROM kb.chunks")[0][0] == chunks
    env.pages[0] = _page(
        "111",
        "Payment retry policy",
        "<h1>Payment retry policy</h1><p>Now retries five times with linear backoff.</p>",
        when="2026-10-01T02:00:00.000Z",
    )
    env.ingest()
    assert env.sql("SELECT count(*) FROM kb.documents WHERE source_id='111'")[0][0] == 1
    assert env.sql("SELECT count(*) FROM kb.chunks WHERE content ILIKE '%%five times%%'")[0][0] >= 1
    assert (
        env.sql("SELECT count(*) FROM kb.chunks WHERE content ILIKE '%%three times%%'")[0][0] == 0
    )


async def test_TC_067_status_json_reports_staleness(env):
    env.ingest()
    proc = env.cli("status", "--json")
    rows = {r["source_type"]: r for r in json.loads(proc.stdout)["sources"]}
    assert (
        rows["confluence"]["document_count"] == 1
        and rows["confluence"]["staleness_hours"] is not None
    )
    assert rows["gitlab"]["last_success_at"] is None  # never ingested -> reported, not an error


async def test_TC_074_prune_requires_explicit_retention_and_dry_run_is_default(env):
    env.ingest()
    refused = env.cli("prune", expect=(2,))
    assert "--older-than" in refused.stderr
    before = env.sql("SELECT count(*) FROM kb.documents")[0][0]
    dry = env.cli("prune", "--tombstoned", "--older-than", "30d", "--json")
    body = json.loads(dry.stdout)
    assert body["dry_run"] is True and "documents_deleted" in body
    assert env.sql("SELECT count(*) FROM kb.documents")[0][0] == before


async def test_TC_068_list_sources_empty_db_then_populated(env):
    async with env.pg_server() as s:
        empty = result_payload(await s.call_tool("kb_list_sources", {}))
    assert empty["status"] == "empty"
    env.ingest()
    async with env.pg_server() as s:
        full = result_payload(await s.call_tool("kb_list_sources", {}))
    assert full["status"] == "ok"
    assert full["meta"]["data_freshness"]["staleness_hours"] is not None
    assert [i for i in full["items"] if i.get("source_type") == "confluence"]


async def test_TC_077_list_sources_hides_all_tombstoned_source(env):
    env.ingest()
    env.sql("UPDATE kb.documents SET deleted_at = now() WHERE source_type='confluence'")
    async with env.pg_server() as s:
        out = result_payload(await s.call_tool("kb_list_sources", {}))
    assert not [i for i in out.get("items", []) if i.get("source_type") == "confluence"]


# -- Journey 3 (TC-052, TC-053) ------------------------------------------------------------------


@pytest.fixture
def moto_url():
    server = ThreadedMotoServer(port=0, verbose=False)
    server.start()
    host, port = server.get_host_and_port()
    url = f"http://{host}:{port}"
    httpx.post(f"{url}/moto-api/reset")
    yield url
    server.stop()


async def test_TC_052_semantic_synthesis_cites_original_urls_and_queue(env, moto_url):
    env.ingest()
    sqs = boto3.client(
        "sqs",
        region_name="us-east-1",
        endpoint_url=moto_url,
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )
    queue_url = sqs.create_queue(QueueName="payment-retry-dlq")["QueueUrl"]
    async with (
        env.pg_server() as kb,
        open_server("sqs_sns", {"MCP_SQS_SNS_ENDPOINT_URL": moto_url}) as mq,
    ):
        prompt = await kb.get_prompt(
            "semantic_synthesis", {"question": "payment retry and dead letter queue"}
        )
        text = prompt.messages[0].content.text
        exposed = {t.name for s in (kb, mq) for t in (await s.list_tools()).tools}
        for tool in re.findall(r"\b(?:kb|sqs|sns)_[a-z_]+", text):
            assert tool in exposed, tool
        found = result_payload(
            await kb.call_tool(
                "kb_semantic_search",
                {
                    "query": "payment worker retries failed transactions backoff",
                    "top_k": 8,
                    "min_similarity": 0.3,
                },
            )
        )
        queue = result_payload(
            await mq.call_tool("sqs_get_queue_attributes", {"queue_name": "payment-retry-dlq"})
        )
    assert found["status"] == "ok"
    assert all(c["uri"].startswith("https://wiki.example.test/") for c in found["citations"])
    assert queue["status"] == "ok", queue
    assert "payment-retry-dlq" in json.dumps(queue["citations"])
    assert queue_url


async def test_TC_053_off_topic_question_yields_explicit_empty(env):
    env.ingest()
    payload = await _search(env, "zebra quantum marmalade xylophone", top_k=8, min_similarity=0.3)
    assert payload["status"] == "empty" and payload["citations"] == []
