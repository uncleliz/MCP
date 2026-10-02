"""``enforce_permission`` — the single default-deny permission filter (choke point #1).

ADR-0016 (document visibility, TEAM-ONLY corpus at v1) + ADR-0021 (gateway boundary) place a server
-side permission filter **before** context assembly. The invariant this module guarantees, and the
adversarial test (FR-019/AC-002, TC-090) proves, is:

    a candidate whose backing document the caller has **no grant** on is dropped *before* the
    context-pack is assembled — it never becomes a candidate, a claim, or a citation.

Design (why it is default-deny, not allow-by-default):

* The permitted set is computed by *intersection*: a document is kept only when there exists a grant
  row ``(document_id, principal, 'read')`` whose ``principal`` is one the caller holds. Absence of
  any such row — a missing permission record, an unknown document, a ``restricted`` document the
  caller was never granted — resolves to **deny**. There is no "allow when unsure" branch.
* The caller's principals come from :class:`CallerContext`. In the TEAM-ONLY corpus (ADR-0016 A1) a
  team member holds the pseudo-principal :data:`TEAM_PRINCIPAL` (``'*team*'``), which ingest writes
  as the grant for team-visible documents. A caller who is *not* a team member (empty principal set,
  or an explicit non-team identity) does not hold ``'*team*'`` and therefore cannot read a
  team-only document unless granted explicitly — the same default-deny rule, no special case.

Single choke point (L-001): :func:`enforce_permission` is the *only* function that decides
visibility, over the one grant set :func:`load_grants_for_document_ids` resolves. All 8 knowledge
-tier content tools reach a document only after it has survived this one decision:

* the two grounded tools (``search_company_knowledge`` / ``get_jira_context``) run it on the
  retrieval candidates, wired through exactly one
  :class:`~mcp_knowledge.pack.assembler.PermissionFilter` hook on the one
  :class:`~mcp_knowledge.pack.assembler.ContextPackAssembler`, strictly before the grounding gate
  (#2);
* the six non-grounded content tools (``search_code``, ``get_service``, ``get_repository``,
  ``find_related_knowledge``, ``get_knowledge_summary``, ``get_document_version``) resolve the
  document id(s) backing their rows and drop anything the caller holds no grant on via the **same**
  :func:`enforce_permission` decision before any content / ``source_uri`` / provenance is returned.

The grounding gate (#2) runs only on the grounded path and always after permission; the two are
separate and ordered. A structural test (FR-019/AC-003, TC-091) asserts there is no second
filtering site and that every content tool passes through this one decision.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from mcp_knowledge.pack.assembler import PermissionFilter
from mcp_knowledge.retrieval.hybrid import Candidate

__all__ = [
    "TEAM_PRINCIPAL",
    "CallerContext",
    "PermissionGrants",
    "build_permission_filter",
    "enforce_permission",
    "load_grants",
    "load_grants_for_document_ids",
]

#: Pseudo-principal that a team member holds and that ingest writes as the grant for team-visible
#: documents in the TEAM-ONLY corpus (ADR-0016 A1). A caller who is not a team member does not hold
#: it, so team-only documents stay denied for them (default-deny).
TEAM_PRINCIPAL = "*team*"


@dataclass(frozen=True)
class CallerContext:
    """The identity the permission filter decides against.

    At v1 (stdio, ADR-0021) the process owner is the single caller; ``is_team_member`` defaults to
    True so the inherited-source-permission model (each user runs with their own credential, ADR
    -0016 Context) keeps the team corpus readable. ``principals`` may carry explicit user/group ids
    for forward compatibility with per-request identity (v1.1); it never *widens* access on its own
    — a principal only grants a document if a matching grant row exists.
    """

    principals: frozenset[str] = frozenset()
    is_team_member: bool = True

    def effective_principals(self) -> frozenset[str]:
        """Every principal the caller holds. A team member also holds :data:`TEAM_PRINCIPAL`.

        This is the *only* set a document may be matched against; nothing else can grant access.
        """
        if self.is_team_member:
            return self.principals | {TEAM_PRINCIPAL}
        return self.principals


@dataclass(frozen=True)
class PermissionGrants:
    """The resolved permitted-document set for one caller, over one candidate universe.

    ``permitted_document_ids`` is the set of document ids the caller may read, computed by the
    default-deny intersection in :func:`load_grants`. A document id absent from this set is denied —
    there is no stored "deny" row; absence *is* the deny.
    """

    permitted_document_ids: frozenset[str] = field(default_factory=frozenset)

    def allows(self, document_id: str) -> bool:
        return document_id in self.permitted_document_ids


class ReadTxProtocol(Protocol):
    """Minimal read surface :func:`load_grants` needs: a named, parameterised statement runner."""

    async def fetch(
        self, name: str, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]: ...


class RetrievalClientProtocol(Protocol):
    def read_tx(self) -> Any:  # async context manager yielding ReadTxProtocol
        ...


def _document_ids(candidates: Iterable[Candidate]) -> list[str]:
    # Preserve the first-seen order and de-duplicate; empty input must not hit the database.
    seen: dict[str, None] = {}
    for candidate in candidates:
        seen.setdefault(str(candidate.document_id), None)
    return list(seen)


async def load_grants(
    client: RetrievalClientProtocol,
    candidates: Sequence[Candidate],
    caller: CallerContext,
) -> PermissionGrants:
    """Resolve which candidate documents the caller may read (default-deny intersection).

    One read-only query (``document_grants``) over the candidate document ids and the caller's
    effective principals: a document is permitted iff the query returns a grant row for it. A
    document with no returned row — unknown, tombstoned, ``restricted`` without an explicit grant,
    or simply never granted — is denied by omission. An empty candidate set or an empty principal
    set short-circuits to "deny everything" without touching the database.
    """
    return await load_grants_for_document_ids(client, _document_ids(candidates), caller)


async def load_grants_for_document_ids(
    client: RetrievalClientProtocol,
    document_ids: Iterable[str],
    caller: CallerContext,
) -> PermissionGrants:
    """Resolve which of ``document_ids`` the caller may read (the one default-deny intersection).

    This is the single grant-resolution primitive: :func:`load_grants` (the candidate/grounded
    path) and the six non-grounded content tools (entity / summary / version / related-knowledge /
    code) all funnel through here, so every tool uses *one* ``document_grants`` query with *one*
    default-deny rule. A document id with no returned grant row — unknown, tombstoned,
    ``restricted`` without an explicit grant, or simply never granted — is denied by omission. An
    empty id set or an empty principal set short-circuits to "deny everything" without touching the
    database.
    """
    seen: dict[str, None] = {}
    for document_id in document_ids:
        if document_id:
            seen.setdefault(str(document_id), None)
    ids = list(seen)
    principals = sorted(caller.effective_principals())
    if not ids or not principals:
        return PermissionGrants(frozenset())
    async with client.read_tx() as tx:
        rows = await tx.fetch(
            "document_grants",
            {"document_ids": ids, "principals": principals},
        )
    permitted = {str(row["document_id"]) for row in rows}
    return PermissionGrants(frozenset(permitted))


def enforce_permission(
    candidates: Sequence[Candidate],
    grants: PermissionGrants,
) -> list[Candidate]:
    """Drop every candidate the caller has no grant on (default-deny). The one visibility decision.

    This is the single site that decides permission. It keeps a candidate only when
    ``grants.allows(document_id)`` is true; everything else is excluded *before* assembly, so a
    restricted document can never reach the context-pack or a citation (FR-019/AC-002).
    """
    return [c for c in candidates if grants.allows(str(c.document_id))]


def build_permission_filter(grants: PermissionGrants) -> PermissionFilter:
    """Bind resolved ``grants`` into the one :class:`PermissionFilter` hook the assembler runs.

    Returning a closure (not a second filtering implementation) keeps enforcement at a single choke
    point: the assembler invokes this exactly once, before the grounding gate. E6 only swaps the
    E4 identity seam for this closure; it never adds a parallel filtering site (L-001).
    """

    def _filter(candidates: Sequence[Candidate]) -> list[Candidate]:
        return enforce_permission(candidates, grants)

    return _filter
