"""T-096 — GroundedResult envelope (ADR-0018 §5): invariants + backward compatibility.

Verifies the grounded envelope encodes the contract invariants at construction (so a malformed
claim raises immediately) and that the base `ToolResult` / 9-source envelope is untouched (EB-002).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from mcp_common.envelope import (
    CONFIDENCE_BASIS,
    UNKNOWN_MESSAGE,
    Citation,
    Claim,
    ClaimEvidence,
    ClaimPosition,
    ClaimProvenance,
    GroundedResult,
    GroundedStatus,
    GroundingSummary,
    GroundingVerdict,
    Meta,
    ResultStatus,
    SourceType,
    ToolResult,
)

NOW = datetime(2026, 10, 1, 6, 0, tzinfo=UTC)


def _meta() -> Meta:
    return Meta(source=SourceType.PGVECTOR, returned=1, has_more=False, elapsed_ms=3, as_of=NOW)


def _provenance() -> ClaimProvenance:
    return ClaimProvenance(
        source=SourceType.CONFLUENCE,
        updated_time=NOW,
        evidence=ClaimEvidence(
            document_id="3f2504e0-4f89-11d3-9a0c-0305e82c3301", chunk_id="918233"
        ),
    )


def _summary(**kw: object) -> GroundingSummary:
    base = {"fact": 1, "low_confidence": 0, "unknown": 0, "conflict": 0}
    base.update(kw)
    return GroundingSummary(**base)  # type: ignore[arg-type]


# -- backward compatibility: base envelope unchanged (EB-002) ---------------------------------


def test_base_tool_result_still_works() -> None:
    r = ToolResult(
        status=ResultStatus.OK,
        items=[{"citation_ref": 0}],
        citations=[Citation(source_type=SourceType.CONFLUENCE, label="x", uri="https://e/1")],
        meta=_meta(),
    )
    assert r.status is ResultStatus.OK


def test_grounded_status_is_separate_from_result_status() -> None:
    # insufficient_evidence must NOT leak into the 9-source ResultStatus enum.
    assert "insufficient_evidence" not in {s.value for s in ResultStatus}
    assert GroundedStatus.INSUFFICIENT_EVIDENCE.value == "insufficient_evidence"


# -- claim verdict invariants (GT-1..GT-4) ----------------------------------------------------


def test_fact_claim_requires_provenance() -> None:
    with pytest.raises(ValueError, match="provenance"):
        Claim(text="x", grounding=GroundingVerdict.FACT)


def test_fact_claim_with_provenance_ok() -> None:
    c = Claim(
        text="payments retries 3x",
        grounding=GroundingVerdict.FACT,
        confidence=0.8,
        confidence_basis=CONFIDENCE_BASIS,
        provenance=[_provenance()],
    )
    assert c.grounding is GroundingVerdict.FACT


def test_unknown_claim_requires_fixed_message_and_empty_provenance() -> None:
    # GT-1: no-evidence -> UNKNOWN with the fixed message.
    with pytest.raises(ValueError, match="fixed message"):
        Claim(text="x", grounding=GroundingVerdict.UNKNOWN)
    with pytest.raises(ValueError, match="empty provenance"):
        Claim(
            text="x",
            grounding=GroundingVerdict.UNKNOWN,
            message=UNKNOWN_MESSAGE,
            provenance=[_provenance()],
        )
    ok = Claim(text="x", grounding=GroundingVerdict.UNKNOWN, message=UNKNOWN_MESSAGE)
    assert ok.message == UNKNOWN_MESSAGE


def test_conflict_claim_requires_two_positions() -> None:
    # GT-4: conflict exposes >=2 positions.
    with pytest.raises(ValueError, match=">=2 positions"):
        Claim(
            text="x",
            grounding=GroundingVerdict.CONFLICT,
            positions=[ClaimPosition(value="a", provenance=[_provenance()])],
        )
    ok = Claim(
        text="x",
        grounding=GroundingVerdict.CONFLICT,
        authority_note="jira is authoritative for current_work_status",
        positions=[
            ClaimPosition(value="open", provenance=[_provenance()]),
            ClaimPosition(value="closed", provenance=[_provenance()]),
        ],
    )
    assert len(ok.positions) == 2


# -- result-level invariant (ADR-0018 §5 invariant 4) -----------------------------------------


def test_insufficient_evidence_forbids_fact_claim() -> None:
    fact = Claim(text="x", grounding=GroundingVerdict.FACT, provenance=[_provenance()])
    with pytest.raises(ValueError, match="insufficient_evidence"):
        GroundedResult(
            status=GroundedStatus.INSUFFICIENT_EVIDENCE,
            claims=[fact],
            grounding_summary=_summary(),
            meta=_meta(),
        )


def test_insufficient_evidence_with_unknown_claim_ok() -> None:
    unknown = Claim(text="x", grounding=GroundingVerdict.UNKNOWN, message=UNKNOWN_MESSAGE)
    r = GroundedResult(
        status=GroundedStatus.INSUFFICIENT_EVIDENCE,
        claims=[unknown],
        grounding_summary=_summary(fact=0, unknown=1),
        meta=_meta(),
    )
    assert r.status is GroundedStatus.INSUFFICIENT_EVIDENCE


def test_grounding_summary_defaults_uncalibrated_and_enabled() -> None:
    s = _summary()
    assert s.calibration_status == "uncalibrated"  # NFR-010 τ TBD
    assert s.reranker == "enabled"


def test_grounding_summary_reranker_disabled_allowed() -> None:
    s = _summary(reranker="disabled")
    assert s.reranker == "disabled"


def test_grounding_summary_rejects_bad_enum() -> None:
    with pytest.raises(ValueError, match="reranker"):
        _summary(reranker="half")
    with pytest.raises(ValueError, match="calibration_status"):
        _summary(calibration_status="maybe")


def test_evidence_resolvable_fields_present() -> None:
    # GT-2 precondition: evidence carries document_id + chunk_id so it resolves via kb_get_document.
    prov = _provenance()
    assert prov.evidence.document_id and prov.evidence.chunk_id
