---
name: squad-tracks
description: Right-sizing for a one-person company — how the /squad orchestrator picks the delivery track (skip the squad, lean, full) from the goal, what each track changes in the stage map, and the safety invariants no track may relax. Used by the /squad orchestrator at intake, by squad-cto when it re-sizes, and by roles whose depth depends on state.json.tier.
---

# Delivery tracks

The CEO's time and the token budget are the scarce resources. Run only as much process as the risk needs.
`state.json.tier` records the track: `standard` = **lean**, `large` = **full**.

## 1. Choosing the track (at intake; first match wins)
| Signal in the goal / repo | Track |
|---|---|
| One file or one small fix, no contract change | **no squad** → `/ecc:orch-fix-defect` or `/ecc:orch-add-feature` |
| New product or system; a new vendor, paid service or MCP/API from outside; data leaving the company; auth, payments, PII or other security-sensitive surface; a breaking change to a shared contract; expected > ~2 weeks of agent work | **full** (`large`) |
| Everything else: a feature inside an existing product and stack, no new vendor, no data-boundary change | **lean** (`standard`) |
When unsure, pick **full**. Write the reason in the intake `history` note; the CEO sees it at Gate 1.

## 2. What lean changes
| Stage | full | lean |
|---|---|---|
| A2 research | full market & technology research | `squad-researcher` mode `light`: internal assets + libraries/OSS via `ecc:search-first`; ≤ 5 sources; no vendor pricing survey unless a Must needs it |
| A3 options | ≥ 3 viable options + defer | ≥ 2 viable options (one reuses/extends what exists) + defer, sketch level |
| A4 brief | ≤ 2 pages | ≤ 1 page (TL;DR, options table, recommendation, plan, decisions needed) |
| B12 D2 (UAT → PRE) | santa-method, 2 independent checkers | 1 independent checker + the CTO's own reading of the evidence |
| B15 D3 (PRE → CAB) | santa-method, 2 checkers | **unchanged: 2 checkers** |
| Models | each role's own model | `squad-lead` and `squad-reviewer` run on `sonnet` (the orchestrator passes it); CTO and checkers unchanged |
| C6 retro | full retro | short retro (≤ 10 lines) — still mandatory |
Everything else runs the same.

Environments are independent of the track: `ENVIRONMENTS=pre,prod` (one staging-like environment) skips the
UAT stages on both tracks; PRE then carries the UAT duties.

## 3. Invariants — no track relaxes these
- The two CEO gates, the verbatim approval files, and the production guard.
- Tests first, coverage ≥ 80 % on changed code, review with no open CRITICAL/HIGH.
- Deploys to every configured pre-production environment through `scripts/squad/*.sh`, rollback rehearsal
  on PRE, CAB pack.
- Every decision in `decisions.md` with evidence; loop ceilings; escalation rules.

## 4. Changing the track
- **Up** (lean → full): the CTO may do it at any time when a full-track signal appears (e.g. a vendor is
  needed). Record it as a decision; the orchestrator sets `tier: large` and re-runs any skipped depth
  (e.g. full research) before the next CTO decision.
- **Down** (full → lean): only the CEO, at Gate 1 or in answer to an escalation.
