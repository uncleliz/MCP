"""The deterministic grounding verdict gate — choke point #2 (ADR-0018 §2/§3/§4, T-106).

This is the single server-side place a claim is assigned exactly one verdict
(``FACT``/``LOW_CONFIDENCE``/``UNKNOWN``/``CONFLICT``). It runs **after** the permission choke point
(#1, E6) and **after** rerank + context-compression, as the last step before the context-pack
leaves the server. It is wired as the one :class:`~mcp_knowledge.pack.assembler.GroundingGate` hook
on the one :class:`~mcp_knowledge.pack.assembler.ContextPackAssembler`; there is no second grading
site (L-001, GT-5).

Invariants enforced **now**, independently of the (TBD) FACT↔LOW_CONFIDENCE threshold τ (ADR-0018
D1 "Bất biến enforce NGAY"):

1. **no valid evidence ⇒ UNKNOWN** with the fixed message — never a fabricated FACT (GT-1/GT-3).
2. **evidence is checked before confidence** — confidence never "rescues" a source-less claim into
   a FACT (GT-6).
3. **≥2 independent sources with different values for one claim ⇒ CONFLICT**, exposing every
   position with full provenance + the configured ``authority_note`` (GT-4). Nothing merged or
   silently chosen.
4. A claim with full, resolvable provenance has its confidence computed deterministically
   (:mod:`mcp_knowledge.grounding.confidence`) and is graded FACT (``≥ τ_fact``) or LOW_CONFIDENCE
   otherwise, among the already-evidenced claims only. ``calibration_status`` stays
   ``uncalibrated`` until τ is measured on a real golden-set (NFR-010).

The verdict and confidence are **deterministic**: no LLM, no egress, no wall-clock in the decision
(``now`` is injectable so freshness is reproducible in tests). Permission is NOT re-checked here —
visibility is decided once at choke point #1; re-filtering would duplicate a guarantee (L-001).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from mcp_common.envelope import (
    UNKNOWN_MESSAGE,
    Claim,
    ClaimEvidence,
    ClaimPosition,
    ClaimProvenance,
    GroundingVerdict,
    SourceType,
)

from mcp_knowledge.grounding.confidence import (
    DEFAULT_WEIGHTS,
    ConfidenceWeights,
    agreement_factor,
    compute_confidence,
    normalise_retrieval,
)
from mcp_knowledge.liveness.authority import SourceAuthorityConfig
from mcp_knowledge.liveness.freshness import assess_freshness
from mcp_knowledge.pack.assembler import ContextPack, RawClaim
from mcp_knowledge.pack.compress import CompressedChunk

__all__ = [
    "GroundingVerdictGate",
    "grade_claims",
]

_SOURCE_TYPES = {s.value for s in SourceType}


def _source_type_of(raw: str | None) -> SourceType | None:
    return SourceType(raw) if raw in _SOURCE_TYPES else None


def _has_valid_evidence(raw: RawClaim, source: SourceType | None) -> bool:
    """True iff the raw claim carries the required, resolvable provenance (ADR-0018 §1).

    A FACT needs every required field *present* (values may be the allowed ``null`` for
    ``source_version``/``owner``) and an ``evidence`` that is actually resolvable: a known
    ``source`` plus a retraceable ``document_id`` + ``chunk_id`` **and** a link
    (``source_uri``/``url``). Missing any of these ⇒ the claim can never be a FACT; the gate makes
    it UNKNOWN rather than passing it through ungraded (GT-3).
    """
    if source is None:
        return False
    if not (raw.document_id or "").strip():
        return False
    if raw.chunk_id is None:
        return False
    if not (raw.source_uri or "").strip():
        return False
    return True


@dataclass
class _ClaimGroup:
    """Chunks the gate treats as asserting the same claim (same normalised subject key)."""

    key: str
    text: str
    members: list[tuple[RawClaim, float]] = field(default_factory=list)  # (claim, retrieval01)

    def distinct_values(self) -> dict[str, list[RawClaim]]:
        """Map a normalised value → the raw claims asserting it (for CONFLICT detection)."""
        buckets: dict[str, list[RawClaim]] = {}
        for raw, _ in self.members:
            buckets.setdefault(_norm(raw.text), []).append(raw)
        return buckets


def _norm(value: str) -> str:
    return " ".join((value or "").strip().casefold().split())


def _claim_key(raw: RawClaim) -> str:
    """The subject key used to group agreeing/conflicting claims.

    The minimal gate (ADR-0018 "coi mỗi chunk-đáng-tin là một claim") keys by the chunk's
    ``heading_path`` when present (the stable subject a document section is about); otherwise the
    claim stands alone under its own text, so unrelated chunks are never spuriously grouped.
    """
    heading = (raw.heading_path or "").strip()
    return _norm(heading) if heading else f"__self__::{_norm(raw.text)}"


def _provenance_of(raw: RawClaim, source: SourceType, *, confidence: float) -> ClaimProvenance:
    updated = raw.source_updated_at
    updated_time = updated if isinstance(updated, datetime) else datetime(1970, 1, 1, tzinfo=UTC)
    return ClaimProvenance(
        source=source,
        updated_time=updated_time,
        evidence=ClaimEvidence(
            document_id=str(raw.document_id),
            chunk_id=f"{raw.document_id}#{raw.chunk_id}",
            url=raw.source_uri,
            source_uri=raw.source_uri,
        ),
        owner=raw.author,
        confidence=round(max(0.0, min(1.0, confidence)), 6),
    )


def _unknown(raw: RawClaim) -> Claim:
    return Claim(
        text=(raw.text or "")[:4096],
        grounding=GroundingVerdict.UNKNOWN,
        confidence=None,
        provenance=[],
        message=UNKNOWN_MESSAGE,
    )


def _fact_fact_type(fact_type: str | None) -> str:
    return fact_type or "architecture"


def grade_claims(
    raws: Sequence[RawClaim],
    *,
    config: SourceAuthorityConfig | None = None,
    weights: ConfidenceWeights = DEFAULT_WEIGHTS,
    fact_type: str = "architecture",
    now: datetime | None = None,
) -> list[Claim]:
    """Grade raw snapshot claims into graded :class:`Claim`s (the gate's core, pure function).

    * A raw claim without resolvable provenance ⇒ ``UNKNOWN`` (fixed message), never FACT (GT-1/3).
    * Claims grouped under the same subject key that carry **different** values across **≥2
      independent sources** ⇒ one ``CONFLICT`` exposing every position + ``authority_note`` (GT-4).
    * Otherwise ⇒ confidence = ``retrieval^w_r · agreement^w_a · freshness^w_f`` (deterministic),
      verdict FACT (``≥ τ_fact``) or LOW_CONFIDENCE, with the full six-field provenance (GT-2).

    ``now`` is injectable so the freshness factor — and therefore the whole grading — is
    reproducible without a wall-clock dependency.
    """
    now = now or datetime.now(UTC)
    horizon = config.horizon_for(fact_type) if config else None

    # Min-max bounds for retrieval normalisation over the evidenced candidate set (rrf_score proxy
    # when no rerank score is available; ordering is already the reranked order).
    scores = [float(getattr(raw, "retrieval_score", 0.0) or 0.0) for raw in raws]
    lo = min(scores) if scores else 0.0
    hi = max(scores) if scores else 0.0

    # Partition into UNKNOWN (no evidence) and evidenced claims; group evidenced by subject key.
    evidenced: list[tuple[RawClaim, SourceType, float]] = []
    graded: list[Claim] = []
    groups: dict[str, _ClaimGroup] = {}
    order: list[str] = []

    for raw, raw_score in zip(raws, scores, strict=True):
        source = _source_type_of(raw.source_type)
        if not _has_valid_evidence(raw, source):
            graded.append(_unknown(raw))  # evidence-check BEFORE confidence (GT-6)
            continue
        assert source is not None
        retrieval01 = normalise_retrieval(raw_score, lo=lo, hi=hi)
        evidenced.append((raw, source, retrieval01))
        key = _claim_key(raw)
        if key not in groups:
            groups[key] = _ClaimGroup(key=key, text=raw.text or "")
            order.append(key)
        groups[key].members.append((raw, retrieval01))

    _ = evidenced  # evidenced list retained for clarity; grading walks the groups below

    for key in order:
        group = groups[key]
        graded.append(
            _grade_group(
                group,
                config=config,
                weights=weights,
                fact_type=fact_type,
                horizon=horizon,
                now=now,
            )
        )
    return graded


def _independent_sources(raws: Sequence[RawClaim]) -> int:
    """Count distinct independent sources (different ``source_type`` OR different document)."""
    seen: set[tuple[str, str]] = set()
    for raw in raws:
        seen.add((raw.source_type or "", str(raw.document_id)))
    return len(seen)


def _grade_group(
    group: _ClaimGroup,
    *,
    config: SourceAuthorityConfig | None,
    weights: ConfidenceWeights,
    fact_type: str,
    horizon: int | None,
    now: datetime,
) -> Claim:
    buckets = group.distinct_values()
    all_raws = [raw for raw, _ in group.members]

    # CONFLICT: ≥2 distinct values across ≥2 independent sources (ADR-0018 §4, GT-4).
    if len(buckets) >= 2 and _independent_sources(all_raws) >= 2:
        return _conflict_claim(group, buckets, config=config, fact_type=fact_type)

    # Agreement over the group's independent sources; freshness over the freshest member.
    n_independent = _independent_sources(all_raws)
    agreement = agreement_factor(n_independent)
    retrieval = max(score for _, score in group.members)
    freshest = _freshest(all_raws)
    fresh = assess_freshness(
        updated_time=freshest.source_updated_at
        if isinstance(freshest.source_updated_at, datetime)
        else None,
        horizon_hours=horizon,
        now=now,
    )
    confidence = compute_confidence(
        retrieval=retrieval,
        agreement=agreement,
        freshness=fresh.factor,
        weights=weights,
    )
    from mcp_knowledge.grounding.confidence import TAU_FACT

    verdict = GroundingVerdict.FACT if confidence >= TAU_FACT else GroundingVerdict.LOW_CONFIDENCE
    provenance = [
        _provenance_of(raw, _source_type_of(raw.source_type) or SourceType.PGVECTOR,
                       confidence=confidence)
        for raw in all_raws
    ]  # fmt: skip
    return Claim(
        text=(group.text or "")[:4096],
        grounding=verdict,
        confidence=confidence,
        confidence_basis=(
            "evidence-strength (retrieval×agreement×freshness), NOT P(claim true)"
        ),
        provenance=provenance,
    )


def _freshest(raws: Sequence[RawClaim]) -> RawClaim:
    def _key(raw: RawClaim) -> datetime:
        value = raw.source_updated_at
        return value if isinstance(value, datetime) else datetime(1970, 1, 1, tzinfo=UTC)

    return max(raws, key=_key)


def _conflict_claim(
    group: _ClaimGroup,
    buckets: dict[str, list[RawClaim]],
    *,
    config: SourceAuthorityConfig | None,
    fact_type: str,
) -> Claim:
    note = config.authority_note(fact_type) if config else None
    positions: list[ClaimPosition] = []
    for raws in buckets.values():
        representative = raws[0]
        positions.append(
            ClaimPosition(
                value=(representative.text or "")[:2048] or "unknown",
                provenance=[
                    _provenance_of(
                        raw,
                        _source_type_of(raw.source_type) or SourceType.PGVECTOR,
                        confidence=1.0,
                    )
                    for raw in raws
                ],
            )
        )
    return Claim(
        text=(group.text or "")[:4096],
        grounding=GroundingVerdict.CONFLICT,
        positions=positions,
        authority_note=note,
    )


class GroundingVerdictGate:
    """The one grounding gate (choke point #2): grades a compressed pack's claims deterministically.

    Instances are callable with the compressed chunks the assembler kept (after permission #1) and
    return a :class:`ContextPack` whose ``graded_claims`` carry a verdict and (for FACT/LOW) the
    full six-field provenance. ``verdicts_assigned`` is flipped to True so the envelope builder
    knows the pack is graded. The raw ``claims`` list is preserved for GT-7 (provenance survives
    compression and reaches the gate intact).
    """

    def __init__(
        self,
        *,
        config: SourceAuthorityConfig | None = None,
        weights: ConfidenceWeights = DEFAULT_WEIGHTS,
        fact_type: str = "architecture",
        now: datetime | None = None,
    ) -> None:
        self._config = config
        self._weights = weights
        self._fact_type = fact_type
        self._now = now

    def __call__(self, chunks: Sequence[CompressedChunk]) -> ContextPack:
        raws: list[RawClaim] = [_raw_from_chunk(chunk) for chunk in chunks]
        graded = grade_claims(
            raws,
            config=self._config,
            weights=self._weights,
            fact_type=self._fact_type,
            now=self._now,
        )
        return ContextPack(
            claims=raws,
            chunks=list(chunks),
            verdicts_assigned=True,
            graded_claims=graded,
        )


@dataclass(frozen=True)
class _RawWithScore(RawClaim):
    """A :class:`RawClaim` that also carries the candidate's retrieval score for normalisation."""

    retrieval_score: float = 0.0


def _raw_from_chunk(chunk: CompressedChunk) -> _RawWithScore:
    candidate = chunk.candidate
    return _RawWithScore(
        text=chunk.content,
        source_type=candidate.source_type,
        source_uri=candidate.source_uri,
        document_id=candidate.document_id,
        chunk_id=candidate.chunk_id,
        source_updated_at=candidate.source_updated_at,
        author=candidate.author,
        heading_path=candidate.heading_path,
        retrieval_score=float(candidate.rrf_score or 0.0),
    )
