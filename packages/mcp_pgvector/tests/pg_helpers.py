"""Test doubles and data builders for mcp-pgvector.

* `FakeClient`/`FakeTx` stand in for `PgVectorClient` so the tool logic (empty-vs-filtered
  semantics, strategy per pgvector version, redaction, budgets, mapping) is unit-tested without a
  database; the SQL itself is exercised against a real PostgreSQL+pgvector in `test_db_*.py`.
* `seed` loads a small corpus embedded by the deterministic fake provider into a migrated database.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

from mcp_common.config import CommonSettings
from mcp_common.errors import NotPermittedError
from mcp_ingest.embedding.fake import DeterministicFakeProvider
from mcp_pgvector.client import ALLOWED_STATEMENTS, SOURCE, Capabilities, PgVectorClient
from mcp_pgvector.read_api import PgVectorReadApi
from mcp_pgvector.settings import Settings
from pydantic import SecretStr

MODEL = "fake/hashed-bow"
NOW = datetime(2026, 10, 1, 6, 0, tzinfo=UTC)
CONFLUENCE_URI = "https://wiki.example.com/pages/viewpage.action?pageId=123456"
GITLAB_URI = "https://gitlab.example.com/payments/worker/-/blob/main/README.md"
KAFKA_URI = "https://wiki.example.com/pages/viewpage.action?pageId=777"
DOC_ID = "3f2504e0-4f89-11d3-9a0c-0305e82c3301"


class FakeTx:
    def __init__(self, handlers: dict[str, Any], calls: list[tuple[str, dict[str, Any]]]) -> None:
        self._handlers = handlers
        self.calls = calls

    async def fetch(self, name: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        if name not in ALLOWED_STATEMENTS:
            raise NotPermittedError("no", source=SOURCE, operation=name)
        self.calls.append((name, dict(params or {})))
        handler = self._handlers.get(name, [])
        rows = handler(params or {}) if callable(handler) else handler
        return [dict(r) for r in rows]

    async def set_local(self, name: str, value: str) -> None:
        self.calls.append(("set_local", {"name": name, "value": value}))


class FakeClient:
    def __init__(
        self, handlers: dict[str, Any] | None = None, *, version: tuple[int, ...] | None = (0, 8, 0)
    ) -> None:
        self.handlers = handlers or {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.version = version
        self.transactions = 0

    async def capabilities(self) -> Capabilities:
        return Capabilities(self.version)

    @asynccontextmanager
    async def read_tx(self) -> AsyncIterator[FakeTx]:
        self.transactions += 1
        yield FakeTx(self.handlers, self.calls)

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]

    def params(self, name: str) -> list[dict[str, Any]]:
        return [p for n, p in self.calls if n == name]


def chunk_row(
    *,
    similarity: float = 0.71,
    source_type: str = "confluence",
    chunk_id: int = 918233,
    document_id: str = DOC_ID,
    chunk_index: int = 3,
    content: str = "Retry tối đa 3 lần với backoff luỹ thừa.",
    heading_path: str | None = "Payment retry policy > Backoff",
    title: str | None = "Payment retry policy",
    container: str | None = "PAY",
    source_id: str = "123456",
    source_uri: str = CONFLUENCE_URI,
    model: str = MODEL,
) -> dict[str, Any]:
    return {
        "chunk_id": chunk_id, "document_id": uuid.UUID(document_id), "chunk_index": chunk_index,
        "content": content, "heading_path": heading_path, "embedding_model": model,
        "source_type": source_type, "source_id": source_id, "source_uri": source_uri,
        "title": title, "container": container,
        "source_updated_at": NOW - timedelta(days=9), "ingested_at": NOW - timedelta(hours=3),
        "similarity": similarity,
    }  # fmt: skip


def freshness_row(source_type: str = "confluence", hours: float = 2.9) -> dict[str, Any]:
    return {"source_type": source_type, "last_ingested_at": NOW - timedelta(hours=hours)}


def provider(dimensions: int = 8, model_id: str = MODEL) -> DeterministicFakeProvider:
    return DeterministicFakeProvider(dimensions=dimensions, model_id=model_id)


# -- real-database corpus ----------------------------------------------------------------------

CORPUS: list[dict[str, Any]] = [
    {
        "source_type": "confluence", "source_id": "123456", "source_uri": CONFLUENCE_URI,
        "title": "Payment retry policy", "container": "PAY", "author": "an.nguyen",
        "updated": NOW - timedelta(days=9),
        "chunks": [
            (
                "Payment retry policy",
                "payment worker retry failed transactions three times backoff",
            ),
            (
                "Payment retry policy > Backoff",
                "exponential backoff doubles the delay between retry attempts",
            ),
            (
                "Payment retry policy > DLQ",
                "after the last retry the message moves to the dead letter queue",
            ),
        ],
    },
    {
        "source_type": "gitlab", "source_id": "42:blob:README.md", "source_uri": GITLAB_URI,
        "title": "payments/worker README", "container": "payments/worker", "author": None,
        "updated": NOW - timedelta(days=2),
        "chunks": [
            ("README", "deploy the worker with the pipeline release tag and watch the dashboard"),
        ],
    },
    {
        "source_type": "confluence", "source_id": "777", "source_uri": KAFKA_URI,
        "title": "Kafka lag runbook", "container": "OPS", "author": "binh.tran",
        "updated": NOW - timedelta(days=40),
        "chunks": [
            ("Kafka lag runbook", "kafka consumer group lag rebalance partitions slow processing"),
        ],
    },
]  # fmt: skip


def seed(
    admin_dsn: str,
    embedder: DeterministicFakeProvider,
    *,
    corpus: list[dict[str, Any]] | None = None,
    tombstone: bool = True,
) -> dict[str, str]:
    """Insert the corpus (+ one tombstoned document whose chunk is the best match for 'retry')."""
    import psycopg
    from pgvector.psycopg import register_vector

    ids: dict[str, str] = {}
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        register_vector(conn)
        run_id = conn.execute(
            "INSERT INTO kb.ingest_runs (source_type, status, finished_at) "
            "VALUES ('confluence', 'success', %s) RETURNING id",
            (NOW - timedelta(hours=3),),
        ).fetchone()[0]
        docs = list(corpus if corpus is not None else CORPUS)
        if tombstone and corpus is None:
            docs.append(
                {
                    "source_type": "confluence", "source_id": "dead", "title": "Deleted page",
                    "source_uri": "https://wiki.example.com/pages/viewpage.action?pageId=999",
                    "container": "PAY", "author": None, "updated": NOW,
                    "chunks": [("Deleted", "payment worker retry failed transactions three times")],
                    "deleted": True,
                }
            )  # fmt: skip
        for doc in docs:
            doc_id = conn.execute(
                "INSERT INTO kb.documents (source_type, source_id, source_uri, title, container, "
                "author, content_hash, source_updated_at, ingested_at, deleted_at, "
                "last_seen_run_id) VALUES (%s,%s,%s,%s,%s,%s,'h',%s,%s,%s,%s) RETURNING id",
                (
                    doc["source_type"], doc["source_id"], doc["source_uri"], doc["title"],
                    doc["container"], doc["author"], doc["updated"], NOW - timedelta(hours=3),
                    NOW if doc.get("deleted") else None, run_id,
                ),
            ).fetchone()[0]  # fmt: skip
            ids[doc["source_id"]] = str(doc_id)
            texts = [text for _heading, text in doc["chunks"]]
            for index, ((heading, text), vec) in enumerate(
                zip(doc["chunks"], embedder.embed_documents(texts), strict=True)
            ):
                conn.execute(
                    "INSERT INTO kb.chunks (document_id, chunk_index, content, heading_path, "
                    "embedding, embedding_model) VALUES (%s,%s,%s,%s,%s,%s)",
                    (doc_id, index, text, heading, vec, embedder.model_id),
                )
        conn.execute(
            "INSERT INTO kb.ingest_source_state (source_type, last_success_at, last_run_id) "
            "VALUES ('confluence', %s, %s)",
            (NOW - timedelta(hours=3), run_id),
        )
    return ids


Handler = Callable[[dict[str, Any]], list[dict[str, Any]]]


def make_api(client: object, common: CommonSettings, *, dimensions: int = 8) -> PgVectorReadApi:
    """A read API over a scripted client and the 8-d fake embedder (no database)."""
    return PgVectorReadApi(
        client,  # type: ignore[arg-type]
        provider(dimensions),
        common,
        now=lambda: NOW,
    )


def real_api(dsn: str, common: CommonSettings) -> PgVectorReadApi:
    """A read API over a real database connection and the 1024-d fake embedder."""
    settings = Settings(dsn=SecretStr(dsn))
    client = PgVectorClient(settings, common=common)
    return PgVectorReadApi(client, provider(1024), common, settings, now=lambda: NOW)


def as_user(dsn: str, user: str) -> str:
    return dsn.replace("postgres@", f"{user}@", 1)


DOC_ROW = {
    "document_id": uuid.UUID(DOC_ID), "source_type": "confluence", "source_id": "123456",
    "source_uri": CONFLUENCE_URI, "title": "Payment retry policy", "container": "PAY",
    "author": "an.nguyen", "source_updated_at": NOW - timedelta(days=9),
    "ingested_at": NOW - timedelta(hours=3),
}  # fmt: skip


def chunks_of(*texts: str) -> list[dict[str, Any]]:
    return [
        {"chunk_index": i, "content": t, "heading_path": None, "chunk_count": len(texts)}
        for i, t in enumerate(texts)
    ]
