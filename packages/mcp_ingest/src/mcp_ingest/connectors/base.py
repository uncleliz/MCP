"""`SourceConnector` protocol, `SourceDocument` and the sync/async bridge (T-069).

Connectors reuse the *transport* layer (`client.py`) of the source packages — read-only allowlist,
timeouts, retries, deny-glob — and never `read_api.py`, whose tool bounds (limit caps, max chars)
would truncate a crawl (ADR-0007 A3, ADR-0012 A4; enforced by `tests/test_connector_isolation.py`).

The pipeline is a synchronous CLI while the source clients are async; :class:`AsyncBridge` owns
one event loop per connector so the async HTTP clients stay bound to a single loop.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine, Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol, TypeVar, runtime_checkable

from mcp_ingest.identity import Visibility

__all__ = [
    "AsyncBridge",
    "ConnectorStatus",
    "Cursor",
    "SourceConnector",
    "SourceDocument",
]

T = TypeVar("T")
SourceFormat = Literal["plain", "html", "markdown"]
CrawlMode = Literal["incremental", "full"]


@dataclass(frozen=True)
class SourceDocument:
    """One crawled item with everything the citation (BR-005) and the policy gate need."""

    source_type: str
    source_id: str  # stable id, see identity.py / spike S5
    source_uri: str  # the ORIGINAL URL a human can open: this is the citation
    raw_content: str
    source_format: SourceFormat = "plain"
    source_updated_at: datetime | None = None  # the watermark of this document
    title: str | None = None
    container: str | None = None  # space key / project path / index alias
    author: str | None = None
    visibility: Visibility = "restricted"  # default-deny (ADR-0016 A1)
    path: str | None = None  # repository path, matched against the deny-glob
    # Set by a connector that refused to even fetch the content (e.g. deny-glob on the path).
    blocked_reason: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Cursor:
    """Incremental watermark. The boundary is INCLUSIVE (`>=`): a document sitting exactly on the
    watermark is fetched again and absorbed by the content hash (ADR-0012 A2)."""

    watermark: datetime | None = None

    def to_json(self) -> dict[str, Any]:
        return {"watermark": self.watermark.isoformat() if self.watermark else None}

    @classmethod
    def from_json(cls, raw: Any) -> Cursor | None:
        if not isinstance(raw, dict) or not raw.get("watermark"):
            return None
        return cls(datetime.fromisoformat(str(raw["watermark"])))


@dataclass(frozen=True)
class ConnectorStatus:
    enabled: bool
    configured: bool
    missing_env: list[str] = field(default_factory=list)


@runtime_checkable
class SourceConnector(Protocol):
    source_type: str
    name: str  # class name shown by `mcp-ingest sources`
    operations_used: tuple[str, ...]  # client operations this connector calls (readonly test)

    def status(self) -> ConnectorStatus: ...

    def iter_documents(
        self, cursor: Cursor | None, mode: CrawlMode, *, limit: int | None = None
    ) -> Iterator[SourceDocument]:
        """Yield documents whose watermark is `>= cursor` (all of them in `full` mode)."""
        ...

    def fetch_documents(self, source_ids: Iterable[str]) -> Iterator[SourceDocument]:
        """Re-fetch specific documents (`run --retry-failed`); ids that no longer exist at the
        source are simply not yielded."""
        ...

    def close(self) -> None: ...


class AsyncBridge:
    """Runs coroutines of the async source client on one private event loop."""

    def __init__(self) -> None:
        self._runner: asyncio.Runner | None = None

    def run(self, coro: Coroutine[Any, Any, T]) -> T:
        if self._runner is None:
            self._runner = asyncio.Runner()
        return self._runner.run(coro)

    def close(self, *finalizers: Coroutine[Any, Any, Any]) -> None:
        if self._runner is None:
            for coro in finalizers:
                coro.close()
            return
        try:
            for coro in finalizers:
                self._runner.run(coro)
        finally:
            self._runner.close()
            self._runner = None
