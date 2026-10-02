"""Grounded-result assembly for the knowledge search tools (ADR-0018 §5, ADR-0020).

At E4 the grounding *verdict* gate is NOT yet built (that is E8/T-106). The invariant already
enforced here is the one that must hold *regardless* of the (still-TBD) FACT↔LOW_CONFIDENCE
threshold: a claim without resolvable evidence is **never** a FACT — it is ``UNKNOWN`` with a fixed
message, and the result status is ``insufficient_evidence``. The pipeline (permission choke point #1
→ hybrid retrieve → rerank → compress → context-pack assembler) runs end to end so the single choke
points are exercised; the assembler yields raw claims and this module turns them into a
contract-valid :class:`GroundedResult` without inventing a verdict.

E8 replaces :func:`ground_pack` with the deterministic verdict gate; the envelope shape and the
rendering here stay the same.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from mcp_common.envelope import (
    UNKNOWN_MESSAGE,
    Citation,
    Claim,
    DataFreshness,
    GroundedResult,
    GroundedStatus,
    GroundingSummary,
    GroundingVerdict,
    Meta,
    SourceType,
)

from mcp_knowledge.pack.assembler import ContextPack, RawClaim

__all__ = [
    "GroundedOutcome",
    "build_grounded_result",
    "build_reconciled_result",
    "render_grounded_text",
]


class GroundedOutcome:
    """What a grounded tool coroutine returns: the grounded envelope + a short query description."""

    def __init__(self, result: GroundedResult, *, query_description: str | None = None) -> None:
        self.result = result
        self.query_description = query_description


def _elapsed_ms(started: float) -> int:
    import time

    return max(int((time.monotonic() - started) * 1000), 0)


def build_grounded_result(
    pack: ContextPack,
    *,
    started: float,
    query_echo: dict[str, object],
    reranker_status: str,
    data_freshness: DataFreshness | None = None,
    warnings: Sequence[str] = (),
) -> GroundedResult:
    """Build a contract-valid :class:`GroundedResult` from an assembled context-pack.

    When the E8 grounding gate has graded the pack (``verdicts_assigned`` ⇒ ``graded_claims``
    populated), those graded claims — each carrying a server-assigned verdict and, for
    FACT/LOW_CONFIDENCE, the full six-field provenance — are assembled via
    :func:`build_reconciled_result`, so FACT/CONFLICT/UNKNOWN all flow through one builder and the
    status/citations invariants hold (ADR-0018 §5). The gate owns the verdict; this function never
    invents one.

    When the gate has NOT run (the E3/E4 skeleton path, or an empty pack), no claim can be a FACT:
    every raw claim is surfaced as the deterministic ``UNKNOWN`` (no-evidence ⇒ UNKNOWN invariant,
    GT-1) and the status is ``insufficient_evidence``. This is the honest state until a gate grades
    claims; it never fabricates a FACT.
    """
    if pack.verdicts_assigned:
        return build_reconciled_result(
            pack.graded_claims,
            started=started,
            query_echo=query_echo,
            reranker_status=reranker_status,
            data_freshness=data_freshness,
            warnings=warnings,
        )
    claims = [_unknown_claim(raw) for raw in pack.claims] or [_unknown_placeholder(query_echo)]
    summary = GroundingSummary(
        fact=0,
        low_confidence=0,
        unknown=len(claims),
        conflict=0,
        reranker="disabled" if reranker_status == "disabled" else "enabled",
        calibration_status="uncalibrated",
    )
    meta = Meta(
        source=SourceType.PGVECTOR,
        returned=len(claims),
        has_more=False,
        next_cursor=None,
        truncated=False,
        elapsed_ms=_elapsed_ms(started),
        as_of=datetime.now(UTC),
        query_echo=dict(query_echo),
        warnings=[w[:512] for w in warnings],
        data_freshness=data_freshness,
    )
    # No FACT ⇒ insufficient_evidence; citations stay empty (UNKNOWN claims carry no provenance).
    return GroundedResult(
        status=GroundedStatus.INSUFFICIENT_EVIDENCE,
        claims=claims,
        grounding_summary=summary,
        citations=[],
        meta=meta,
    )


def _citations_from_claims(claims: Sequence[Claim]) -> list[Citation]:
    """Build citations from the provenance of graded claims (FACT/LOW_CONFIDENCE/CONFLICT).

    Each distinct ``source_uri`` becomes one citation so the rendered answer can point at the live
    *and* the snapshot source. UNKNOWN claims carry no provenance, so they add nothing.
    """
    seen: dict[str, Citation] = {}
    for claim in claims:
        provenances = list(claim.provenance)
        for position in claim.positions:
            provenances.extend(position.provenance)
        for prov in provenances:
            uri = prov.evidence.source_uri or prov.evidence.url
            key = uri or f"{prov.source.value}:{prov.evidence.document_id}"
            if key in seen:
                continue
            seen[key] = Citation(
                source_type=prov.source,
                label=f"{prov.source.value}: {prov.evidence.document_id}"[:512],
                uri=uri,
                locator={"document_id": prov.evidence.document_id},
            )
    return list(seen.values())


def build_reconciled_result(
    claims: Sequence[Claim],
    *,
    started: float,
    query_echo: dict[str, object],
    reranker_status: str,
    data_freshness: DataFreshness | None = None,
    warnings: Sequence[str] = (),
) -> GroundedResult:
    """Build a contract-valid :class:`GroundedResult` from already-graded live-vs-knowledge claims.

    E5 (reconcile) grades the live-vs-knowledge claims itself (agree ⇒ FACT, differ ⇒ CONFLICT,
    live-unavailable ⇒ LOW_CONFIDENCE) because that verdict depends on comparing the two *values*.
    This builder assembles those graded claims into the envelope, keeping the same invariants as
    :func:`build_grounded_result`: ``status`` is ``insufficient_evidence`` iff no claim reached a
    non-UNKNOWN verdict, otherwise ``ok`` (``partial`` would need an explicit truncation signal we
    do not have here). Citations are derived from the claims' provenance (both sides of a conflict).
    """
    graded = list(claims) or [_unknown_placeholder(query_echo)]
    fact = sum(1 for c in graded if c.grounding is GroundingVerdict.FACT)
    low = sum(1 for c in graded if c.grounding is GroundingVerdict.LOW_CONFIDENCE)
    unknown = sum(1 for c in graded if c.grounding is GroundingVerdict.UNKNOWN)
    conflict = sum(1 for c in graded if c.grounding is GroundingVerdict.CONFLICT)
    has_grounded = fact + low + conflict > 0
    summary = GroundingSummary(
        fact=fact,
        low_confidence=low,
        unknown=unknown,
        conflict=conflict,
        reranker="disabled" if reranker_status == "disabled" else "enabled",
        calibration_status="uncalibrated",
    )
    citations = _citations_from_claims(graded) if has_grounded else []
    meta = Meta(
        source=SourceType.PGVECTOR,
        returned=len(graded),
        has_more=False,
        next_cursor=None,
        truncated=False,
        elapsed_ms=_elapsed_ms(started),
        as_of=datetime.now(UTC),
        query_echo=dict(query_echo),
        warnings=[w[:512] for w in warnings],
        data_freshness=data_freshness,
    )
    status = GroundedStatus.OK if has_grounded else GroundedStatus.INSUFFICIENT_EVIDENCE
    return GroundedResult(
        status=status,
        claims=graded,
        grounding_summary=summary,
        citations=citations,
        meta=meta,
    )


def _unknown_claim(raw: RawClaim) -> Claim:
    return Claim(
        text=raw.text[:4096],
        grounding=GroundingVerdict.UNKNOWN,
        confidence=None,
        provenance=[],
        message=UNKNOWN_MESSAGE,
    )


def _unknown_placeholder(query_echo: dict[str, object]) -> Claim:
    text = str(query_echo.get("query") or query_echo.get("subject") or "truy vấn")
    return Claim(
        text=text[:4096],
        grounding=GroundingVerdict.UNKNOWN,
        confidence=None,
        provenance=[],
        message=UNKNOWN_MESSAGE,
    )


def render_grounded_text(result: GroundedResult, *, query_description: str | None = None) -> str:
    """Render the text Claude reads for a grounded result (contract info.x-text-rendering).

    UNKNOWN / insufficient_evidence surfaces the fixed message, never a fabricated answer.
    """
    lines: list[str] = []
    counts = result.grounding_summary
    if result.status is GroundedStatus.INSUFFICIENT_EVIDENCE:
        lines.append(
            "Không đủ bằng chứng để khẳng định "
            f"{query_description or 'câu hỏi'}: không có nguồn chính thức."
        )
    else:
        lines.append(
            f"{counts.fact} khẳng định có nguồn, {counts.conflict} mâu thuẫn, "
            f"{counts.unknown} chưa rõ."
        )
    for index, claim in enumerate(result.claims):
        if claim.grounding is GroundingVerdict.UNKNOWN:
            lines.append(f"[{index}] {claim.message}")
        elif claim.grounding is GroundingVerdict.CONFLICT:
            values = ", ".join(p.value for p in claim.positions)
            lines.append(f"[{index}] CONFLICT: {claim.text} — các giá trị: {values}")
        else:
            lines.append(f"[{index}] {claim.text}")
    for citation in result.citations:
        if citation.uri:
            lines.append(f"Nguồn: {citation.label} — {citation.uri}")
    if counts.reranker == "disabled":
        lines.append("Lưu ý: reranker ở chế độ RRF-only (chất lượng xếp hạng giảm, minh bạch).")
    if result.meta.warnings:
        lines.append("Lưu ý: " + "; ".join(result.meta.warnings))
    return "\n".join(lines)
