"""The read side of the 8 knowledge tools (T-100/T-101/T-102, FR-016/FR-018/FR-020).

Each public method validates, runs the named read-only statement(s) via :class:`KnowledgeClient`
(domain SQL) or the hybrid pipeline (:class:`HybridRetriever` → rerank → compress →
:class:`ContextPackAssembler`), and returns a :class:`ToolOutcome` (entity/version/related/code)
or a
:class:`GroundedOutcome` (search_company_knowledge / get_jira_context).

Choke points (L-001): the single permission filter (#1, E6 seam) runs **before** the single
context-pack assembler / grounding gate (#2, E8 seam). This module wires both as hooks on ONE
:class:`ContextPackAssembler` so neither is duplicated; E6/E8 swap the implementations in place.
"""

from __future__ import annotations

import re
import time
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from mcp_common.envelope import Citation, DataFreshness, SourceType
from mcp_common.errors import ErrorCode, ToolError
from mcp_common.tooling import ToolOutcome, build_result, invalid_input, not_found_result

from mcp_knowledge.domain_sql import FANOUT_LIMIT, MAX_HOPS
from mcp_knowledge.grounding.verdict import GroundingVerdictGate
from mcp_knowledge.liveness.authority import SourceAuthorityConfig, load_source_authority
from mcp_knowledge.liveness.freshness import FreshnessAssessment
from mcp_knowledge.liveness.reconcile import (
    LiveValue,
    ReconcileOutcome,
    SnapshotValue,
    reconcile_claim,
)
from mcp_knowledge.pack.assembler import ContextPackAssembler, PermissionFilter
from mcp_knowledge.pack.compress import compress_candidates
from mcp_knowledge.permission.enforce import (
    CallerContext,
    build_permission_filter,
    load_grants,
    load_grants_for_document_ids,
)
from mcp_knowledge.rerank.local import Reranker
from mcp_knowledge.retrieval.hybrid import Candidate, HybridQuery, HybridRetriever
from mcp_knowledge.tools.grounded import (
    GroundedOutcome,
    build_grounded_result,
    build_reconciled_result,
)

SOURCE = "knowledge"

_REL_TYPES = ("depends_on", "documented_by", "owns", "related_to")
_SUBJECT_TYPES = ("entity", "topic")

__all__ = ["KnowledgeReadApi"]


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
        return True
    except ValueError:
        return False


def _doc_probe(document_id: str) -> Candidate:
    """A minimal :class:`Candidate` standing in for a document, so a pinned test
    :class:`PermissionFilter` can decide the non-grounded content tools exactly as it decides the
    grounded candidates. Only ``document_id`` is meaningful here; the filter keys on it.
    """
    return Candidate(
        chunk_id=0,
        document_id=document_id,
        chunk_index=0,
        content="",
        heading_path=None,
        source_type="pgvector",
        source_id=document_id,
        source_uri="",
        title=None,
        container=None,
        author=None,
        source_updated_at=None,
        ingested_at=None,
        rrf_score=0.0,
        legs={},
    )


class JiraContextProvider:
    """Minimal read-only protocol the knowledge server needs from a live Jira client (ADR-0019).

    Satisfied structurally by :class:`mcp_jira.client.JiraClient` (no import dependency between the
    packages). ``search_issues`` is a GET-only JQL search — there is no create/transition/comment
    path. E5 (T-105) uses the returned issues to reconcile the live current-work-status against the
    knowledge snapshot (:mod:`mcp_knowledge.liveness.reconcile`): a missing/failing Jira degrades to
    a snapshot-only answer marked "live verification unavailable" (spec §35), never an error.
    """

    async def search_issues(self, jql: str, *, max_results: int) -> Any: ...


class KnowledgeReadApi:
    """All 8 tools. The hybrid pipeline parts (retriever/reranker/assembler) are injected so tests
    can drive them with fakes; production wires the real pgvector-backed retriever."""

    def __init__(
        self,
        client: Any,
        retriever: HybridRetriever,
        *,
        reranker: Reranker | None = None,
        permission_filter: PermissionFilter | None = None,
        caller: CallerContext | None = None,
        jira: JiraContextProvider | None = None,
    ) -> None:
        self._client = client
        self._retriever = retriever
        self._reranker = reranker or Reranker(enabled=False)
        # One assembler, both choke-point hooks (L-001). The permission filter (#1) runs inside the
        # assembler, strictly before the grounding gate (#2). E6 makes #1 a real default-deny filter
        # built per call from grants resolved against `kb.document_permissions` (see
        # `_permission_filter`). A test may inject a fixed `permission_filter` to pin the hook; when
        # it does, that is honoured verbatim (no DB lookup). E8 later swaps the gate in place.
        self._assembler = ContextPackAssembler(permission_filter=permission_filter)
        self._fixed_permission_filter = permission_filter
        self._caller = caller or CallerContext()
        self._jira = jira

    # == search_company_knowledge (FR-016) ======================================================

    async def search_company_knowledge(
        self,
        *,
        query: str,
        top_k: int = 8,
        source_types: Sequence[str] | None = None,
        fact_type: str | None = None,
        include_live: bool = True,
    ) -> GroundedOutcome:
        started = time.monotonic()
        if not 3 <= len(query) <= 4096:
            raise invalid_input("query", "độ dài phải từ 3 đến 4096 ký tự", SOURCE)
        candidates = await self._retriever.retrieve(
            HybridQuery(text=query, top_k=top_k, source_types=tuple(source_types or ()))
        )
        pack, reranker_status = await self._run_pipeline(query, candidates)
        result = build_grounded_result(
            pack,
            started=started,
            query_echo={"query": query, "top_k": top_k},
            reranker_status=reranker_status,
            warnings=["no official source found"] if not pack.claims else [],
        )
        return GroundedOutcome(result, query_description=f'"{query}"')

    # == get_jira_context (FR-018) ===============================================================

    async def get_jira_context(self, *, subject: str, top_k: int = 10) -> GroundedOutcome:
        """Reconcile the knowledge snapshot about *subject* against live Jira (ADR-0018 §4, FR-018).

        Current work status is authoritative in Jira (migration 0008 ``current_work_status→jira``),
        so this tool runs the live read and reconciles it against the snapshot:

        * snapshot and live **agree** ⇒ one FACT claim with provenance from both sides (AC-001);
        * they **differ** ⇒ a CONFLICT claim exposing both positions + ``authority_note`` (AC-002);
        * live **unavailable** ⇒ snapshot only, marked "live verification unavailable" (spec §35),
          with the snapshot's freshness down-weighting its confidence (AC-003).

        The snapshot pipeline (permission #1 → retrieve → rerank → compress → assembler #2) is
        unchanged; this only adds the live comparison on top of its candidates.
        """
        started = time.monotonic()
        if not 1 <= len(subject) <= 256:
            raise invalid_input("subject", "độ dài phải từ 1 đến 256 ký tự", SOURCE)
        echo = {"subject": subject, "top_k": top_k}

        config = await self._load_authority_config()
        live_value, live_warning = await self._fetch_live_jira_value(subject, top_k)

        candidates = await self._retriever.retrieve(HybridQuery(text=subject, top_k=top_k))

        # Resolve the ONE permission filter for this candidate set once (one `document_grants`
        # read), then use the same resolved filter for BOTH the grounded pack and the reconcile
        # snapshot. The reconcile path reads the snapshot value directly off a candidate (not off
        # the permission-filtered pack), so without this it would be a second content path around
        # the choke point — exactly the R-028/E-007 bypass. A denied candidate becomes neither the
        # pack nor the snapshot the reconciler exposes (FR-019/AC-002, L-001: one decision).
        permission_filter = await self._permission_filter(candidates)
        candidates = list(permission_filter(candidates))
        pack, reranker_status = await self._run_pipeline(
            subject, candidates, permission_filter=permission_filter
        )

        snapshot = self._snapshot_value_for(subject, candidates, live_value)
        if snapshot is None:
            # No snapshot evidence at all: fall back to the honest UNKNOWN path (nothing to
            # reconcile).
            warnings = [live_warning] if live_warning else ["no official source found"]
            result = build_grounded_result(
                pack,
                started=started,
                query_echo=echo,
                reranker_status=reranker_status,
                warnings=warnings,
            )
            return GroundedOutcome(result, query_description=f'bối cảnh của "{subject}"')

        outcome: ReconcileOutcome = reconcile_claim(
            text=f'current work status of "{subject}"',
            fact_type="current_work_status",
            snapshot=snapshot,
            live=live_value,
            config=config,
            live_error=live_warning,
        )
        warnings = list(outcome.warnings)
        freshness = self._freshness_meta(outcome.snapshot_freshness)
        result = build_reconciled_result(
            [outcome.claim],
            started=started,
            query_echo=echo,
            reranker_status=reranker_status,
            data_freshness=freshness,
            warnings=warnings,
        )
        return GroundedOutcome(result, query_description=f'bối cảnh của "{subject}"')

    async def _load_authority_config(self) -> SourceAuthorityConfig:
        """Load the source-authority + freshness config from the DB (defaults if unavailable)."""
        try:
            async with self._client.read_tx() as tx:
                return await load_source_authority(tx)
        except Exception:  # noqa: BLE001 — config read must never break the Live path (spec §35)
            return await load_source_authority(_EmptyTx())

    async def _fetch_live_jira_value(
        self, subject: str, top_k: int
    ) -> tuple[LiveValue | None, str | None]:
        """Run the read-only live Jira search and extract one comparable current-status value.

        Returns ``(live_value, warning)``. ``warning`` is set (and ``live_value`` is ``None``) when
        Jira is not configured or the read fails — the caller then serves the snapshot alone and
        marks it "live verification unavailable" (spec §35). Never raises for a live failure.
        """
        if self._jira is None:
            return None, "live Jira not configured; using knowledge snapshot only"
        try:
            page = await self._jira.search_issues(
                f'text ~ "{subject}" ORDER BY updated DESC', max_results=top_k
            )
        except ToolError as exc:
            return None, f"live Jira unavailable: {exc.code.value}"
        issues = _issues_of(page)
        if not issues:
            return None, "no live Jira issue matched; using knowledge snapshot only"
        return _live_value_from_issue(issues[0]), None

    def _snapshot_value_for(
        self, subject: str, candidates: Sequence[Candidate], live: LiveValue | None
    ) -> SnapshotValue | None:
        """Pick the snapshot value to reconcile against the live value.

        Prefer a candidate that references the same live issue key (so the two sides describe the
        same thing); otherwise the top candidate stands in for "what the snapshot says about the
        subject". Returns ``None`` when there is no candidate at all (nothing to reconcile).
        """
        chosen = _candidate_matching_key(candidates, live.external_id if live else None)
        if chosen is None:
            chosen = candidates[0] if candidates else None
        if chosen is None:
            return None
        value = _snapshot_status_text(chosen)
        return SnapshotValue(
            value=value,
            source=_source_type_of(chosen.source_type),
            document_id=str(chosen.document_id),
            chunk_id=f"{chosen.document_id}#{chosen.chunk_index}",
            updated_time=chosen.source_updated_at,
            source_uri=chosen.source_uri,
            synced_at=chosen.ingested_at,
        )

    @staticmethod
    def _freshness_meta(assessment: FreshnessAssessment) -> DataFreshness:
        """Expose the snapshot's staleness in ``meta.data_freshness`` (FR-018/AC-003, spec §35)."""
        return DataFreshness(staleness_hours=assessment.staleness_hours)


    async def _run_pipeline(
        self,
        query: str,
        candidates: list[Candidate],
        *,
        permission_filter: PermissionFilter | None = None,
    ) -> tuple[Any, str]:
        """permission (#1) → rerank → compress → gate (#2). Returns (pack, reranker_status).

        The permission choke point (#1) is resolved here, once, and handed to the assembler as its
        single ``permission_filter`` hook so enforcement happens at exactly one site, strictly
        before the grounding gate (#2). Default-deny: candidates whose documents the caller has no
        grant on are dropped before compression, so a restricted document never reaches the pack,
        the claims, or a citation (FR-019/AC-002). A test may pin a fixed filter via the
        constructor; otherwise grants are read from ``kb.document_permissions``.

        ``permission_filter`` may be supplied by a caller that already resolved the one filter for
        this candidate set (``get_jira_context`` does, so the reconcile snapshot and the pack
        share a *single* grant resolution — one choke point, one DB read).

        The grounding gate (#2) is the single :class:`GroundingVerdictGate` hook on the same
        assembler: it assigns each surviving claim exactly one deterministic verdict AFTER
        permission has run (L-001). It never re-filters visibility (that is choke point #1's job).
        """
        if permission_filter is None:
            permission_filter = await self._permission_filter(candidates)
        config = await self._load_authority_config()
        assembler = self._assembler_for(permission_filter, config)
        rerank = self._reranker.rerank(query, [c.content for c in candidates])
        ordered = [candidates[i] for i in rerank.order]
        chunks = compress_candidates(ordered)
        pack = assembler.assemble(ordered, chunks)
        return pack, rerank.status

    async def _permission_filter(self, candidates: list[Candidate]) -> PermissionFilter:
        """The one permission choke point (#1), default-deny (ADR-0016/0021).

        If the constructor pinned a filter, honour it verbatim (tests and the E4 identity seam). The
        default path resolves the caller's grants against ``kb.document_permissions`` and returns a
        default-deny filter bound to those grants — the single site that decides visibility.
        """
        if self._fixed_permission_filter is not None:
            return self._fixed_permission_filter
        grants = await load_grants(self._client, candidates, self._caller)
        return build_permission_filter(grants)

    def _assembler_for(
        self, permission_filter: PermissionFilter, config: SourceAuthorityConfig
    ) -> ContextPackAssembler:
        """One assembler per call: permission hook (#1) before the grounding gate (#2).

        Rebuilt per call because the permission decision depends on the caller + candidate set. The
        grounding gate (#2) is the single :class:`GroundingVerdictGate` — it grades claims
        deterministically using the DB-loaded source-authority config (for a CONFLICT's
        ``authority_note``) and the default confidence weights. This is wiring of the *same* two
        single choke points, ordered #1 then #2, never a second filtering or grading site (L-001).
        """
        return ContextPackAssembler(
            permission_filter=permission_filter,
            grounding_gate=GroundingVerdictGate(config=config),
        )

    # == permission choke point #1 for the non-grounded content tools (R-C-001) ==================

    async def _permitted_document_ids(self, document_ids: Sequence[str]) -> frozenset[str]:
        """Resolve which of ``document_ids`` the caller may read, via the one permission decision.

        The six non-grounded content tools (entity / summary / version / related / code) do not
        build retrieval candidates, so they cannot reuse the assembler's :class:`PermissionFilter`
        hook directly. Instead they resolve the document id(s) backing their rows here and drop
        anything not permitted *before* returning any content / ``source_uri`` / provenance — the
        **same** default-deny ``enforce_permission`` decision the grounded path makes, so permission
        is enforced tier-wide at one site, not per-tool (L-001, FR-019/AC-002).

        A test may pin a fixed :class:`PermissionFilter` via the constructor; when it does, that
        hook is honoured verbatim here too (a candidate is synthesised per document id so the
        pinned closure decides), so a test seam never diverges from production enforcement.
        Otherwise grants are resolved against ``kb.document_permissions`` for the caller's
        effective principals.
        """
        ids = [str(d) for d in document_ids if d]
        if not ids:
            return frozenset()
        if self._fixed_permission_filter is not None:
            probes = [_doc_probe(doc) for doc in dict.fromkeys(ids)]
            kept = self._fixed_permission_filter(probes)
            return frozenset(str(c.document_id) for c in kept)
        grants = await load_grants_for_document_ids(self._client, ids, self._caller)
        return frozenset(d for d in ids if grants.allows(d))

    async def _permits_document(self, document_id: str) -> bool:
        """True iff the caller may read ``document_id`` (single-document convenience)."""
        return document_id in await self._permitted_document_ids([document_id])

    async def _document_gate_denies(self, document_id: str | None) -> bool:
        """True iff a backing document exists and the caller is NOT permitted to read it.

        A row with no backing document (``document_id IS NULL`` — e.g. an entity never linked to a
        source document) carries no document-level visibility to enforce, so it is not denied by
        this gate: there is nothing restricted to protect. A row WITH a backing document is denied
        unless the caller holds a grant on it (default-deny). This keeps the single decision
        (``enforce_permission``) authoritative without turning "no document" into a false deny.
        """
        doc = str(document_id or "")
        if not doc:
            return False
        return not await self._permits_document(doc)

    # == search_code (FR-016/AC-002) =============================================================

    async def search_code(self, *, query: str, top_k: int = 10) -> ToolOutcome:
        started = time.monotonic()
        if not 2 <= len(query) <= 2048:
            raise invalid_input("query", "độ dài phải từ 2 đến 2048 ký tự", SOURCE)
        candidates = await self._retriever.retrieve(
            HybridQuery(text=query, top_k=top_k, source_types=("gitlab",))
        )
        # Permission choke point #1 (R-C-001): drop every candidate whose document the caller holds
        # no grant on, before any chunk content / GitLab source_uri is returned. Same default-deny
        # decision as the grounded path — a restricted doc never becomes an item or a citation.
        permitted_docs = await self._permitted_document_ids(
            [str(c.document_id) for c in candidates]
        )
        candidates = [c for c in candidates if str(c.document_id) in permitted_docs]
        provider = getattr(self._retriever, "_provider", None)
        model_id: str = getattr(provider, "model_id", "unknown") or "unknown"
        items: list[dict[str, Any]] = []
        citations: list[Citation] = []
        for index, c in enumerate(candidates):
            items.append(_chunk_item(c, index, embedding_model=model_id))
            citations.append(_chunk_citation(c, index))
        result = build_result(
            SourceType.PGVECTOR, items, citations,
            started=started, query_echo={"query": query, "top_k": top_k},
        )  # fmt: skip
        return ToolOutcome(result, query_description=f'code cho "{query}"')

    # == get_service / get_repository (FR-020/AC-001) ============================================

    async def get_service(self, *, name: str) -> ToolOutcome:
        return await self._get_entity("service", name, max_len=256)

    async def get_repository(self, *, name: str) -> ToolOutcome:
        return await self._get_entity("repository", name, max_len=512)

    async def _get_entity(self, entity_type: str, name: str, *, max_len: int) -> ToolOutcome:
        started = time.monotonic()
        if not 1 <= len(name) <= max_len:
            raise invalid_input("name", f"độ dài phải từ 1 đến {max_len} ký tự", SOURCE)
        echo = {"name": name}
        async with self._client.read_tx() as tx:
            rows = await tx.fetch(
                "entity_by_type_name", {"entity_type": entity_type, "name": name}
            )
            if not rows:
                return ToolOutcome(
                    not_found_result(SourceType.PGVECTOR, started=started, query_echo=echo),
                    identifier=f"{entity_type} {name}",
                )
            entity = rows[0]
            neighbours = await tx.fetch(
                "entity_neighbours",
                {"entity_id": entity["id"], "rel_types": [], "limit": FANOUT_LIMIT},
            )
        # Permission choke point #1 (R-C-001): an entity whose backing document the caller has no
        # grant on is indistinguishable from a missing one — default-deny, no content/source_uri
        # leak and no existence oracle (FR-019/AC-002).
        if await self._document_gate_denies(entity.get("document_id")):
            return ToolOutcome(
                not_found_result(SourceType.PGVECTOR, started=started, query_echo=echo),
                identifier=f"{entity_type} {name}",
            )
        item = _entity_item(entity, neighbours, citation_ref=0)
        citation = _entity_citation(entity, entity_type, name)
        result = build_result(
            SourceType.PGVECTOR, [item], [citation], started=started, query_echo=echo
        )
        return ToolOutcome(result, query_description=f"{entity_type} {name}")

    # == find_related_knowledge (FR-020/AC-003) ==================================================

    async def find_related_knowledge(
        self, *, entity: str, max_depth: int = 2, rel_types: Sequence[str] | None = None
    ) -> ToolOutcome:
        started = time.monotonic()
        if not 1 <= len(entity) <= 256:
            raise invalid_input("entity", "độ dài phải từ 1 đến 256 ký tự", SOURCE)
        if not 1 <= max_depth <= MAX_HOPS:
            raise invalid_input("max_depth", f"phải nằm trong 1..{MAX_HOPS}", SOURCE)
        for rel in rel_types or ():
            if rel not in _REL_TYPES:
                raise invalid_input("rel_types", f"chỉ nhận {_REL_TYPES}", SOURCE)
        echo = {"entity": entity, "max_depth": max_depth}
        async with self._client.read_tx() as tx:
            root = await tx.fetch("entity_by_name_any_type", {"name": entity})
            if not root:
                return ToolOutcome(
                    not_found_result(SourceType.PGVECTOR, started=started, query_echo=echo),
                    identifier=f"entity {entity}",
                )
            edges = await tx.fetch(
                "related_knowledge",
                {
                    "root_id": root[0]["id"],
                    "max_depth": max_depth,
                    "rel_types": list(rel_types or ()),
                    "fanout": FANOUT_LIMIT,
                    "total_limit": FANOUT_LIMIT * MAX_HOPS,
                },
            )
        # Permission choke point #1 (R-C-001): a root entity whose backing document is denied is
        # treated as not_found; any edge whose backing document the caller cannot read is dropped
        # before it becomes an item/citation — default-deny over the one grant decision. An edge
        # with no backing document carries no restricted content, so it is kept (FR-019/AC-003).
        edge_docs = [str(e["doc_id"]) for e in edges if e.get("doc_id")]
        root_doc = str(root[0].get("document_id") or "")
        permitted = await self._permitted_document_ids([root_doc, *edge_docs])
        if root_doc and root_doc not in permitted:
            return ToolOutcome(
                not_found_result(SourceType.PGVECTOR, started=started, query_echo=echo),
                identifier=f"entity {entity}",
            )
        edges = [e for e in edges if not e.get("doc_id") or str(e["doc_id"]) in permitted]
        items: list[dict[str, Any]] = []
        citations: list[Citation] = []
        for index, edge in enumerate(edges):
            items.append(_edge_item(edge, index))
            citations.append(_edge_citation(edge, index))
        result = build_result(
            SourceType.PGVECTOR, items, citations, started=started, query_echo=echo
        )
        return ToolOutcome(result, query_description=f"liên quan tới {entity}")

    # == get_knowledge_summary (FR-020/AC-001) ===================================================

    async def get_knowledge_summary(
        self, *, subject: str, subject_type: str = "entity"
    ) -> ToolOutcome:
        started = time.monotonic()
        if not 1 <= len(subject) <= 256:
            raise invalid_input("subject", "độ dài phải từ 1 đến 256 ký tự", SOURCE)
        if subject_type not in _SUBJECT_TYPES:
            raise invalid_input("subject_type", f"chỉ nhận {_SUBJECT_TYPES}", SOURCE)
        echo = {"subject": subject, "subject_type": subject_type}
        async with self._client.read_tx() as tx:
            subject_id = subject
            if subject_type == "entity" and not _is_uuid(subject):
                resolved = await tx.fetch("entity_id_by_name", {"name": subject})
                if resolved:
                    subject_id = resolved[0]["subject_id"]
            rows = await tx.fetch(
                "knowledge_summary", {"subject_type": subject_type, "subject_id": subject_id}
            )
            if not rows:
                return ToolOutcome(
                    not_found_result(SourceType.PGVECTOR, started=started, query_echo=echo),
                    identifier=f"summary {subject}",
                )
            summary = rows[0]
            citation = await self._summary_citation(tx, summary)
        # Permission choke point #1 (R-C-001): a generated summary distils one or more source
        # documents; if the caller cannot read ALL of them, the summary could surface restricted
        # content, so it is denied (treated as not_found) — default-deny over the one grant
        # decision, before any summary text / provenance / citation is returned (FR-019/AC-001).
        # Resolved OUTSIDE the open read_tx: the permission read opens its own read-only tx (the
        # client lock
        # is non-reentrant).
        prov_docs = [
            str(p.get("document_id") or (p.get("evidence") or {}).get("document_id") or "")
            for p in (summary.get("provenance") or [])
        ]
        prov_docs = [d for d in prov_docs if _is_uuid(d)]
        if prov_docs:
            permitted = await self._permitted_document_ids(prov_docs)
            if any(d not in permitted for d in prov_docs):
                return ToolOutcome(
                    not_found_result(SourceType.PGVECTOR, started=started, query_echo=echo),
                    identifier=f"summary {subject}",
                )
        item = _summary_item(summary, subject, citation_ref=0)
        result = build_result(
            SourceType.PGVECTOR, [item], [citation], started=started, query_echo=echo
        )
        return ToolOutcome(result, query_description=f"summary của {subject}")

    async def _summary_citation(self, tx: Any, summary: dict[str, Any]) -> Citation:
        provenance = summary.get("provenance") or []
        doc_id = provenance[0].get("document_id") if provenance else None
        uri = None
        source_type = SourceType.PGVECTOR
        if doc_id and _is_uuid(str(doc_id)):
            doc = await tx.fetch("document_citation", {"document_id": str(doc_id)})
            if doc:
                uri = doc[0]["source_uri"]
                source_type = SourceType(doc[0]["source_type"])
        return Citation(
            source_type=source_type,
            label=f"Summary: {summary['subject_id']}",
            uri=uri,
            locator={"subject_type": summary["subject_type"], "subject_id": summary["subject_id"]},
        )

    # == get_document_version (FR-020/AC-001) ====================================================

    async def get_document_version(
        self, *, document_id: str, version: int | None = None
    ) -> ToolOutcome:
        started = time.monotonic()
        if not _is_uuid(document_id):
            raise invalid_input("document_id", "phải là UUID hợp lệ", SOURCE)
        if version is not None and version < 1:
            raise invalid_input("version", "phải là số nguyên dương", SOURCE)
        echo: dict[str, Any] = {"document_id": document_id}
        async with self._client.read_tx() as tx:
            live = await tx.fetch("live_document", {"document_id": document_id})
            if not live:  # missing OR tombstoned
                return ToolOutcome(
                    not_found_result(SourceType.PGVECTOR, started=started, query_echo=echo),
                    identifier=f"document {document_id}",
                )
            doc = live[0]
            versions = await tx.fetch(
                "document_versions",
                {"document_id": document_id, "version": version, "limit": 100},
            )
        # Permission choke point #1 (R-C-001): a document the caller holds no grant on is
        # indistinguishable from a missing one — default-deny, before any version row / content_hash
        # / source_uri citation is returned (FR-019/AC-001). Resolved OUTSIDE the open read_tx (the
        # permission read opens its own read-only tx; the client lock is non-reentrant).
        if not await self._permits_document(document_id):
            return ToolOutcome(
                not_found_result(SourceType.PGVECTOR, started=started, query_echo=echo),
                identifier=f"document {document_id}",
            )
        if not versions:
            return ToolOutcome(
                build_result(
                    SourceType.PGVECTOR, [], [], started=started, query_echo=echo
                ),
                query_description=f"version của document {document_id}",
            )
        items = [_version_item(v, citation_ref=0) for v in versions]
        citation = _document_citation(doc)
        result = build_result(
            SourceType.PGVECTOR, items, [citation], started=started, query_echo=echo
        )
        return ToolOutcome(result, query_description=f"version của document {document_id}")


# == Live-vs-Knowledge helpers (E5, T-105) =======================================================


class _EmptyTx:
    """A stand-in tx that answers no config rows, so the Live path falls back to the migration
    defaults when the real config tx is unavailable (spec §35: degrade, never fail)."""

    async def fetch(self, name: str, params: Any = None) -> list[dict[str, Any]]:
        return []


_SOURCE_TYPES = {s.value for s in SourceType}


def _source_type_of(raw: str | None) -> SourceType:
    return SourceType(raw) if raw in _SOURCE_TYPES else SourceType.PGVECTOR


def _issues_of(page: Any) -> list[dict[str, Any]]:
    """Extract the issue list from whatever the Jira provider returned.

    Tolerates a :class:`mcp_jira.client.JiraPage` (``.values``), a raw ``{"issues": [...]}`` body,
    or a bare list — so the reconciler is not coupled to one return shape.
    """
    values = getattr(page, "values", None)
    if values is not None:
        return [i for i in values if isinstance(i, dict)]
    if isinstance(page, dict):
        issues = page.get("issues") or page.get("values") or []
        return [i for i in issues if isinstance(i, dict)]
    if isinstance(page, list):
        return [i for i in page if isinstance(i, dict)]
    return []


def _live_value_from_issue(issue: dict[str, Any]) -> LiveValue:
    """Map one Jira issue to a comparable live current-work-status value + its provenance."""
    key = str(issue.get("key") or "")
    fields = issue.get("fields") or {}
    status = str((fields.get("status") or {}).get("name") or issue.get("status") or "")
    updated_raw = fields.get("updated") or issue.get("updated")
    url = issue.get("url") or issue.get("self")
    return LiveValue(
        value=status or "unknown",
        source=SourceType.JIRA,
        external_id=key or "unknown",
        url=str(url) if url else None,
        updated_time=_parse_dt(updated_raw),
        source_version=None,
        fetched_at=datetime.now(UTC),
    )


def _parse_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value:
        return None
    text = value.strip()
    # Jira Cloud emits ``+0000`` offsets; normalise to ``+00:00`` for fromisoformat.
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    elif len(text) >= 5 and (text[-5] in "+-") and text[-3] != ":":
        text = text[:-2] + ":" + text[-2:]
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _candidate_matching_key(
    candidates: Sequence[Candidate], key: str | None
) -> Candidate | None:
    """The first candidate whose content/source references the live issue ``key`` (same subject)."""
    if not key or key == "unknown":
        return None
    needle = key.casefold()
    for c in candidates:
        haystack = f"{c.content} {c.source_uri or ''} {c.source_id or ''}".casefold()
        if needle in haystack:
            return c
    return None


def _snapshot_status_text(candidate: Candidate) -> str:
    """The snapshot's statement of the current status for the subject.

    The snapshot is unstructured text. When it carries an explicit ``status: <value>`` phrase (the
    shape ingest writes for a tracked issue) we compare that extracted value against the live
    status — so an *agreeing* snapshot and live resolve to one FACT. Otherwise the full chunk is
    the value and any difference surfaces as a CONFLICT exposing both positions (spec §41): nothing
    is parsed away or silently merged.
    """
    content = (candidate.content or "").strip()
    match = re.search(r"status\s*[:=]\s*([^\n.;]+)", content, flags=re.IGNORECASE)
    if match:
        return match.group(1).strip()[:2048] or "unknown"
    return content[:2048] or "unknown"


# == row -> contract item mappers ================================================================


def _entity_item(
    entity: dict[str, Any], neighbours: list[dict[str, Any]], *, citation_ref: int
) -> dict[str, Any]:
    related = [
        {"rel_type": n["rel_type"], "name": n["name"], "entity_type": n["entity_type"], "depth": 1}
        for n in neighbours
    ]
    attributes = dict(entity.get("metadata") or {})
    if entity.get("display_name"):
        attributes.setdefault("display_name", entity["display_name"])
    return {
        "entity_type": entity["entity_type"],
        "name": entity["name"],
        "attributes": attributes,
        "related": related,
        "citation_ref": citation_ref,
    }


def _source_type(row: dict[str, Any]) -> SourceType:
    return SourceType(row["source_type"]) if row.get("source_type") else SourceType.PGVECTOR


def _entity_citation(entity: dict[str, Any], entity_type: str, name: str) -> Citation:
    uri = entity.get("source_uri")
    return Citation(
        source_type=_source_type(entity),
        label=f"{name} (knowledge entity)",
        uri=uri,
        locator={"entity_type": entity_type, "name": name},
    )


def _edge_item(edge: dict[str, Any], citation_ref: int) -> dict[str, Any]:
    return {
        "src": edge["src_name"],
        "dst": edge["dst_name"],
        "rel_type": edge["rel_type"],
        "depth": edge["depth"],
        "confidence": None,
        "citation_ref": citation_ref,
    }


def _edge_citation(edge: dict[str, Any], citation_ref: int) -> Citation:
    uri = edge.get("source_uri")
    return Citation(
        source_type=_source_type(edge),
        label=f"{edge['src_name']} → {edge['dst_name']} ({edge['rel_type']})",
        uri=uri,
        locator={"src": edge["src_name"], "dst": edge["dst_name"]},
    )


def _summary_item(summary: dict[str, Any], subject: str, *, citation_ref: int) -> dict[str, Any]:
    generated_at = summary.get("generated_at")
    gen_iso = generated_at.isoformat() if generated_at is not None else None
    return {
        "subject_type": summary["subject_type"],
        "subject_id": summary["subject_id"],
        "summary": summary["summary"],
        "provenance": [_claim_provenance(p, gen_iso) for p in (summary.get("provenance") or [])],
        "generated_at": gen_iso,
        "citation_ref": citation_ref,
    }


def _claim_provenance(raw: dict[str, Any], generated_at: str | None) -> dict[str, Any]:
    """Normalise an ingest-written provenance entry (``{document_id, chunk_id, uri}``) into the
    contract's ``ClaimProvenance`` shape. Missing ``source``/``updated_time`` is best-effort — the
    E8 grounding gate fills graded provenance; at E4 we only surface what ingest stored."""
    evidence = raw.get("evidence") or {}
    document_id = str(raw.get("document_id") or evidence.get("document_id") or "")
    chunk_id = str(raw.get("chunk_id") or evidence.get("chunk_id") or document_id)
    uri = raw.get("uri") or evidence.get("source_uri") or evidence.get("url")
    return {
        "source": raw.get("source") or "confluence",
        "updated_time": raw.get("updated_time") or generated_at or "1970-01-01T00:00:00Z",
        "evidence": {
            "document_id": document_id,
            "chunk_id": chunk_id,
            "url": uri,
            "source_uri": uri,
        },
    }


def _version_item(v: dict[str, Any], *, citation_ref: int) -> dict[str, Any]:
    def _iso(value: Any) -> str | None:
        return value.isoformat() if value is not None and hasattr(value, "isoformat") else value

    return {
        "document_id": str(v["document_id"]),
        "version": v["version"],
        "content_hash": v.get("content_hash"),
        "source_version": v.get("source_version"),
        "author": v.get("author"),
        "source_updated_at": _iso(v.get("source_updated_at")),
        "created_at": _iso(v.get("created_at")),
        "status": v["status"],
        "citation_ref": citation_ref,
    }


def _document_citation(doc: dict[str, Any]) -> Citation:
    return Citation(
        source_type=SourceType(doc["source_type"]),
        label=f"{doc.get('title') or 'document'} — versions",
        uri=doc.get("source_uri"),
        locator={"document_id": str(doc["document_id"])},
    )


def _chunk_item(c: Candidate, citation_ref: int, *, embedding_model: str) -> dict[str, Any]:
    return {
        "chunk_id": str(c.chunk_id),
        "document_id": str(c.document_id),
        "chunk_index": c.chunk_index,
        "similarity": max(0.0, min(1.0, round(c.rrf_score, 6))),
        "content": c.content,
        "heading_path": c.heading_path,
        "source_type": c.source_type,
        "source_id": c.source_id,
        "source_uri": c.source_uri,
        "title": c.title,
        "container": c.container,
        "source_updated_at": c.source_updated_at.isoformat() if c.source_updated_at else None,
        "ingested_at": c.ingested_at.isoformat() if c.ingested_at else None,
        "embedding_model": embedding_model,
        "truncated": False,
        "citation_ref": citation_ref,
    }


def _chunk_citation(c: Candidate, citation_ref: int) -> Citation:
    return Citation(
        source_type=SourceType(c.source_type),
        label=f"{c.title or c.source_id} ({c.source_type})",
        uri=c.source_uri,
        locator={"document_id": str(c.document_id), "chunk_index": c.chunk_index},
    )


# `ErrorCode` imported for callers that re-raise mapped errors; referenced to keep linters quiet.
_ = ErrorCode
