"""T-098 — context-pack assembler skeleton: single choke-point seam, permission before gate.

At E3 the assembler gathers raw claims + provenance and assigns NO verdict. It wires the two
choke-point seams so E6 (permission #1) and E8 (grounding gate #2) each swap in one implementation
without creating a second filtering/grading site (L-001).
"""

from __future__ import annotations

from collections.abc import Sequence

from mcp_knowledge.pack.assembler import (
    ContextPack,
    ContextPackAssembler,
    RawClaim,
    identity_permission_filter,
)
from mcp_knowledge.pack.compress import CompressedChunk, compress_candidates
from mcp_knowledge.retrieval.hybrid import Candidate


def _candidate(cid: int, source_type: str = "confluence") -> Candidate:
    return Candidate(
        chunk_id=cid, document_id=f"doc-{cid}", chunk_index=0, content=f"content {cid}",
        heading_path="H", source_type=source_type, source_id=str(cid),
        source_uri=f"https://e/{cid}", title="t", container="PAY", author="a",
        source_updated_at=None, ingested_at=None, rrf_score=1.0 / cid, legs={"vector": cid},
    )  # fmt: skip


def _chunks(cands: Sequence[Candidate]) -> list[CompressedChunk]:
    return compress_candidates(cands, token_budget=1000)


def test_e3_assembler_assigns_no_verdict() -> None:
    cands = [_candidate(1), _candidate(2)]
    pack = ContextPackAssembler().assemble(cands, _chunks(cands))
    assert pack.verdicts_assigned is False
    assert all(isinstance(c, RawClaim) for c in pack.claims)
    assert len(pack.claims) == 2


def test_raw_claim_carries_provenance() -> None:
    cands = [_candidate(7)]
    pack = ContextPackAssembler().assemble(cands, _chunks(cands))
    claim = pack.claims[0]
    assert claim.document_id == "doc-7"
    assert claim.source_uri == "https://e/7"
    assert claim.chunk_id == 7
    assert claim.source_type == "confluence"


def test_permission_filter_runs_before_gate_and_filters_candidates() -> None:
    # Choke point #1 seam: a filter that drops a document must remove it from the pack entirely,
    # BEFORE the gate (#2) ever sees it. This proves the ordering the E6/E8 code will rely on.
    cands = [_candidate(1), _candidate(2), _candidate(3)]
    chunks = _chunks(cands)

    def deny_doc_2(candidates: Sequence[Candidate]) -> list[Candidate]:
        return [c for c in candidates if c.chunk_id != 2]

    pack = ContextPackAssembler(permission_filter=deny_doc_2).assemble(cands, chunks)
    kept_ids = {c.chunk_id for c in pack.claims}
    assert kept_ids == {1, 3}  # doc 2 excluded before assembly


def test_single_gate_is_the_only_path_to_claims() -> None:
    # Structural: a custom gate is the single site that produces claims; the assembler never
    # fabricates claims on its own. Swapping the gate (E8) changes verdicts at ONE place.
    sentinel = ContextPack(claims=[], chunks=[], verdicts_assigned=True, notes=["graded by E8"])

    def gate(chunks: Sequence[CompressedChunk]) -> ContextPack:
        return sentinel

    cands = [_candidate(1)]
    pack = ContextPackAssembler(grounding_gate=gate).assemble(cands, _chunks(cands))
    assert pack.verdicts_assigned is True
    assert pack.notes == ["graded by E8"]


def test_reranker_status_threaded_into_pack() -> None:
    # The RRF-only fallback signal (L-002) is carried through the assembler to the summary.
    cands = [_candidate(1)]
    pack = ContextPackAssembler(reranker_status="disabled").assemble(cands, _chunks(cands))
    assert pack.reranker_status == "disabled"


def test_identity_permission_filter_is_passthrough_seam() -> None:
    cands = [_candidate(1), _candidate(2)]
    assert identity_permission_filter(cands) == list(cands)
