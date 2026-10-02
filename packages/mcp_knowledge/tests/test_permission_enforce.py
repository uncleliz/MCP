"""T-104 unit — ``enforce_permission`` default-deny logic (FR-019/AC-001, AC-002).

Pure, database-free tests of the one visibility decision: a candidate survives only when the caller
holds a grant on its document; absence of a grant — missing record, non-team principal, restricted
document — is deny. These pin the default-deny rule the adversarial integration test then proves
end to end.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from mcp_knowledge.permission.enforce import (
    TEAM_PRINCIPAL,
    CallerContext,
    PermissionGrants,
    build_permission_filter,
    enforce_permission,
    load_grants,
)
from mcp_knowledge.retrieval.hybrid import Candidate


def _candidate(doc: str, cid: int = 1) -> Candidate:
    return Candidate(
        chunk_id=cid, document_id=doc, chunk_index=0, content=f"c{cid}", heading_path=None,
        source_type="confluence", source_id=str(cid), source_uri=f"https://e/{cid}", title="t",
        container="PAY", author=None, source_updated_at=datetime(2026, 9, 1, tzinfo=UTC),
        ingested_at=None, rrf_score=1.0 / cid, legs={"vector": cid},
    )  # fmt: skip


class _FakeGrantTx:
    """Serves ``document_grants`` from a map, mirroring the SQL default-deny intersection."""

    def __init__(self, grants: dict[str, set[str]]) -> None:
        self._grants = grants
        self.calls: list[dict] = []

    async def fetch(self, name: str, params: dict | None = None):
        assert name == "document_grants"
        self.calls.append(params or {})
        wanted = set((params or {}).get("document_ids") or [])
        principals = set((params or {}).get("principals") or [])
        return [
            {"document_id": doc, "principal": p}
            for doc in wanted
            for p in self._grants.get(doc, set()) & principals
        ]


class _FakeClient:
    def __init__(self, grants: dict[str, set[str]]) -> None:
        self.tx = _FakeGrantTx(grants)

    def read_tx(self):
        client = self

        class _Ctx:
            async def __aenter__(self):
                return client.tx

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


# -- CallerContext ------------------------------------------------------------------------------


def test_team_member_holds_team_pseudo_principal() -> None:
    assert TEAM_PRINCIPAL in CallerContext().effective_principals()


def test_non_team_caller_does_not_hold_team_principal() -> None:
    caller = CallerContext(principals=frozenset({"user:alice"}), is_team_member=False)
    assert TEAM_PRINCIPAL not in caller.effective_principals()
    assert "user:alice" in caller.effective_principals()


# -- enforce_permission (the one decision) ------------------------------------------------------


def test_grant_present_keeps_candidate() -> None:
    cands = [_candidate("doc-a")]
    grants = PermissionGrants(frozenset({"doc-a"}))
    assert enforce_permission(cands, grants) == cands


def test_missing_grant_is_denied_default_deny() -> None:
    # AC-002: no grant record at all => the document is dropped (not surfaced).
    cands = [_candidate("doc-a"), _candidate("doc-restricted", cid=2)]
    grants = PermissionGrants(frozenset({"doc-a"}))
    kept = enforce_permission(cands, grants)
    assert [c.document_id for c in kept] == ["doc-a"]


def test_empty_grants_denies_everything() -> None:
    cands = [_candidate("doc-a"), _candidate("doc-b", cid=2)]
    assert enforce_permission(cands, PermissionGrants(frozenset())) == []


def test_build_permission_filter_is_default_deny_closure() -> None:
    cands = [_candidate("doc-a"), _candidate("doc-b", cid=2)]
    filt = build_permission_filter(PermissionGrants(frozenset({"doc-b"})))
    assert [c.document_id for c in filt(cands)] == ["doc-b"]


# -- load_grants (DB intersection, default-deny) ------------------------------------------------


async def test_load_grants_resolves_team_document_for_team_member() -> None:
    client = _FakeClient({"doc-a": {TEAM_PRINCIPAL}})
    grants = await load_grants(client, [_candidate("doc-a")], CallerContext())
    assert grants.allows("doc-a")


async def test_load_grants_denies_restricted_without_explicit_grant() -> None:
    # 'doc-restricted' only has a grant for user:bob; a plain team member must not resolve it.
    client = _FakeClient({"doc-restricted": {"user:bob"}})
    grants = await load_grants(client, [_candidate("doc-restricted")], CallerContext())
    assert not grants.allows("doc-restricted")


async def test_load_grants_allows_restricted_with_explicit_principal_grant() -> None:
    client = _FakeClient({"doc-restricted": {"user:bob"}})
    caller = CallerContext(principals=frozenset({"user:bob"}))
    grants = await load_grants(client, [_candidate("doc-restricted")], caller)
    assert grants.allows("doc-restricted")


async def test_load_grants_empty_candidates_skips_db() -> None:
    client = _FakeClient({"doc-a": {TEAM_PRINCIPAL}})
    grants = await load_grants(client, [], CallerContext())
    assert grants.permitted_document_ids == frozenset()
    assert client.tx.calls == []  # no database round-trip when there is nothing to check


async def test_load_grants_no_principals_denies_without_db() -> None:
    client = _FakeClient({"doc-a": {TEAM_PRINCIPAL}})
    caller = CallerContext(principals=frozenset(), is_team_member=False)
    grants = await load_grants(client, [_candidate("doc-a")], caller)
    assert grants.permitted_document_ids == frozenset()
    assert client.tx.calls == []


async def test_load_grants_deduplicates_candidate_document_ids() -> None:
    client = _FakeClient({"doc-a": {TEAM_PRINCIPAL}})
    cands: Sequence[Candidate] = [_candidate("doc-a", 1), _candidate("doc-a", 2)]
    grants = await load_grants(client, cands, CallerContext())
    assert grants.allows("doc-a")
    assert client.tx.calls[0]["document_ids"] == ["doc-a"]
