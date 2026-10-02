"""Report models of the CLI, mirroring `components.schemas` of api-contract.yaml
(`MigrationResult`, `IngestError`, `IngestSourceResult`, `IngestRunReport`, `IngestStatusRow`,
`IngestSourceConfigRow`, and the inline `prune` response) and the exit-code rules.

Exit codes (contract `ingest_run`): 0 success, 1 partial, 2 failed, 3 another process holds the
per-source advisory lock.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "EXIT_FAILED",
    "EXIT_LOCKED",
    "EXIT_OK",
    "EXIT_PARTIAL",
    "IngestError",
    "IngestRunReport",
    "IngestSourceConfigRow",
    "IngestSourceResult",
    "IngestStatusReport",
    "IngestStatusRow",
    "MigrationResult",
    "MigrationStatusReport",
    "PruneReport",
    "PruneSourceRow",
    "SourcesReport",
    "overall",
]

EXIT_OK = 0
EXIT_PARTIAL = 1
EXIT_FAILED = 2
EXIT_LOCKED = 3

Stage = Literal[
    "config", "connect", "crawl", "normalize", "redact", "chunk", "embed", "persist",
    "reconcile", "prune",
]  # fmt: skip
SourceName = Literal["confluence", "gitlab", "opensearch", "jira"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MigrationResult(_Model):
    """`components.schemas.MigrationResult`."""

    applied: list[str]
    already_applied: list[str] = Field(default_factory=list)
    current_version: str | None
    dry_run: bool = False


class MigrationStatusReport(_Model):
    """Read-only view of migration state for `mcp-ingest db status` (T-090)."""

    current_version: str | None
    applied: list[str] = Field(default_factory=list)
    pending: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class IngestError(_Model):
    stage: Stage
    source_id: str | None = None
    # contract ErrorCode (invalid_input, not_permitted, upstream_*, source_misconfigured, ...)
    code: str
    message: str = Field(max_length=1024)
    retryable: bool


class IngestSourceResult(_Model):
    source_type: str
    run_id: str | None = None
    status: Literal["success", "partial", "failed", "skipped"]
    documents_seen: int = 0
    documents_upserted: int = 0
    documents_skipped: int = 0
    documents_failed: int = 0
    documents_tombstoned: int | None = None
    chunks_written: int = 0
    duration_s: float | None = None
    cursor_advanced: bool = False
    errors: list[IngestError] = Field(default_factory=list)


class IngestRunReport(_Model):
    mode: Literal["incremental", "full", "reembed"]
    started_at: datetime
    finished_at: datetime | None = None
    status: Literal["success", "partial", "failed"]
    exit_code: Literal[0, 1, 2, 3]
    embedding_model: str | None = None
    dry_run: bool = False
    sources: list[IngestSourceResult]


class IngestStatusRow(_Model):
    source_type: str
    last_success_at: datetime | None = None
    staleness_hours: float | None = Field(default=None, ge=0)
    document_count: int = Field(ge=0)
    chunk_count: int = Field(ge=0)
    last_run_status: Literal["running", "success", "partial", "failed"] | None = None
    last_run_at: datetime | None = None


class IngestStatusReport(_Model):
    as_of: datetime
    sources: list[IngestStatusRow]


class IngestSourceConfigRow(_Model):
    source_type: str
    connector: str
    enabled: bool
    configured: bool
    missing_env: list[str]


class SourcesReport(_Model):
    connectors: list[IngestSourceConfigRow]


class PruneSourceRow(_Model):
    source_type: str
    documents_deleted: int = Field(ge=0)
    chunks_deleted: int = Field(ge=0)


class PruneReport(_Model):
    documents_deleted: int = Field(ge=0)
    chunks_deleted: int = Field(ge=0)
    dry_run: bool
    reindex_recommended: bool = False
    per_source: list[PruneSourceRow] = Field(default_factory=list)


def overall(results: list[IngestSourceResult], *, locked: set[str]) -> tuple[str, int]:
    """Roll per-source results up into the run `status` and exit code.

    * a source skipped because another process holds its lock, because it is disabled, or because
      it is not configured is *neutral* when something else ran: a team that only uses Confluence
      must not get exit 1 every hour because the GitLab connector has no configuration. The
      configuration problem is still reported in that source's `errors`;
    * when nothing could run: every source locked -> exit 3; any misconfigured -> failed (2);
      otherwise (all disabled) -> success, there was nothing to do;
    * all success -> 0; everything that ran failed -> 2; any mixture -> 1 (partial).
    """
    ran = [result.status for result in results if result.status != "skipped"]
    if not ran:
        if locked and len(locked) == len(results):
            return "failed", EXIT_LOCKED
        if any(error.stage == "config" for result in results for error in result.errors):
            return "failed", EXIT_FAILED
        return "success", EXIT_OK
    if all(status == "success" for status in ran):
        return "success", EXIT_OK
    if all(status == "failed" for status in ran):
        return "failed", EXIT_FAILED
    return "partial", EXIT_PARTIAL
