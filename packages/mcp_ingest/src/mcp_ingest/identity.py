"""Stable `source_id` and `visibility` rules per connector (spike S5, T-055).

Pure functions over already-fetched metadata; the connectors (T-070..T-072) call them so the
rules live in exactly one tested place. See docs/spikes/S5-visibility-source-id.md for the
rationale and the "permissions changed after crawl" case.

Policy (ADR-0016 Part 2, user decision 2026-10-01): the corpus is TEAM-ONLY. A document is
labelled `team` only when it can be *proved* visible to the whole team; anything unknown,
unverifiable or restricted is labelled `restricted` and rejected later by the ingest stage
(T-073 -> `kb.ingest_failures{stage: redact, code: blocked_by_policy}`). Default-deny.
"""

from __future__ import annotations

import re
from collections.abc import Collection
from typing import Literal

__all__ = [
    "Visibility",
    "confluence_source_id",
    "confluence_visibility",
    "gitlab_source_id",
    "gitlab_visibility",
    "jira_source_id",
    "jira_visibility",
    "opensearch_alias",
    "opensearch_source_id",
    "opensearch_visibility",
]

Visibility = Literal["team", "restricted"]
GitLabKind = Literal["blob", "mr", "issue"]

# -- source_id ---------------------------------------------------------------------------------


def confluence_source_id(page_id: str | int) -> str:
    """The numeric page id: unchanged by rename, move between spaces, or version bumps."""
    text = str(page_id).strip()
    if not text.isdigit():
        raise ValueError(f"Confluence page id must be numeric, got {text!r}")
    return text


def gitlab_source_id(project_id: int, kind: GitLabKind, ref: str | int) -> str:
    """`<numeric project id>:<kind>:<ref>`; the project *path* is deliberately not used because
    a rename/transfer would fork every document of the project. Files are always read from the
    project's default branch, so the branch is not part of the id."""
    if kind not in ("blob", "mr", "issue"):
        raise ValueError(f"unknown GitLab document kind {kind!r}")
    reference = str(ref)
    if not reference:
        raise ValueError("GitLab source reference must not be empty")
    return f"{int(project_id)}:{kind}:{reference}"


_DATA_STREAM_PREFIX = re.compile(r"^\.ds-")
_ROLLOVER_SUFFIX = re.compile(r"-\d{6}$")  # ILM rollover generation: -000007
_DATE_SUFFIX = re.compile(r"-\d{4}[.\-]\d{2}(?:[.\-]\d{2})?$")  # -2026.09.30 / -2026-09 / ...


def opensearch_alias(index: str) -> str:
    """Strip data-stream, date and ILM-rollover suffixes: `logs-app-000007` -> `logs-app`.

    Only those shapes are stripped; `postmortems-v2` or `runbooks-2026-q3` are real names.
    """
    name = _DATA_STREAM_PREFIX.sub("", index)
    name = _ROLLOVER_SUFFIX.sub("", name)
    name = _DATE_SUFFIX.sub("", name)
    name = _ROLLOVER_SUFFIX.sub("", name)  # `.ds-x-2026.09.30-000012` strips in two steps
    return name or index


def opensearch_source_id(index: str, doc_id: str) -> str:
    """`<alias>:<doc id>`: stable across ILM rollover as long as the producer re-uses the same
    `_id` (or the connector is configured with an id field). Auto-generated `_id`s are not stable
    across a reindex; such indices are not suitable for ingest (S5, ADR-0012 A5)."""
    if not doc_id:
        raise ValueError("OpenSearch document id must not be empty")
    return f"{opensearch_alias(index)}:{doc_id}"


# -- visibility --------------------------------------------------------------------------------


def confluence_visibility(
    *,
    space_key: str,
    space_type: str | None,
    team_spaces: Collection[str],
    page_read_restricted: bool | None,
    ancestors_read_restricted: bool | None,
) -> Visibility:
    """`team` iff the space is declared team-wide by the operator
    (`MCP_INGEST_CONFLUENCE_TEAM_SPACES`), is a global (not personal) space, and neither the
    page nor any ancestor has a read restriction. `None` (could not be determined) never passes.
    """
    declared = {key.lower() for key in team_spaces}
    if space_key.lower() not in declared:
        return "restricted"
    if space_type != "global":
        return "restricted"
    if page_read_restricted is not False or ancestors_read_restricted is not False:
        return "restricted"
    return "team"


_READER_ACCESS_LEVEL = 20  # GitLab Reporter: the lowest role that can read private repo code


def _declared_team_project(path: str, team_projects: Collection[str]) -> bool:
    lowered = path.lower().strip("/")
    for declared in team_projects:
        prefix = declared.lower().strip("/")
        if lowered == prefix or lowered.startswith(prefix + "/"):
            return True
    return False


def gitlab_visibility(
    *,
    kind: GitLabKind,
    project_visibility: str | None,
    feature_access: str | None,
    confidential: bool,
    project_path: str,
    team_projects: Collection[str],
    crawler_access_level: int | None,
    internal_is_team: bool = True,
) -> Visibility:
    """`team` iff everyone on the team can read this item in GitLab, as far as the API shows.

    * project visibility `public` -> team; `internal` -> team unless `internal_is_team=False`
      (instances with external users); `private` -> team only when the project path is under a
      path the operator declared (`MCP_INGEST_GITLAB_TEAM_PROJECTS`) *and* the crawler holds at
      least Reporter (otherwise it could not read the code, and the label would be a guess);
    * the feature (`repository_access_level` / `issues_access_level` /
      `merge_requests_access_level`) must be `enabled` (everyone who sees the project), not
      `private` (members only) nor `disabled`;
    * confidential issues/MRs are never team content.
    """
    if kind in ("issue", "mr") and confidential:
        return "restricted"
    if feature_access != "enabled":
        return "restricted"
    if project_visibility == "public":
        return "team"
    if project_visibility == "internal":
        return "team" if internal_is_team else "restricted"
    if project_visibility == "private":
        declared = _declared_team_project(project_path, team_projects)
        readable = crawler_access_level is not None and crawler_access_level >= _READER_ACCESS_LEVEL
        return "team" if declared and readable else "restricted"
    return "restricted"


def opensearch_visibility(index: str, *, allowlist: Collection[str]) -> Visibility:
    """`team` only for an index whose alias the operator allowlisted
    (`MCP_INGEST_OPENSEARCH_INDICES`, default empty = connector off). The allowlist is an
    attestation that the index holds team-wide content; OpenSearch has no per-document ACL we
    could read back."""
    declared = {item.lower() for item in allowlist}
    return "team" if opensearch_alias(index).lower() in declared else "restricted"


def jira_source_id(issue_key: str) -> str:
    """The issue **key** (e.g. `PAY-1234`): stable across edits. A project *move* that renames
    the key forks the document — the old key disappears at the source and is tombstoned by the
    next full reconcile (ADR-0019: Jira has no reliable delete feed)."""
    key = str(issue_key).strip().upper()
    if not re.match(r"^[A-Z][A-Z0-9]+-[0-9]+$", key):
        raise ValueError(f"Jira issue key must match ^[A-Z][A-Z0-9]+-[0-9]+$, got {issue_key!r}")
    return key


def jira_visibility(
    *,
    project_key: str,
    team_projects: Collection[str],
    issue_security_level: str | None,
    project_restricted: bool | None,
) -> Visibility:
    """`team` iff the issue carries NO security level, its project is NOT access-restricted, and
    the operator declared the project team-wide (`MCP_INGEST_JIRA_TEAM_PROJECTS`). Default-deny
    (ADR-0016 A2 / ADR-0019): a security level, a restricted project, or an unknown
    (`None`, unverifiable) restriction state never passes."""
    declared = {key.lower() for key in team_projects}
    if project_key.lower() not in declared:
        return "restricted"
    if issue_security_level:
        return "restricted"
    if project_restricted is not False:
        return "restricted"
    return "team"
