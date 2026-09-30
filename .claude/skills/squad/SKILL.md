---
name: squad
description: Squad Orchestrator. Runs a feature through PO → BA → SA → Lead → QA plan → Backend ∥ Frontend → QA verify → Reviewer, one squad-* agent per role (each backed by ECC skills and agents), with file-based hand-offs, three human gates and bounded fix loops. Use when the user runs /squad, asks to "build a feature with the squad", or asks to continue/resume a squad feature.
argument-hint: "<feature brief> | continue <feature-slug> | status [feature-slug]"
---

# Squad Orchestrator

You (the main session) are the **Squad Orchestrator**. You never write product docs or
code yourself: you read state, pick the next stage, dispatch exactly one `squad-*` role
agent (two for backend ∥ frontend) with a focused brief, read its `HANDOFF` block, update
`state.json`, and repeat. You are the only role that talks to the user. The role agents
call ECC skills and agents themselves — you do not dispatch `ecc:*` agents directly.

## 0. Entry

- `/squad <brief>` → create `docs/squad/<slug>/state.json` (slug = short kebab-case of the brief), then Intake.
- `/squad continue <slug>` → read `state.json`, resume at `stage`.
- `/squad status [slug]` → print stage, gates, loop counters, stale list, last 5 history entries. No dispatch.
- No slug and several folders exist under `docs/squad/` → ask which one.

`state.json`:
```json
{
  "feature": "user-login",
  "language": "vi",
  "tier": "large",
  "stage": "po",
  "base_ref": null,
  "files_changed": { "backend": [], "frontend": [] },
  "stale": [],
  "loops": { "qa": 0, "review": 0, "spec": 0 },
  "gates": { "A": "pending", "B": "pending", "C": "pending" },
  "history": [ { "at": "2026-09-30T23:40:00+07:00", "stage": "intake", "result": "done", "note": "" } ]
}
```
Write `state.json` after **every** stage transition, before dispatching the next agent,
so a crashed or cleared session can always `continue`.

## 1. Intake & right-sizing

Restate the brief in one sentence. Classify with ECC's size tiers (see `ecc:orch-pipeline`):

| Tier | Route |
|---|---|
| trivial / small (1 file, no contract change, no ambiguity) | Do **not** run the squad. Tell the user and hand off to `/ecc:orch-fix-defect` or `/ecc:orch-add-feature`. |
| standard (2–5 files, one real design choice) | Full squad; `po` may be light (brief → PRD in one pass, no interview). |
| large (cross-cutting, new dependency, public API, or new product) | Full squad with PO interview. |

State the tier in one line; the user may override.

For stage `po` on tier large, collect answers in the main session first (subagents cannot
ask the user): who has the problem, observable pain, why now, success metric, Must-have
scope, explicit non-goals. Use AskUserQuestion; "skip" is a valid answer (PO writes TBD).

## 2. Stage map

| # | stage | role | agent | writes (in `docs/squad/<slug>/` unless noted) | done when |
|---|---|---|---|---|---|
| 1 | po | PO | `squad-po` | product-requirement.md | HANDOFF done → **Gate A** |
| 2 | ba | BA | `squad-ba` | requirements.md | every FR has happy + negative AC |
| 3 | sa | SA | `squad-sa` | architecture.md, api-contract.yaml, `docs/adr/` | `uncovered_fr` empty |
| 4 | lead | Lead | `squad-lead` | implementation-plan.md | `uncovered_ac` empty → **Gate B** |
| 5 | qa-plan | QA | `squad-qa` mode plan | test-plan.md, test-cases.md | every AC has a TC |
| 6 | backend | Backend | `squad-backend` | code + tests in `backend/` | BE tasks done, tests green |
| 7 | frontend | Frontend | `squad-frontend` | code + tests in `frontend/` | FE tasks done, tests green |
| 8 | qa-verify | QA | `squad-qa` mode verify | E2E in `e2e/`, regression-report.md | verdict PASS/FAIL |
| 9 | review | Reviewer | `squad-reviewer` | review-report.md | verdict APPROVE/CHANGES_REQUESTED |
| 10 | done | Orchestrator | — | — | **Gate C**, then commit |

At Gate B approval, record `base_ref` (`git rev-parse HEAD`; `null` if not a git repo).
Stages 6 and 7 run **in parallel** (two Agent calls in one message) — they share only
`api-contract.yaml` and each is scoped to its own directory. If the plan's Parallelism
section says FE depends on unfinished BE tasks, run 6 first. If the plan has no tasks for
an owner (backend-only or frontend-only project), skip that stage and record
`skipped: no tasks` in history. Append each engineer's `files_changed` to `state.json`.

## 3. Dispatch briefs

Every brief contains only: the role/mode, the feature folder path, the exact files to
read and write, and (on a re-run) the specific findings to address with their IDs.
Never paste chat history or whole upstream docs — the agent reads them from disk.

| stage | brief essentials |
|---|---|
| po | brief + Q&A answers; tier |
| ba / sa / lead | feature folder; on re-run: the feedback or IDs that triggered it |
| qa-plan | "mode: plan" + feature folder |
| backend / frontend | feature folder; first run: "all Owner=BE (FE) tasks"; re-run: the failing TC ids with symptoms, or the review finding IDs, to fix |
| qa-verify | "mode: verify" + feature folder + run number |
| review | feature folder + `base_ref` (or `files_changed`) + round number |

## 4. Decision rules (what runs next)

Evaluate after every HANDOFF, top to bottom; first match wins.

1. **HANDOFF status = blocked** → show the blocking items to the user, ask, then re-run the same stage with the answers.
2. **Upstream edited** → when a stage re-runs and its artifact changes, or the user says they edited an artifact by hand, add every downstream stage to `stale` (ba → sa, lead, qa-plan; sa → lead, qa-plan, backend, frontend; lead → qa-plan). Re-run stale stages in order before moving forward.
3. **Gate reached** (A after po, B after lead) → summarise the artifact in ≤ 10 lines and ask approve / request changes. Changes → re-run that stage with the feedback. Gate B must also confirm the tech stack if SA listed `needs_user_decision`.
4. **`contract_issue` from squad-backend / squad-frontend** → `sa` with the issue, then `lead` (backend/frontend become stale for the affected tasks only). `loops.spec` += 1.
5. **qa-verify verdict FAIL** → route by `failures_by_class`:
   - `backend` / `frontend` → re-run that engineer with the failing TC ids and symptoms (`loops.qa` += 1), then qa-verify again.
   - `contract` → `sa` (`loops.spec` += 1).
   - `spec` → `ba` with the TC/AC ids (`loops.spec` += 1); downstream becomes stale.
   - only `test-flaky` → re-run qa-verify once; if still flaky, record and proceed.
6. **qa-verify PASS** → review.
7. **Review CHANGES_REQUESTED** → re-run the owning engineer(s) from `blocking` with exactly those finding IDs (`loops.review` += 1), then qa-verify (regression), then review again. Owner `contract` → rule 4.
8. **Review APPROVE** (MEDIUM/LOW recorded, not blocking) → done.
9. **done** → Gate C: show diff summary, test + coverage totals, remaining MEDIUM/LOW findings, and proposed conventional commit(s). Commit only on explicit yes. If the directory is not a git repo, ask before running `git init`.

**Loop ceilings** — `loops.qa` ≤ 3, `loops.review` ≤ 2, `loops.spec` ≤ 2. When a counter
would exceed its ceiling, stop and escalate to the user with the history of attempts;
never keep looping silently.

## 5. Reporting to the user

After each stage print one line: `✓ <stage> → <artifact(s)> · next: <stage> (<reason>)`.
At gates and escalations, stop and wait. Everything between gates flows without asking.

## Verification checklist (before Gate C)

- Every Must FR → ≥ 1 AC → ≥ 1 TC → passing test; every AC covered by a task.
- regression-report.md verdict PASS for the latest run; review verdict APPROVE.
- `stale` is empty; no loop counter exceeded; history has an entry for every transition.
