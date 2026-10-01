"""kb tool layer: input bounds, the HNSW post-filter honesty rules, redaction and budget.

Three read operations over the `kb` schema (FR-011, FR-013, NFR-004):

* :meth:`PgVectorReadApi.semantic_search` embeds the question with the **same provider** as the
  ingest pipeline and runs one parameterised ANN query. `status=empty` is only reported when the
  best similarity *without the user's filters* is below `min_similarity`; when the filters are
  what removed the matches, the warning says so (ADR-0011 A3, FR-011/AC-002, FR-013/AC-002);
* :meth:`get_document` re-assembles one document from its chunks (tombstoned -> `not_found`);
* :meth:`list_sources` reports per-source counts and freshness (NFR-004).

Content comes back redacted and wrapped as untrusted **here**, never from the database: `kb.chunks`
stores redacted-but-unwrapped text (ADR-0015 A2).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from mcp_common.config import CommonSettings
from mcp_common.envelope import Citation, DataFreshness, SourceType
from mcp_common.errors import ErrorCode, ToolError
from mcp_common.render import SOURCE_LABELS
from mcp_common.runtime import BoundedExecutor
from mcp_common.tooling import (
    CallState,
    ToolOutcome,
    build_result,
    invalid_input,
    not_found_result,
)
from mcp_ingest.embedding import EmbeddingError
from mcp_ingest.ports import EmbeddingProvider
from pgvector import Vector

from mcp_pgvector.client import SOURCE, PgVectorClient, ReadTx
from mcp_pgvector.settings import Settings

__all__ = ["PgVectorReadApi"]

_SOURCE_TYPES = frozenset(t.value for t in SourceType)
_RUN_STATUSES = frozenset({"running", "success", "partial", "failed"})
_OVERFETCH = 4  # pgvector < 0.8 fallback: ask for top_k x 4, then trim (ADR-0011 A3)


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value else None


def _is_http(url: str | None) -> bool:
    return bool(url) and urlsplit(str(url)).scheme in ("http", "https")


def _origin(sample_uri: str | None) -> str | None:
    """`https://host/path?x` -> `https://host/` (the source system a document came from)."""
    if not _is_http(sample_uri):
        return None
    parts = urlsplit(str(sample_uri))
    return f"{parts.scheme}://{parts.netloc}/"


def _hours(since: datetime | None, now: datetime) -> float | None:
    if since is None:
        return None
    return round(max((now - since).total_seconds(), 0.0) / 3600, 1)


def _label(source_type: str) -> str:
    try:
        return SOURCE_LABELS[SourceType(source_type)]
    except ValueError:
        return source_type


class PgVectorReadApi:
    def __init__(
        self,
        client: PgVectorClient,
        provider: EmbeddingProvider,
        common: CommonSettings,
        settings: Settings | None = None,
        *,
        executor: BoundedExecutor | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._client = client
        self._provider = provider
        self._common = common
        self._settings = settings
        self._executor = executor or BoundedExecutor(max_workers=2)
        self._now = now or (lambda: datetime.now(UTC))

    # -- helpers -------------------------------------------------------------------------------

    def _state(self) -> CallState:
        return CallState(
            self._common.max_output_bytes, redact_disabled=self._common.redact_disabled
        )

    @staticmethod
    def _check_types(source_types: Sequence[str]) -> list[str]:
        if len(source_types) > 10:  # noqa: PLR2004
            raise invalid_input("source_types", "tối đa 10 giá trị", SOURCE)
        if len(set(source_types)) != len(source_types):
            raise invalid_input("source_types", "các giá trị phải khác nhau", SOURCE)
        unknown = [t for t in source_types if t not in _SOURCE_TYPES]
        if unknown:
            raise invalid_input("source_types", f"không hợp lệ: {', '.join(unknown)}", SOURCE)
        return list(source_types)

    async def _embed_query(self, query: str) -> list[float]:
        try:
            return await self._executor.run(self._provider.embed_query, query, source=SOURCE)
        except ToolError:
            raise
        except EmbeddingError as exc:
            raise ToolError(
                ErrorCode.UPSTREAM_ERROR,
                f"Không tạo được embedding cho câu hỏi: {str(exc)[:300]}",
                SOURCE, False,
                details={"hint": "kiểm tra MCP_INGEST_EMBEDDING_* (provider/model/url)"},
            ) from exc  # fmt: skip

    async def warm_up(self) -> None:
        """Load a lazily-initialised local model before the first real question (best effort)."""
        try:
            await self._executor.run(self._provider.embed_query, "warm-up", source=SOURCE)
        except Exception:  # noqa: BLE001 - the first real query reports the real error
            return

    def _freshness_from(self, rows: list[dict[str, Any]], *, conservative: bool) -> DataFreshness:
        times = [r["last_ingested_at"] for r in rows if r.get("last_ingested_at")]
        chosen = (min(times) if conservative else max(times)) if times else None
        return DataFreshness(
            last_ingested_at=chosen,
            staleness_hours=_hours(chosen, self._now()),
            embedding_model=self._provider.model_id,
        )

    async def _freshness(
        self, tx: ReadTx, source_types: list[str], *, conservative: bool = True
    ) -> DataFreshness:
        rows = await tx.fetch("freshness", {"source_types": source_types})
        return self._freshness_from(rows, conservative=conservative)

    # -- kb_semantic_search --------------------------------------------------------------------

    async def semantic_search(
        self,
        *,
        query: str,
        top_k: int = 8,
        min_similarity: float = 0.30,
        source_types: Sequence[str] = (),
        container: str | None = None,
        updated_after: datetime | None = None,
        max_chars_per_chunk: int = 2000,
    ) -> ToolOutcome:
        if not 3 <= len(query) <= 4096:  # noqa: PLR2004
            raise invalid_input("query", "độ dài phải từ 3 đến 4096 ký tự", SOURCE)
        if not 1 <= top_k <= 50:  # noqa: PLR2004
            raise invalid_input("top_k", "phải nằm trong khoảng 1..50", SOURCE)
        if not 0.0 <= min_similarity <= 1.0:
            raise invalid_input("min_similarity", "phải nằm trong khoảng 0..1", SOURCE)
        if not 200 <= max_chars_per_chunk <= 8000:  # noqa: PLR2004
            raise invalid_input("max_chars_per_chunk", "phải nằm trong khoảng 200..8000", SOURCE)
        types = self._check_types(source_types)
        if updated_after is not None and updated_after.tzinfo is None:
            raise invalid_input("updated_after", "thiếu timezone (ví dụ hậu tố Z)", SOURCE)
        filtered = bool(types or container or updated_after)
        call = self._state()
        model = self._provider.model_id
        vector = Vector(await self._embed_query(query))
        caps = await self._client.capabilities()
        fetch_limit = top_k if caps.supports_iterative_scan else top_k * _OVERFETCH
        warnings: list[str] = []
        async with self._client.read_tx() as tx:
            await tx.set_local("hnsw.ef_search", str(min(1000, max(64, 8 * top_k))))
            if caps.supports_iterative_scan:
                await tx.set_local("hnsw.iterative_scan", "relaxed_order")
            rows = await tx.fetch(
                "search",
                {
                    "query": vector, "model": model, "source_types": types,
                    "container": container, "updated_after": updated_after, "limit": fetch_limit,
                },
            )  # fmt: skip
            rows = sorted(rows, key=lambda r: r["similarity"], reverse=True)
            hits = [r for r in rows if r["similarity"] >= min_similarity][:top_k]
            if not hits:
                warnings.append(await self._explain_empty(
                    tx, rows=rows, filtered=filtered, vector=vector, model=model, top_k=top_k,
                    fetch_limit=fetch_limit, min_similarity=min_similarity,
                ))  # fmt: skip
            scope = sorted({r["source_type"] for r in hits}) or types
            freshness = await self._freshness(tx, scope)
        items: list[dict[str, Any]] = []
        citations: list[Citation] = []
        for row in hits:
            if call.budget.exhausted:
                call.truncated_elsewhere = True
                call.warn("đã đạt max_bytes; giảm top_k hoặc max_chars_per_chunk")
                break
            built = self._map_chunk(row, call, max_chars_per_chunk, len(citations))
            if built is None:
                warnings.append(f"bỏ qua chunk {row['chunk_id']}: source_type không hợp lệ")
                continue
            items.append(built[0])
            citations.append(built[1])
        echo: dict[str, Any] = {"top_k": top_k, "min_similarity": min_similarity}
        if types:
            echo["source_types"] = types
        if container:
            echo["container"] = container
        if updated_after:
            echo["updated_after"] = _iso(updated_after)
        result = build_result(
            SourceType.PGVECTOR, items, citations, started=call.started, query_echo=echo,
            truncated=call.truncated, warnings=[*warnings, *call.warnings],
            redactions=call.counter.count,
        )  # fmt: skip
        result.meta.data_freshness = freshness
        return ToolOutcome(result, query_description="đoạn tài liệu đã index khớp câu hỏi")

    async def _explain_empty(
        self,
        tx: ReadTx,
        *,
        rows: list[dict[str, Any]],
        filtered: bool,
        vector: Vector,
        model: str,
        top_k: int,
        fetch_limit: int,
        min_similarity: float,
    ) -> str:
        """Why empty: nothing indexed, nothing similar, or the filters (ADR-0011 A3)."""
        if filtered:
            reference = sorted(
                await tx.fetch("search_unfiltered", {"query": vector, "model": model,
                                                     "limit": fetch_limit}),
                key=lambda r: r["similarity"], reverse=True,
            )  # fmt: skip
        else:
            reference = rows
        if not reference:
            return (
                f"kho kb chưa có chunk nào được index bằng model '{model}' — "
                "không có dữ liệu index (chạy mcp-ingest run hoặc reembed)"
            )
        best = reference[0]["similarity"]
        if best < min_similarity:
            return (
                f"best_similarity={best:.2f} < min_similarity={min_similarity:.2f} — "
                "không có dữ liệu index phù hợp"
            )
        excluded = len([r for r in reference if r["similarity"] >= min_similarity][:top_k])
        return (
            f"filters may have excluded matches; thử lại không có source_types/container — "
            f"bộ lọc source_types/container/updated_after đã loại {excluded} kết quả "
            f"(best_similarity={best:.2f} ≥ min_similarity={min_similarity:.2f} khi không lọc)"
        )

    def _map_chunk(
        self, row: dict[str, Any], call: CallState, max_chars: int, ref: int
    ) -> tuple[dict[str, Any], Citation] | None:
        source_type = str(row["source_type"])
        if source_type not in _SOURCE_TYPES:
            return None
        content = str(row["content"])
        cut = content[:max_chars]
        already_cut = call.budget.truncated
        wrapped = call.text(cut, source_type, f"{row['source_id']}#{row['chunk_index']}")
        truncated = len(content) > max_chars or (call.budget.truncated and not already_cut)
        title = call.plain(row["title"]) if row.get("title") else None
        heading = call.plain(row["heading_path"]) if row.get("heading_path") else None
        item = {
            "chunk_id": str(row["chunk_id"]),
            "document_id": str(row["document_id"]),
            "chunk_index": row["chunk_index"],
            "similarity": max(0.0, min(1.0, float(row["similarity"]))),
            "content": wrapped or "",
            "heading_path": heading,
            "source_type": source_type,
            "source_id": row["source_id"],
            "source_uri": row["source_uri"],
            "title": title,
            "container": row.get("container"),
            "source_updated_at": _iso(row.get("source_updated_at")),
            "ingested_at": _iso(row.get("ingested_at")),
            "embedding_model": row["embedding_model"],
            "truncated": truncated,
            "citation_ref": ref,
        }
        where = f" ({_label(source_type)}{' ' + row['container'] if row.get('container') else ''})"
        citation = Citation(
            source_type=SourceType(source_type),
            label=f"{heading or title or row['source_id']}{where}"[:512],
            uri=row["source_uri"] if _is_http(row["source_uri"]) else None,
            locator={
                "document_id": str(row["document_id"]),
                "chunk_index": row["chunk_index"],
                "source_uri": row["source_uri"],
            },
            retrieved_at=self._now(),
        )
        return item, citation

    # -- kb_get_document -----------------------------------------------------------------------

    async def get_document(
        self,
        *,
        document_id: str | None = None,
        source_uri: str | None = None,
        max_chars: int = 20000,
    ) -> ToolOutcome:
        if not 500 <= max_chars <= 100000:  # noqa: PLR2004
            raise invalid_input("max_chars", "phải nằm trong khoảng 500..100000", SOURCE)
        parsed_id: uuid.UUID | None = None
        if document_id:
            try:
                parsed_id = uuid.UUID(document_id)
            except ValueError as exc:
                raise invalid_input("document_id", "không phải UUID hợp lệ", SOURCE) from exc
        elif source_uri:
            parts = urlsplit(source_uri)
            if len(source_uri) > 2048 or not parts.scheme or not parts.netloc:  # noqa: PLR2004
                raise invalid_input("source_uri", "phải là URL hợp lệ (tối đa 2048 ký tự)", SOURCE)
        else:
            raise invalid_input("document_id", "phải có document_id hoặc source_uri", SOURCE)
        call = self._state()
        echo: dict[str, Any] = {
            k: v for k, v in (("document_id", document_id), ("source_uri", source_uri)) if v
        }
        echo["max_chars"] = max_chars
        async with self._client.read_tx() as tx:
            docs = await tx.fetch(
                "document_by_key",
                {"document_id": parsed_id, "source_uri": None if parsed_id else source_uri},
            )
            if not docs:
                return ToolOutcome(
                    not_found_result(SourceType.PGVECTOR, started=call.started, query_echo=echo),
                    identifier=f"Document '{document_id or source_uri}'",
                )
            doc = docs[0]
            chunks = await tx.fetch("document_chunks", {"document_id": doc["document_id"]})
            freshness = await self._freshness(tx, [doc["source_type"]])
        source_type = str(doc["source_type"])
        if source_type not in _SOURCE_TYPES:
            raise ToolError(
                ErrorCode.INTERNAL, "Document có source_type không hợp lệ.", SOURCE, False
            )
        joined = "\n\n".join(str(c["content"]) for c in chunks)
        cut = joined[:max_chars]
        char_truncated = len(joined) > max_chars
        already_cut = call.budget.truncated
        wrapped = call.text(cut, source_type, str(doc["source_id"]))
        truncated = char_truncated or (call.budget.truncated and not already_cut)
        warnings = []
        if char_truncated:
            warnings.append(f"content truncated at {max_chars} chars")
        title = call.plain(doc["title"]) if doc.get("title") else None
        item = {
            "document_id": str(doc["document_id"]),
            "source_type": source_type,
            "source_id": doc["source_id"],
            "source_uri": doc["source_uri"],
            "title": title,
            "container": doc.get("container"),
            "author": doc.get("author"),
            "source_updated_at": _iso(doc.get("source_updated_at")),
            "ingested_at": _iso(doc.get("ingested_at")),
            "chunk_count": int(chunks[0]["chunk_count"]) if chunks else 0,
            "content": wrapped or "",
            "truncated": truncated,
            "citation_ref": 0,
        }
        where = f" ({_label(source_type)}{' ' + doc['container'] if doc.get('container') else ''})"
        citation = Citation(
            source_type=SourceType(source_type),
            label=f"{title or doc['source_id']}{where}"[:512],
            uri=doc["source_uri"] if _is_http(doc["source_uri"]) else None,
            locator={"document_id": str(doc["document_id"]), "source_uri": doc["source_uri"]},
            retrieved_at=self._now(),
        )
        result = build_result(
            SourceType.PGVECTOR, [item], [citation], started=call.started, query_echo=echo,
            truncated=truncated, warnings=[*warnings, *call.warnings],
            redactions=call.counter.count,
        )  # fmt: skip
        result.meta.data_freshness = freshness
        return ToolOutcome(result, identifier=f"Document '{document_id or source_uri}'")

    # -- kb_list_sources -----------------------------------------------------------------------

    async def list_sources(self, *, source_types: Sequence[str] = ()) -> ToolOutcome:
        types = self._check_types(source_types)
        call = self._state()
        async with self._client.read_tx() as tx:
            rows = await tx.fetch("list_sources", {"source_types": types})
        now = self._now()
        items: list[dict[str, Any]] = []
        citations: list[Citation] = []
        warnings: list[str] = []
        freshness_rows: list[dict[str, Any]] = []
        for row in rows:
            source_type = str(row["source_type"])
            if source_type not in _SOURCE_TYPES:
                warnings.append(f"bỏ qua nguồn '{source_type}' không thuộc contract")
                continue
            reference = row.get("last_success_at") or row.get("last_ingested_at")
            staleness = _hours(reference, now)
            status = row.get("last_run_status")
            origin = _origin(row.get("sample_uri"))
            if int(row["document_count"]) == 0 or origin is None:
                # Nothing to cite (contract invariant 6: pgvector citations resolve to a URL), so
                # the source is reported in a warning rather than as an item (FR-013/AC-002).
                warnings.append(
                    f"{source_type}: chưa có document nào được index"
                    + (f" (run gần nhất {status})" if status else "")
                )
                continue
            models = [m for m in str(row.get("embedding_models") or "").split(",") if m]
            if len(models) > 1:
                warnings.append(
                    f"{source_type}: nhiều embedding_model trong kho ({', '.join(models)}) — "
                    "cần mcp-ingest reembed"
                )
            if status in ("failed", "partial"):
                warnings.append(
                    f"{source_type}: run gần nhất {status} — dữ liệu có thể cũ hơn bình thường"
                )
            if row.get("last_success_at") is None:
                warnings.append(f"{source_type}: chưa có lần ingest nào thành công")
            freshness_rows.append({"last_ingested_at": reference})
            items.append(
                {
                    "source_type": source_type,
                    "document_count": int(row["document_count"]),
                    "chunk_count": int(row["chunk_count"]),
                    "last_ingested_at": _iso(row.get("last_ingested_at")),
                    "last_success_at": _iso(row.get("last_success_at")),
                    "staleness_hours": staleness,
                    "last_run_status": status if status in _RUN_STATUSES else None,
                    "embedding_model": ",".join(models) if models else None,
                    "citation_ref": len(citations),
                }
            )
            when = f"{reference.astimezone(UTC):%Y-%m-%dT%H:%MZ}" if reference else "chưa có"
            age = f" ({staleness}h)" if staleness is not None else ""
            citations.append(
                Citation(
                    source_type=SourceType(source_type),
                    label=(
                        f"kb: {source_type} — {row['document_count']} docs, cập nhật {when}{age}"
                    ),
                    uri=origin,
                    locator={"source_type": source_type},
                    retrieved_at=now,
                )
            )
        result = build_result(
            SourceType.PGVECTOR, items, citations, started=call.started,
            query_echo={"source_types": types} if types else {}, warnings=warnings,
        )  # fmt: skip
        # Top-level freshness = the most recent successful ingest in scope (per-source numbers
        # are in the items); `kb_semantic_search` reports the conservative (oldest) one instead.
        result.meta.data_freshness = self._freshness_from(freshness_rows, conservative=False)
        return ToolOutcome(
            result, query_description="nguồn nào đã được index trong kho embedding (kb)"
        )
