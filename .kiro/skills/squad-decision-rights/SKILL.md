---
name: squad-decision-rights
description: Decision rights of the squad's CTO versus the CEO — what the CTO decides alone, what must be escalated to the CEO (thresholds read from .kiro/squad/config.env and plan-approval.md), and what the CTO never does. Used by squad-cto and the /squad orchestrator.
---

# Decision rights: CEO ↔ CTO

The CEO decides **two** things: the plan (Gate 1: which option, scope, budget, milestones) and the
go-live (Gate 2: CAB). The CTO decides everything technical in between. The CEO's Gate 1 decision is
recorded in `docs/squad/features/<feature>/2-gate1/plan-approval.md`; that file is the **baseline** every escalation rule
below is measured against.

## Thresholds
Read from `.kiro/squad/config.env`, unless `plan-approval.md` overrides them for this feature:

| key | meaning |
|---|---|
| `ESCALATE_COST_PCT` | forecast build or monthly run cost above baseline by more than this % |
| `ESCALATE_SCHEDULE_PCT` | forecast go-live date later than baseline by more than this % of the planned duration |
| `TOKEN_BUDGET_M` | token budget for the whole feature, in millions (0 = none); plan-approval.md may set a feature budget |

## The CTO decides alone
- Whether the decision brief is ready to go to the CEO (plan-review).
- Design, stack details, ADRs, task plan — inside the approved option and baseline (D1).
- Fix loops, re-plans, task re-assignment, test strategy changes.
- Promotion UAT → PRE (D2) and CAB readiness (D3), using `squad-env-promotion` + santa-method.
- Automatic production rollback on a breach of the cab-pack triggers (release does it first; the CTO acknowledges and chooses the fix route).
- Minor scope trade-offs **inside** Should/Could that keep every Must.
- Moving a feature from the lean to the full track (`squad-tracks`); never the reverse.
- Unblocking a role (`blocked` mode): re-routing, clarifying within the approved artifacts, choosing between
  options the artifacts already allow.
- Writing the retrospective and the lessons ledger (`squad-retro`).
- Accepting an S3/S4 defect as residual risk in the error ledger (`squad-errors`); never an S1/S2.

## The CTO must escalate to the CEO (decision = ESCALATE)
1. A Must-have cannot be delivered, or a new Must-have is needed, or the work drifts outside the approved option.
2. Cost forecast exceeds `ESCALATE_COST_PCT`, or schedule forecast exceeds `ESCALATE_SCHEDULE_PCT`.
3. A new paid external service, a new vendor, or data leaving the company boundary not covered by the approved option.
4. A CRITICAL or HIGH security / privacy / data-loss finding that cannot be fixed before CAB.
5. A breaking change to a contract other teams already use.
6. Santa-method reviewers still disagree after two rounds.
7. A loop ceiling in the orchestrator is exceeded.
8. A production rollback happened twice for the same release.
9. The token forecast for the feature exceeds its token budget (state.json `tokens`, history per stage).
   Options usually are: raise the budget, cut Should/Could scope, or switch to the lean track (CEO only).

An escalation always carries: the rule that fired, evidence, 2–3 options with consequences, and the CTO's
recommendation — so the CEO can answer in one line.

## The CTO never
- Approves Gate 1 or Gate 2, or writes `plan-approval.md` / `cab-approval.md`.
- Deploys to or changes production (rollback on breach is done by release under its own rule).
- Edits artifacts owned by another role, or earlier entries of `decisions.md`.
- Approves work without evidence (file, section, TC or finding IDs).
