---
name: squad-engineering-standards
description: The engineering contract every squad developer works under — the three things stated up front (Goal, Guardrails, Done), Definition of Ready before coding, Definition of Done before handoff, clean-code limits for AI-generated code, and the self-verify/self-fix loop (test until green, up to a ceiling). Read by squad-backend and squad-frontend; the limits come from config with safe defaults so no external service is required.
---

# Engineering standards — freedom inside firm boundaries

A developer gets freedom over *how* to build, in exchange for three things being fixed and verifiable:
**the Goal**, **the Guardrails**, and **what Done means**. State all three at the top of the work, then
let the developer run. This mirrors Definition of Ready / Definition of Done / TDD: hard, external checks
turn a probabilistic model into predictable output.

## 1. The three things, stated before coding
Every backend/frontend brief carries these, drawn from the existing artifacts (never invented):

**Goal — the end result.** What the user can do when this is finished, as observable acceptance criteria
(not a technical description). "When X, the user sees Y." Source: `requirements.md` ACs for the task.

**Guardrails — lines never crossed.**
- Write only under your own directory (`backend/` or `frontend/`); never touch docs,
  `api-contract.yaml`, or the other engineer's code.
- No new dependency unless the plan or an ADR names it. A dependency not in the project's manifest is a
  hallucinated import — stop, do not add it.
- Follow the patterns/stack named in `platform-baseline.md`; do not introduce a new pattern silently.
- No secrets in code; config via env, documented in `.env.example`.
- Never weaken, skip, or delete a test to go green. A wrong test is reported, not silenced.
- If the contract is wrong or insufficient, stop and report `CONTRACT_ISSUE`; never work around it.

**Done — the standard that is checked, not judged.**
- Every AC has at least one test named after it; the full suite is green (`failed=0`).
- Coverage on changed code ≥ `COVERAGE_MIN` (default 80; a project may set 90 for AI-hardened code).
- Linter, type checker and build are clean.
- Cognitive complexity of any new function ≤ `COMPLEXITY_MAX` (default 15; AI-hardened projects use 8) —
  break logic into smaller functions rather than nesting.
- Duplicated lines on new code ≤ `DUPLICATION_MAX` % (default 3; AI-hardened 1).
- Responses conform to `api-contract.yaml` (status, schema, error shape).

The limits are read from `config.env` with the defaults above, so the standard runs with no external
service. SonarQube is optional (see §4); when it is off, the linter/type-checker/coverage/complexity tools
already in the stack enforce the same intent.

## 2. Self-verify and self-fix (test until green, bounded)
Before handing off, run the verify-fix loop on your own output using the stack's own commands and
`scripts/squad/check.sh`:
1. build → run the full test suite with coverage → lint/type-check.
2. If anything is red, fix it and run again.
3. Repeat **until green**, up to `SELF_FIX_MAX` iterations (default 10). The ceiling is a stop, not a quota:
   if it is green on the second pass, stop on the second pass.
4. If it is still red after the ceiling, do **not** claim done — hand off `status: blocked` with the
   failing output so the Delivery Manager can route it (CTO). This prevents an endless fix loop burning tokens.

You verify with the **same commands** the release and go-live steps use (`check.sh`, the stack's test/build,
`scripts/squad/deploy.sh`/`smoke.sh` when a demo needs a running environment), so "green for me" and "green
at go-live" mean the same thing.

## 3. Demo and usage notes (when there is something to see)
When the task has a runnable surface (an API, a UI), include in your handoff notes how to see it work:
- the command to bring the environment up **if it is not already running** (e.g. compose up, migrate, seed);
- optionally a deploy to PRE (`scripts/squad/deploy.sh pre`) when it must be seen running for real;
- a short usage note: sample calls or steps, and what the user should expect to see.
For a purely internal task with no surface, say so and skip the demo. Keep this in the handoff; do not
invent new scripts — use the ones the project already has.

## 4. SonarQube (optional, off by default)
If `SONARQUBE_ENABLED=true` and a SonarQube MCP server is configured, the quality gate also runs through
Sonar: the reviewer/orchestrator reads the project's quality-gate status and treats a red gate as a
blocker, and a developer may iterate "scan → fix" against the Sonar rules. When `SONARQUBE_ENABLED` is
false (default), skip all Sonar steps; the §1 limits enforced by the stack's own tools are the gate.
Sonar tokens live in the user's MCP config, never in the kit.
