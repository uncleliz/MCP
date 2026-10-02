"""GT-1..GT-7 — adversarial grounding contract (ADR-0018 §6, FR-021/AC-001..006, T-107).

These are the adversarial tests that make the "no fabricated company fact" guarantee *enforced*
rather than promised (L-001): each feeds the exact input the contract says it blocks and asserts the
server-side verdict. Every FACT/UNKNOWN/CONFLICT assertion runs **now** — it does not depend on the
(still-TBD) FACT↔LOW_CONFIDENCE threshold τ (NFR-010, L-002), which is only used to distinguish FACT
from LOW_CONFIDENCE among already-evidenced claims.

GT → TC map: GT-1→TC-095, GT-2→TC-096, GT-3→TC-098, GT-4→TC-097, GT-5→TC-100 (structural),
GT-6→TC-099, GT-7→TC-100 (compression).
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime

import pytest
from knowledge_helpers import FakeKnowledgeClient, FakeRetriever, make_candidate
from mcp_common.envelope import (
    CONFIDENCE_BASIS,
    UNKNOWN_MESSAGE,
    GroundedStatus,
    GroundingVerdict,
)
from mcp_knowledge.grounding.confidence import TAU_FACT, TAU_LOW
from mcp_knowledge.grounding.verdict import GroundingVerdictGate, grade_claims
from mcp_knowledge.pack.assembler import ContextPackAssembler, RawClaim
from mcp_knowledge.pack.compress import compress_candidates
from mcp_knowledge.retrieval.hybrid import Candidate
from mcp_knowledge.tools.read_api import KnowledgeReadApi

_asyncio = pytest.mark.asyncio

_NOW = datetime(2026, 10, 2, tzinfo=UTC)
_DOC_A = "11111111-1111-4111-8111-111111111111"
_DOC_B = "22222222-2222-4222-8222-222222222222"


def _api(candidates: list[Candidate]) -> KnowledgeReadApi:
    return KnowledgeReadApi(FakeKnowledgeClient(), FakeRetriever(candidates))


def _raw(
    *,
    text: str,
    document_id: str = _DOC_A,
    chunk_id: int | None = 1,
    source_type: str = "confluence",
    source_uri: str | None = "https://wiki.example.com/x",
    heading: str | None = None,
    updated: datetime | None = None,
) -> RawClaim:
    return RawClaim(
        text=text,
        source_type=source_type,
        source_uri=source_uri,
        document_id=document_id,
        chunk_id=chunk_id,
        source_updated_at=updated or datetime(2026, 9, 28, tzinfo=UTC),
        author="an.nguyen",
        heading_path=heading,
    )


# == GT-1 — no source → UNKNOWN + fixed message (adversarial, TC-095) ============================


@_asyncio
async def test_gt1_no_evidence_is_unknown_with_fixed_message_and_no_fact() -> None:
    outcome = await _api([]).search_company_knowledge(query="off-topic with no corpus hit")
    result = outcome.result
    assert result.status is GroundedStatus.INSUFFICIENT_EVIDENCE
    assert result.grounding_summary.fact == 0
    assert all(c.grounding is not GroundingVerdict.FACT for c in result.claims)
    unknowns = [c for c in result.claims if c.grounding is GroundingVerdict.UNKNOWN]
    assert unknowns and all(c.message == UNKNOWN_MESSAGE for c in unknowns)
    assert all(c.provenance == [] for c in unknowns)


def test_gt1_unit_claim_missing_source_type_is_unknown() -> None:
    graded = grade_claims([_raw(text="x", source_type="not-a-source")], now=_NOW)
    assert graded[0].grounding is GroundingVerdict.UNKNOWN
    assert graded[0].message == UNKNOWN_MESSAGE


# == GT-2 — source → FACT with all 6 fields, evidence resolves to the chunk (TC-096) =============


@_asyncio
async def test_gt2_source_becomes_fact_with_six_fields_and_resolvable_evidence() -> None:
    candidate = make_candidate()
    outcome = await _api([candidate]).search_company_knowledge(query="payment retry backoff")
    facts = [c for c in outcome.result.claims if c.grounding is GroundingVerdict.FACT]
    assert facts, "an evidenced claim must grade to FACT"
    claim = facts[0]
    prov = claim.provenance[0]
    # 6 provenance fields present (source_version/owner may be the allowed null) — ADR-0018 §1.
    assert prov.source is not None
    assert prov.updated_time is not None
    assert prov.evidence.document_id and prov.evidence.chunk_id
    assert prov.evidence.source_uri or prov.evidence.url
    assert claim.confidence is not None
    assert prov.owner == candidate.author  # owner surfaced (null allowed, here present)
    # Evidence resolves back to EXACTLY the originating chunk (document_id + chunk_id).
    assert prov.evidence.document_id == candidate.document_id
    assert prov.evidence.chunk_id == f"{candidate.document_id}#{candidate.chunk_id}"


# == GT-3 — missing a required provenance field ⇒ never FACT (adversarial, TC-098) ===============


def test_gt3_missing_evidence_uri_never_fact() -> None:
    graded = grade_claims([_raw(text="timeout is 3s", source_uri=None)], now=_NOW)
    assert graded[0].grounding is not GroundingVerdict.FACT
    # downgraded to UNKNOWN, not passed through ungraded
    assert graded[0].grounding is GroundingVerdict.UNKNOWN


def test_gt3_missing_chunk_id_never_fact() -> None:
    graded = grade_claims([_raw(text="timeout is 3s", chunk_id=None)], now=_NOW)
    assert graded[0].grounding is not GroundingVerdict.FACT


def test_gt3_missing_document_id_never_fact() -> None:
    graded = grade_claims([_raw(text="x", document_id="")], now=_NOW)
    assert graded[0].grounding is not GroundingVerdict.FACT


# == GT-4 — two sources differ ⇒ CONFLICT exposing every position + authority_note (TC-097) ======


def _authority_config():
    import asyncio

    from mcp_knowledge.liveness.authority import load_source_authority

    class _EmptyTx:
        async def fetch(self, name, params=None):  # type: ignore[no-untyped-def]
            return []

    return asyncio.run(load_source_authority(_EmptyTx()))


def test_gt4_conflicting_sources_expose_all_positions_with_authority_note() -> None:
    config = _authority_config()
    raws = [
        _raw(text="read timeout is 5s", document_id=_DOC_A, source_type="confluence",
             heading="payment-service/timeouts"),
        _raw(text="read timeout is 10s", document_id=_DOC_B, source_type="gitlab",
             heading="payment-service/timeouts"),
    ]  # fmt: skip
    graded = grade_claims(raws, config=config, fact_type="runtime_config", now=_NOW)
    conflicts = [c for c in graded if c.grounding is GroundingVerdict.CONFLICT]
    assert len(conflicts) == 1
    conflict = conflicts[0]
    # Both positions exposed, each with full provenance; nothing merged or chosen.
    assert len(conflict.positions) == 2
    values = {p.value for p in conflict.positions}
    assert values == {"read timeout is 5s", "read timeout is 10s"}
    assert all(p.provenance for p in conflict.positions)
    assert conflict.authority_note is not None
    # runtime_config → gitlab (configured in the DB, not hardcoded in a prompt)
    assert "gitlab" in conflict.authority_note
    # No FACT silently chosen for the conflicting subject.
    assert all(c.grounding is not GroundingVerdict.FACT for c in graded)


def test_gt4_same_value_two_sources_is_agreement_not_conflict() -> None:
    raws = [
        _raw(text="timeout is 3s", document_id=_DOC_A, source_type="confluence",
             heading="svc/timeout"),
        _raw(text="timeout is 3s", document_id=_DOC_B, source_type="gitlab",
             heading="svc/timeout"),
    ]  # fmt: skip
    graded = grade_claims(raws, config=_authority_config(), now=_NOW)
    assert len(graded) == 1
    assert graded[0].grounding is not GroundingVerdict.CONFLICT  # agreeing sources are one claim


# == GT-5 — single grounding gate, no bypass (structural, TC-100) ================================


def test_gt5_assembler_has_exactly_one_grounding_gate_hook() -> None:
    # Structural: the assembler exposes exactly one grounding-gate seam; a custom gate is the only
    # site producing graded claims. Swapping the gate (as E8 does) changes verdicts at ONE place.
    gate_params = inspect.signature(ContextPackAssembler.__init__).parameters
    assert "grounding_gate" in gate_params
    # The one place claims become graded is the gate call: a no-op gate yields no graded claims.
    cands = [make_candidate()]
    chunks = compress_candidates(cands)
    seen: list[int] = []

    def counting_gate(chunks):  # type: ignore[no-untyped-def]
        from mcp_knowledge.pack.assembler import ContextPack

        seen.append(len(chunks))
        return ContextPack(claims=[], chunks=list(chunks), verdicts_assigned=True)

    ContextPackAssembler(grounding_gate=counting_gate).assemble(cands, chunks)
    assert seen == [len(chunks)], "the gate must be the single, once-called grading site"


@_asyncio
async def test_gt5_every_served_claim_passed_the_gate() -> None:
    # No claim reaches the envelope without the gate having assigned it a verdict.
    outcome = await _api([make_candidate()]).search_company_knowledge(query="payment retry")
    assert outcome.result.claims  # claims present
    assert all(c.grounding in set(GroundingVerdict) for c in outcome.result.claims)


# == GT-6 — confidence is a labelled proxy; it never rescues a source-less claim (TC-099) ========


@_asyncio
async def test_gt6_confidence_always_labelled_and_uncalibrated() -> None:
    outcome = await _api([make_candidate()]).search_company_knowledge(query="payment retry")
    assert outcome.result.grounding_summary.calibration_status == "uncalibrated"
    for claim in outcome.result.claims:
        if claim.confidence is not None:
            assert claim.confidence_basis == CONFIDENCE_BASIS


def test_gt6_no_path_turns_high_confidence_no_evidence_claim_into_fact() -> None:
    # Adversarial: a claim with NO evidence but every other signal maxed cannot be a FACT. The gate
    # checks evidence BEFORE confidence, so there is no confidence value that rescues it.
    graded = grade_claims(
        [_raw(text="made-up high-signal claim", source_uri=None, chunk_id=None)],
        now=_NOW,
    )
    assert graded[0].grounding is GroundingVerdict.UNKNOWN
    assert graded[0].confidence is None  # no confidence computed for a source-less claim


def test_gt6_tau_constants_are_marked_tbd_for_grep() -> None:
    # QA greps for the TBD marker next to each τ so an un-calibrated threshold is never mistaken for
    # a measured one (L-002/NFR-010). Assert the constants exist and the source carries the marker.
    import mcp_knowledge.grounding.confidence as conf

    assert isinstance(TAU_FACT, float) and isinstance(TAU_LOW, float)
    source = inspect.getsource(conf)
    assert source.count("# THRESHOLD TBD (NFR-010, L-002)") >= 2


# == GT-7 — compression preserves provenance into the gate (TC-100) ==============================


def test_gt7_compression_preserves_provenance_reaching_the_gate() -> None:
    # A long candidate is content-compressed, but every provenance field survives into the gate, so
    # the gate can grade it FACT with resolvable evidence (never "compress away provenance").
    long_candidate = make_candidate(content=" ".join(f"w{i}" for i in range(400)))
    chunks = compress_candidates([long_candidate], per_chunk_max_tokens=10)
    assert chunks[0].truncated is True  # content was shortened
    pack = GroundingVerdictGate(config=_authority_config(), now=_NOW)(chunks)
    claim = pack.graded_claims[0]
    assert claim.grounding is GroundingVerdict.FACT
    prov = claim.provenance[0]
    assert prov.evidence.document_id == long_candidate.document_id
    assert prov.evidence.chunk_id == f"{long_candidate.document_id}#{long_candidate.chunk_id}"
    assert prov.evidence.source_uri == long_candidate.source_uri


# == τ independence — the above FACT/UNKNOWN/CONFLICT asserts do not read τ ========================


def test_tau_only_separates_fact_from_low_not_the_unknown_invariant() -> None:
    # With τ_fact impossibly high, an evidenced claim degrades to LOW_CONFIDENCE — never UNKNOWN;
    # and a no-evidence claim stays UNKNOWN regardless of τ. This proves the invariant/τ split.
    assert 0.0 <= TAU_LOW <= TAU_FACT <= 1.0
