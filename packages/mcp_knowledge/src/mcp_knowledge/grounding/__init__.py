"""CHG-001 E8 (T-106..T-108) — the deterministic grounding verdict gate (choke point #2).

The grounding gate is the single server-side place a claim gets exactly one verdict
(``FACT``/``LOW_CONFIDENCE``/``UNKNOWN``/``CONFLICT``), run **after** the permission choke point
(#1) and after rerank + context-compression, as the last step before the context-pack leaves the
server
(ADR-0018 §2, L-001). Two pieces, kept separate from the pipeline and the permission filter:

* :mod:`mcp_knowledge.grounding.confidence` — the deterministic evidence-strength
  ``retrieval^w_r · agreement^w_a · freshness^w_f`` (ADR-0018 D1) and the τ constants that stay
  **TBD** until a real golden-set calibrates them (NFR-010, L-002).
* :mod:`mcp_knowledge.grounding.verdict` — the gate that grades raw claims deterministically and
  enforces the threshold-independent invariants (no-evidence ⇒ UNKNOWN; confidence never rescues a
  source-less claim; ≥2 conflicting sources ⇒ CONFLICT).
"""

from __future__ import annotations

from mcp_knowledge.grounding.confidence import (
    DEFAULT_WEIGHTS,
    TAU_FACT,
    TAU_LOW,
    ConfidenceWeights,
    agreement_factor,
    compute_confidence,
    normalise_retrieval,
)
from mcp_knowledge.grounding.eval_harness import EvalCase, EvalReport, run_eval
from mcp_knowledge.grounding.telemetry import GroundingTrace, trace_from_result
from mcp_knowledge.grounding.verdict import GroundingVerdictGate, grade_claims

__all__ = [
    "DEFAULT_WEIGHTS",
    "TAU_FACT",
    "TAU_LOW",
    "ConfidenceWeights",
    "EvalCase",
    "EvalReport",
    "GroundingTrace",
    "GroundingVerdictGate",
    "agreement_factor",
    "compute_confidence",
    "grade_claims",
    "normalise_retrieval",
    "run_eval",
    "trace_from_result",
]
