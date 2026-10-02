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
    # CHG-001 E3 (T-096) — grounded envelope (ADR-0018), additive, backward-compatible.
    "GroundedStatus",
    "GroundingVerdict",
    "ClaimEvidence",
    "ClaimProvenance",
    "ClaimPosition",
    "Claim",
    "GroundingSummary",
    "GroundedResult",
    "UNKNOWN_MESSAGE",
    "CONFIDENCE_BASIS",
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
    JIRA = "jira"


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


# =============================================================================
# CHG-001 E3 — grounded envelope (ADR-0018 §5), additive on top of ToolResultBase.
#
# `GroundedResult` MIRRORS `components.schemas.GroundedResultBase`: it keeps `citations`/`meta`,
# replaces `items` with `claims[]`, and adds `grounding_summary`. The base `ToolResult` and the
# 9-source envelope above are UNTOUCHED (EB-002: no regression). The grounding *verdict* itself is
# assigned by the gate in E8 (T-106); this module provides the contract-faithful container plus the
# invariants the contract encodes, so a violation raises at construction instead of only failing a
# later contract test.
# =============================================================================

#: The fixed UNKNOWN message (ADR-0018 §6 GT-1, spec §40). A no-evidence claim always carries
#: exactly this text — never a fabricated answer.
UNKNOWN_MESSAGE = "Tôi không tìm thấy nguồn chính thức xác nhận thông tin này."

#: The explicit confidence label (L-002): confidence is evidence-strength, NOT P(claim true).
CONFIDENCE_BASIS = "evidence-strength (retrieval×agreement×freshness), NOT P(claim true)"


class GroundedStatus(StrEnum):
    """`ResultStatus` + `insufficient_evidence` (ADR-0018 §5). Kept separate from `ResultStatus`
    so the 9-source servers are untouched."""

    OK = "ok"
    EMPTY = "empty"
    NOT_FOUND = "not_found"
    PARTIAL = "partial"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class GroundingVerdict(StrEnum):
    """Per-claim verdict (ADR-0018). Invariants enforced regardless of the (TBD) threshold:
    no-evidence ⇒ UNKNOWN; ≥2 conflicting sources ⇒ CONFLICT; confidence never rescues a
    source-less claim."""

    FACT = "FACT"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    UNKNOWN = "UNKNOWN"
    CONFLICT = "CONFLICT"


class ClaimEvidence(BaseModel):
    """Matches `components.schemas.ClaimProvenance.properties.evidence` — resolvable to a chunk."""

    model_config = ConfigDict(extra="forbid")

    document_id: str
    chunk_id: str
    url: str | None = Field(default=None, max_length=2048)
    source_uri: str | None = Field(default=None, max_length=2048)


class ClaimProvenance(BaseModel):
    """Matches `components.schemas.ClaimProvenance`. Missing a required field ⇒ never FACT."""

    model_config = ConfigDict(extra="forbid")

    source: SourceType
    updated_time: datetime
    evidence: ClaimEvidence
    source_version: str | None = None
    owner: str | None = None
    synced_at: datetime | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)


class ClaimPosition(BaseModel):
    """Matches `components.schemas.ClaimPosition` — one side of a CONFLICT, with full provenance."""

    model_config = ConfigDict(extra="forbid")

    value: str = Field(max_length=2048)
    provenance: list[ClaimProvenance] = Field(min_length=1)


class Claim(BaseModel):
    """Matches `components.schemas.Claim`. Construction-time invariants mirror the contract's
    `if/then` so a malformed claim raises immediately (GT-1/GT-3/GT-4)."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(max_length=4096)
    grounding: GroundingVerdict
    confidence: float | None = Field(default=None, ge=0, le=1)
    confidence_basis: str | None = None
    provenance: list[ClaimProvenance] = Field(default_factory=list)
    positions: list[ClaimPosition] = Field(default_factory=list)
    authority_note: str | None = None
    message: str | None = None

    @model_validator(mode="after")
    def _check_verdict_invariants(self) -> Claim:
        if self.grounding in (GroundingVerdict.FACT, GroundingVerdict.LOW_CONFIDENCE):
            if not self.provenance:
                raise ValueError(
                    "a FACT/LOW_CONFIDENCE claim must carry at least one provenance entry "
                    "(ADR-0018 §6 GT-2/GT-3; no-evidence can never be FACT)"
                )
        if self.grounding is GroundingVerdict.UNKNOWN:
            if self.provenance:
                raise ValueError("an UNKNOWN claim must have empty provenance (GT-1)")
            if self.message != UNKNOWN_MESSAGE:
                raise ValueError(
                    "an UNKNOWN claim must carry the fixed message "
                    f"{UNKNOWN_MESSAGE!r} (ADR-0018 §6 GT-1)"
                )
        if self.grounding is GroundingVerdict.CONFLICT and len(self.positions) < 2:
            raise ValueError(
                "a CONFLICT claim must expose >=2 positions, each with provenance (GT-4)"
            )
        return self


class GroundingSummary(BaseModel):
    """Matches `components.schemas.GroundingSummary`."""

    model_config = ConfigDict(extra="forbid")

    fact: int = Field(ge=0)
    low_confidence: int = Field(ge=0)
    unknown: int = Field(ge=0)
    conflict: int = Field(ge=0)
    reranker: str = "enabled"
    #: `uncalibrated` until the FACT↔LOW_CONFIDENCE threshold τ is set on a real golden-set
    #: (ADR-0018 D1, NFR-010 UNVERIFIED, blocked by HF egress). Never pretend otherwise.
    calibration_status: str = "uncalibrated"

    @model_validator(mode="after")
    def _check_enums(self) -> GroundingSummary:
        if self.reranker not in ("enabled", "disabled"):
            raise ValueError("grounding_summary.reranker must be 'enabled' or 'disabled'")
        if self.calibration_status not in ("calibrated", "uncalibrated"):
            raise ValueError(
                "grounding_summary.calibration_status must be 'calibrated' or 'uncalibrated'"
            )
        return self


class GroundedResult(BaseModel):
    """Matches `components.schemas.GroundedResultBase` (ADR-0018 §5).

    Backward-compatible extension of `ToolResultBase`: keeps `citations`/`meta`, swaps `items` for
    `claims[]`, adds `grounding_summary`. Invariant 4 (``status=insufficient_evidence`` ⇒ no FACT
    claim) is enforced at construction; invariants 1-3/5 live on `Claim`; invariant 6 (`evidence`
    resolves via `kb_get_document`) is a runtime check (GT-2), not encodable here.
    """

    model_config = ConfigDict(extra="forbid")

    status: GroundedStatus
    claims: list[Claim] = Field(default_factory=list)
    grounding_summary: GroundingSummary
    citations: list[Citation] = Field(default_factory=list)
    meta: Meta

    @model_validator(mode="after")
    def _check_status_invariants(self) -> GroundedResult:
        if self.status is GroundedStatus.INSUFFICIENT_EVIDENCE and any(
            c.grounding is GroundingVerdict.FACT for c in self.claims
        ):
            raise ValueError(
                "status=insufficient_evidence must have no FACT claim (ADR-0018 §5 invariant 4)"
            )
        return self
