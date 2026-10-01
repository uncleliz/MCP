"""Test doubles and builders for the mcp-ingest pipeline tests."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
from mcp_ingest.connectors.base import ConnectorStatus, CrawlMode, Cursor, SourceDocument
from mcp_ingest.embedding.fake import DeterministicFakeProvider
from mcp_ingest.pipeline.chunk import ChunkConfig
from mcp_ingest.pipeline.run import RunContext, RunOptions, run_ingest
from mcp_ingest.reports import IngestRunReport

T0 = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
MODEL = "fake/hashed-bow"
CONFLUENCE_BASE = "https://wiki.example.com"


def provider(model_id: str = MODEL, dimensions: int = 1024) -> DeterministicFakeProvider:
    return DeterministicFakeProvider(dimensions=dimensions, model_id=model_id)


def as_user(dsn: str, user: str) -> str:
    return dsn.replace("postgres@", f"{user}@", 1)


def doc(
    source_id: str = "100",
    *,
    source_type: str = "confluence",
    content: str = "<h1>Title</h1><p>Retry policy: three attempts with backoff.</p>",
    fmt: str = "html",
    minutes: int = 0,
    title: str | None = "Payment retry policy",
    uri: str | None = None,
    visibility: str = "team",
    container: str | None = "PAY",
    path: str | None = None,
    blocked_reason: str | None = None,
) -> SourceDocument:
    return SourceDocument(
        source_type=source_type,
        source_id=source_id,
        source_uri=uri or f"{CONFLUENCE_BASE}/pages/viewpage.action?pageId={source_id}",
        raw_content=content,
        source_format=fmt,  # type: ignore[arg-type]
        source_updated_at=T0 + timedelta(minutes=minutes),
        title=title,
        container=container,
        author="an.nguyen",
        visibility=visibility,  # type: ignore[arg-type]
        path=path,
        blocked_reason=blocked_reason,
    )


class FakeConnector:
    """A scriptable `SourceConnector`: honours the inclusive watermark like a real one."""

    operations_used: tuple[str, ...] = ()

    def __init__(
        self,
        source_type: str = "confluence",
        documents: Iterable[SourceDocument] = (),
        *,
        die_after: int | None = None,
        die_with: Exception | None = None,
    ) -> None:
        self.source_type = source_type
        self.name = "FakeConnector"
        self.documents = list(documents)
        self.die_after = die_after
        self.die_with = die_with or ConnectionError("source unreachable")
        self.closed = False
        self.cursors_seen: list[Cursor | None] = []
        self.fetched: list[str] = []

    def status(self) -> ConnectorStatus:
        return ConnectorStatus(enabled=True, configured=True)

    def iter_documents(
        self, cursor: Cursor | None, mode: CrawlMode, *, limit: int | None = None
    ) -> Iterator[SourceDocument]:
        self.cursors_seen.append(cursor)
        floor = cursor.watermark if (cursor and mode == "incremental") else None
        count = 0
        for index, document in enumerate(self.documents):
            if self.die_after is not None and index >= self.die_after:
                raise self.die_with
            if floor is not None and document.source_updated_at is not None:
                if document.source_updated_at < floor:
                    continue
            yield document
            count += 1
            if limit is not None and count >= limit:
                return

    def fetch_documents(self, source_ids: Iterable[str]) -> Iterator[SourceDocument]:
        wanted = list(source_ids)
        self.fetched.extend(wanted)
        for document in self.documents:
            if document.source_id in wanted:
                yield document

    def close(self) -> None:
        self.closed = True


class PoisonProvider:
    """Fails (like a flaky embedding service) on chunks that contain POISON."""

    def __init__(self) -> None:
        self._inner = provider()
        self.model_id = self._inner.model_id
        self.dimensions = self._inner.dimensions
        self.max_input_tokens = self._inner.max_input_tokens
        self.normalize = True
        self.calls = 0

    def embed_documents(self, texts):
        self.calls += 1
        if any("POISON" in t for t in texts):
            raise TimeoutError("embedding service timed out")
        return self._inner.embed_documents(texts)

    def embed_query(self, text):  # pragma: no cover
        return self._inner.embed_query(text)


def make_ctx(
    dsn: str,
    *,
    model: str = MODEL,
    max_retries: int = 2,
    chunk: ChunkConfig | None = None,
    prov: Any = None,
) -> RunContext:
    return RunContext(
        dsn=dsn,
        provider=prov or provider(model),
        chunk_config=chunk or ChunkConfig(64, 8),
        deny_globs=["*.env", "*secret*", "*credential*", "*.pem", "id_rsa*"],
        max_retries=max_retries,
        backoff_s=0.0,
        sleep=lambda _s: None,
    )


def run(
    dsn: str,
    connectors: dict[str, FakeConnector],
    *,
    mode: str = "incremental",
    ctx: RunContext | None = None,
    explicit: bool = False,
    **options: Any,
) -> IngestRunReport:
    """Run the pipeline over `connectors` against the real database at `dsn`."""
    opts = RunOptions(sources=list(connectors), mode=mode, **options)  # type: ignore[arg-type]
    return run_ingest(
        ctx or make_ctx(dsn),
        opts,
        describe=lambda name: connectors[name].status(),
        build=lambda name: connectors[name],
        explicit_source=explicit,
    )


def q(dsn: str, sql: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    with psycopg.connect(dsn, autocommit=True) as conn:
        cursor = conn.execute(sql, params)
        return cursor.fetchall() if cursor.description else []


def scalar(dsn: str, sql: str, params: tuple[Any, ...] = ()) -> Any:
    return q(dsn, sql, params)[0][0]


Factory = Callable[..., Any]


def contract_schema(
    operation_id: str | None = None, *, schema: str | None = None
) -> dict[str, Any]:
    """A JSON Schema (with the contract's `components` attached so `$ref`s resolve) for either
    a named `components.schemas` entry or the 200 response of an operation."""
    from mcp_common.contract_testing import load_contract, operations_by_id

    contract = load_contract()
    if schema is not None:
        node: dict[str, Any] = {"$ref": f"#/components/schemas/{schema}"}
    else:
        assert operation_id is not None
        operation = operations_by_id(contract)[operation_id]
        node = dict(operation["responses"]["200"]["content"]["application/json"]["schema"])
    return {**node, "components": contract["components"]}


def assert_valid(payload: Any, **schema_args: Any) -> None:
    import jsonschema

    jsonschema.Draft202012Validator(contract_schema(**schema_args)).validate(payload)
