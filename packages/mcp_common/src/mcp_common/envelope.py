"""T-008: the uniform tool result envelope (ADR-0004) — `ToolResult`/`Citation`/`Meta`.

Mirrors `components.schemas.{SourceType,ResultStatus,Citation,Meta,ItemBase,
ToolResultBase}` in `api-contract.yaml` field-for-field.

The three invariants the contract itself encodes as JSON Schema `if/then` (see
`ToolResultBase` description in the contract) are enforced here too, at
construction time, so a violation raises immediately instead of only failing a
later contract test:

1. `status` ∈ {`ok`, `partial`} ⇒ `citations` non-empty (BR-002, FR-015).
2. `status` ∈ {`empty`, `not_found`} ⇒ `items` and `citations` both empty.
3. `meta.has_more = true` ⇒ `meta.next_cursor` is not `None`.

The remaining invariants the contract calls out as *not* JSON-Schema-encodable
(citation_ref bounds, `meta.returned == len(items)`, per-source-type `uri` rules,
the `status=partial` + empty-items "range citation" case) are deliberately **not**
checked here — they live in `mcp_common.testing.assert_envelope_invariants()` (T-014),
which both BE's unit tests and QA's harness reuse.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "SourceType",
    "ResultStatus",
    "Citation",
    "DataFreshness",
    "Meta",
    "ItemBase",
    "ToolResult",
]


class SourceType(StrEnum):
    """Matches `components.schemas.SourceType` exactly."""

    CONFLUENCE = "confluence"
    GITLAB = "gitlab"
    OPENSEARCH = "opensearch"
    KIBANA = "kibana"
    CLOUDWATCH = "cloudwatch"
    KAFKA = "kafka"
    REDIS = "redis"
    SQS = "sqs"
    SNS = "sns"
    PGVECTOR = "pgvector"


class ResultStatus(StrEnum):
    """Matches `components.schemas.ResultStatus`. A *successful*-result status —
    real errors never use this enum (see `mcp_common.errors.ErrorCode`)."""

    OK = "ok"
    EMPTY = "empty"
    NOT_FOUND = "not_found"
    PARTIAL = "partial"


class Citation(BaseModel):
    """Matches `components.schemas.Citation`."""

    model_config = ConfigDict(extra="forbid")

    source_type: SourceType
    label: str = Field(min_length=1, max_length=512)
    uri: str | None = Field(default=None, max_length=2048)
    locator: dict[str, Any] = Field(default_factory=dict)
    retrieved_at: datetime | None = None


class DataFreshness(BaseModel):
    """Matches `components.schemas.Meta.properties.data_freshness` (mcp-pgvector only)."""

    model_config = ConfigDict(extra="forbid")

    last_ingested_at: datetime | None = None
    staleness_hours: float | None = Field(default=None, ge=0)
    embedding_model: str | None = None


class Meta(BaseModel):
    """Matches `components.schemas.Meta`."""

    model_config = ConfigDict(extra="forbid")

    source: SourceType
    returned: int = Field(ge=0)
    has_more: bool
    next_cursor: str | None = None
    truncated: bool = False
    elapsed_ms: int = Field(ge=0)
    as_of: datetime
    query_echo: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    redactions: int = Field(default=0, ge=0)
    data_freshness: DataFreshness | None = None

    @model_validator(mode="after")
    def _has_more_requires_next_cursor(self) -> Meta:
        if self.has_more and not self.next_cursor:
            raise ValueError(
                "meta.has_more=true requires a non-empty meta.next_cursor (contract invariant 3)"
            )
        return self


class ItemBase(BaseModel):
    """Matches `components.schemas.ItemBase`. Per-tool item schemas (built in
    Phase 1+, e.g. `ConfluencePage`) extend this with `allOf` in the contract; here
    they simply subclass and add their own fields."""

    model_config = ConfigDict(extra="allow")

    citation_ref: int = Field(ge=0)


class ToolResult(BaseModel):
    """Matches `components.schemas.ToolResultBase`.

    `items` is typed as plain dicts here because the contract overrides it with a
    concrete item schema (`allOf`) on every individual operation — those concrete
    schemas belong to each server's Phase 1/2/3 tool definitions, not to this shared
    envelope.
    """

    model_config = ConfigDict(extra="forbid")

    status: ResultStatus
    items: list[dict[str, Any]] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    meta: Meta

    @model_validator(mode="after")
    def _check_status_invariants(self) -> ToolResult:
        if self.status in (ResultStatus.OK, ResultStatus.PARTIAL) and not self.citations:
            raise ValueError(
                "citations must be non-empty when status is 'ok' or 'partial' (BR-002/FR-015); "
                "even a status=partial result with items=[] needs a range citation "
                "(log group + window, or index + range)."
            )
        if self.status in (ResultStatus.EMPTY, ResultStatus.NOT_FOUND) and (
            self.items or self.citations
        ):
            raise ValueError(
                "items and citations must both be empty when status is 'empty' or 'not_found'"
            )
        return self
