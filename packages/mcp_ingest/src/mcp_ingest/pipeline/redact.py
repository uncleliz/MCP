"""Stage `redact` = the policy gate (T-073, ADR-0012 A1, ADR-0015 A1, ADR-0016 A1-A2).

Runs on the ingest side, **before** chunking/embedding/INSERT: the pipeline writes straight into
`kb.chunks`, so a secret that is only scrubbed by the tool layer would be persisted and replayed
by `kb_semantic_search`, bypassing the very deny-glob `gitlab_get_file` enforces.

A document is rejected (-> `ingest_failures{stage: redact, code: blocked_by_policy}`) when
  * its visibility is not `team` (corpus is TEAM-ONLY, ADR-0016 A1) — default-deny;
  * the connector refused to fetch it (`blocked_reason`, e.g. deny-glob on the path);
  * its path matches the deny-glob (`MCP_GITLAB_PATH_DENY`), checked again here as defence in
    depth — the connector could be bypassed by a future code path.
Everything else is normalised and scrubbed with `mcp_common.redact.scrub`.
"""

from __future__ import annotations

import fnmatch
import hashlib
import os
from dataclasses import dataclass

from mcp_common.redact import scrub
from mcp_gitlab.settings import DEFAULT_PATH_DENY

from mcp_ingest.connectors.base import SourceDocument
from mcp_ingest.pipeline.normalize import normalize_document

__all__ = [
    "BLOCKED_BY_POLICY",
    "Blocked",
    "Prepared",
    "apply_policy",
    "deny_globs_from_env",
    "path_denied",
]

BLOCKED_BY_POLICY = "blocked_by_policy"


@dataclass(frozen=True)
class Blocked:
    reason: str
    code: str = BLOCKED_BY_POLICY


@dataclass(frozen=True)
class Prepared:
    text: str  # normalised + redacted, NOT wrapped (ADR-0015 A2)
    title: str | None
    content_hash: str
    redactions: int


def deny_globs_from_env() -> list[str]:
    raw = os.environ.get("MCP_GITLAB_PATH_DENY", DEFAULT_PATH_DENY)
    return [part.strip().lower() for part in raw.split(",") if part.strip()]


def path_denied(path: str | None, globs: list[str]) -> bool:
    if not path:
        return False
    lowered = path.lower().lstrip("/")
    base = lowered.rsplit("/", 1)[-1]
    return any(fnmatch.fnmatchcase(lowered, g) or fnmatch.fnmatchcase(base, g) for g in globs)


def apply_policy(document: SourceDocument, deny_globs: list[str]) -> Blocked | Prepared:
    if document.visibility != "team":
        return Blocked(f"visibility is '{document.visibility}', corpus is team-only")
    if document.blocked_reason:
        return Blocked(document.blocked_reason)
    if path_denied(document.path, deny_globs):
        return Blocked("path matches the deny-glob (MCP_GITLAB_PATH_DENY)")
    text, redactions = scrub(normalize_document(document))
    title = None
    if document.title:
        title, title_redactions = scrub(document.title)
        redactions += title_redactions
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return Prepared(text=text, title=title, content_hash=content_hash, redactions=redactions)
