"""Grounding eval harness (T-108) — runs the gate over a question set, reports counts.

The harness drives the deterministic grounding gate over a set of "Company Questions" and reports,
per question, the verdict the gate produced and the deterministic confidence. It runs **now** with a
**fake, deterministic provider** (fixed candidates per question), so the whole evaluation pipeline
is exercised and testable without HF egress.

## What is measured now vs. what is TBD
Measurable now (deterministic, no egress): the **verdict distribution** (how many FACT / UNKNOWN /
CONFLICT / LOW_CONFIDENCE) and the per-question confidence — i.e. that the gate behaves as the
contract says on known inputs.

NOT claimed here — **recall/precision and the FACT↔LOW_CONFIDENCE threshold τ**:

    # THRESHOLD TBD (NFR-010, L-002) — chốt khi gỡ egress HF chạy golden-set thật

These are meaningful only on a **real** company golden-set with **measured** embedding/rerank, which
is blocked by HF egress (NFR-003/NFR-010 UNVERIFIED, L-002/E-004). The harness is written so that,
once egress is cleared (B9/DK1), the same ``run_eval`` runs the real golden-set and calibrates τ; it
never invents a recall number or a τ while egress is blocked (``calibration_status=uncalibrated``).

The 100-question set lives in ``questions.yaml`` next to this module; the shipped file is a small,
labelled **skeleton** (a handful of representative cases) ready to grow to the full golden-set.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mcp_common.envelope import GroundingVerdict

from mcp_knowledge.grounding.verdict import GroundingVerdictGate
from mcp_knowledge.liveness.authority import SourceAuthorityConfig
from mcp_knowledge.pack.compress import compress_candidates
from mcp_knowledge.retrieval.hybrid import Candidate

QUESTIONS_PATH = Path(__file__).with_name("questions.yaml")

# THRESHOLD TBD (NFR-010, L-002) — chốt khi gỡ egress HF chạy golden-set thật
#: τ and recall/precision are only meaningful on a measured golden-set (blocked by HF egress). The
#: harness therefore reports verdict counts, never a recall number, until egress is cleared.
CALIBRATION_STATUS = "uncalibrated"


@dataclass(frozen=True)
class EvalCase:
    """One eval question + the fake candidates that stand in for retrieval (deterministic)."""

    id: str
    question: str
    expect: str  # the verdict family the gate should produce on these candidates (documentation)
    candidates: list[Candidate] = field(default_factory=list)
    fact_type: str = "architecture"


@dataclass(frozen=True)
class EvalReport:
    """The aggregate result of a harness run: verdict counts + per-case verdicts.

    ``recall`` is deliberately ``None`` (UNVERIFIED until a measured golden-set runs — see module
    docstring). ``calibration_status`` stays ``uncalibrated`` so the report is honest about τ.
    """

    cases: int
    verdict_counts: dict[str, int]
    per_case: list[dict[str, Any]]
    calibration_status: str = CALIBRATION_STATUS
    recall: float | None = None  # TBD — not claimed until HF egress clears (NFR-003/NFR-010)


def run_eval(
    cases: list[EvalCase],
    *,
    config: SourceAuthorityConfig | None = None,
    now: datetime | None = None,
) -> EvalReport:
    """Run the grounding gate over ``cases`` and aggregate the verdicts (pure, deterministic).

    ``now`` is injectable so freshness-dependent grading is reproducible. The report counts
    verdicts and records each case's verdict; it does **not** compute recall or set τ (both TBD
    until the real golden-set runs after HF egress).
    """
    now = now or datetime(2026, 10, 2, tzinfo=UTC)
    counts: Counter[str] = Counter()
    per_case: list[dict[str, Any]] = []
    for case in cases:
        gate = GroundingVerdictGate(config=config, fact_type=case.fact_type, now=now)
        pack = gate(compress_candidates(case.candidates))
        verdicts = [c.grounding for c in pack.graded_claims] or [GroundingVerdict.UNKNOWN]
        for verdict in verdicts:
            counts[verdict.value] += 1
        top = verdicts[0].value
        per_case.append(
            {
                "id": case.id,
                "question": case.question,
                "expect": case.expect,
                "verdict": top,
                "confidence": next(
                    (c.confidence for c in pack.graded_claims if c.confidence is not None),
                    None,
                ),
            }
        )
    return EvalReport(
        cases=len(cases),
        verdict_counts=dict(counts),
        per_case=per_case,
    )


def load_questions(path: Path | None = None) -> list[dict[str, Any]]:
    """Load the (skeleton) question set from ``questions.yaml``.

    Uses a tiny, dependency-free YAML subset parser (``- key: value`` blocks) so the harness adds no
    new dependency (ADR-0018 §7 "no new dependency"). The shipped file is a labelled skeleton ready
    to grow to the full 100-question golden-set once HF egress is cleared.
    """
    path = path or QUESTIONS_PATH
    if not path.exists():
        return []
    return _parse_simple_yaml(path.read_text(encoding="utf-8"))


def _parse_simple_yaml(text: str) -> list[dict[str, Any]]:
    """Parse a minimal ``- key: value`` list-of-maps. No external YAML dependency."""
    items: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        stripped = line.lstrip()
        if stripped.startswith("- "):
            if current is not None:
                items.append(current)
            current = {}
            stripped = stripped[2:]
        if current is None:
            continue
        if ":" in stripped:
            key, _, value = stripped.partition(":")
            current[key.strip()] = value.strip().strip('"')
    if current is not None:
        items.append(current)
    return items
