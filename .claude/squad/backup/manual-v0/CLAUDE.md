# Project rules — Squad workflow on top of ECC

This repo is built by a role-based agent squad. The main session acts as the
**Squad Orchestrator** (skill `/squad`); it is the only role that talks to the user,
and the only one that decides the next stage.

## Roles → agents → outputs

| Stage | Role | Agent | Output (under `docs/squad/<feature>/`) |
|---|---|---|---|
| po | PO | `squad-po` | `product-requirement.md` |
| ba | BA | `squad-ba` | `requirements.md` (FR + acceptance criteria) |
| sa | SA | `squad-sa` | `architecture.md`, `api-contract.yaml` |
| lead | Lead | `squad-lead` | `implementation-plan.md` |
| qa-plan | QA | `squad-qa` (mode: plan) | `test-plan.md`, `test-cases.md` |
| backend | Backend | `ecc:tdd-guide` (scoped to backend) | backend code + unit/integration tests |
| frontend | Frontend | `ecc:tdd-guide` (scoped to frontend) | frontend code + component tests |
| qa-verify | QA | `ecc:e2e-runner` → `squad-qa` (mode: report) | E2E tests + `regression-report.md` |
| review | Reviewer | `ecc:code-reviewer` (+ `ecc:security-reviewer`, language reviewer) | `review-report.md` |
| — | Squad Orchestrator | main session, `/squad` | `state.json`, next-stage decision |

Build breaks during backend/frontend → `ecc:build-error-resolver`.

## Hard rules

- Hand-off is **by file only**. Each role reads its upstream artifacts from disk, never the chat history.
- Every requirement, test and task carries an ID: `FR-001`, `AC-001`, `T-001`, `TC-001`. Downstream artifacts must reference upstream IDs (traceability).
- `api-contract.yaml` is the single source of truth between backend and frontend. Changing it after Gate B sends the flow back to `sa`.
- Docs roles (`squad-po/ba/sa/lead/qa`) write only inside `docs/squad/<feature>/`. Only the backend/frontend stages touch source code.
- Tests first (red → green → refactor), coverage ≥ 80% on changed code.
- Human gates: **A** after `po`, **B** after `lead` (plan + contract), **C** before commit. Never commit without Gate C.
- Artifacts are written in Vietnamese unless `state.json.language` says otherwise; code, identifiers and commit messages in English.

## Layout

```
backend/            # backend source + tests (stack chosen at stage sa)
frontend/           # frontend source + tests
e2e/                # end-to-end tests
docs/squad/<feature>/
  state.json        # orchestrator state machine
  product-requirement.md, requirements.md, architecture.md, api-contract.yaml,
  implementation-plan.md, test-plan.md, test-cases.md,
  regression-report.md, review-report.md
```
