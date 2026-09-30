<!-- squad:begin -->
# Squad workflow on top of ECC (managed by squad-init v1.1.0)

This repo is built by a role-based agent squad. The main session acts as the
**Squad Orchestrator** (skill `/squad`); it is the only role that talks to the user,
and the only one that decides the next stage.

```
PO → BA → SA → Lead → QA plan → Backend ∥ Frontend → QA verify → Reviewer → commit
```

## Roles → agents → outputs

| Stage | Role | Agent | ECC used inside | Output (under `docs/squad/<feature>/`) |
|---|---|---|---|---|
| po | PO | `squad-po` | skill product-lens | `product-requirement.md` |
| ba | BA | `squad-ba` | skill intent-driven-development | `requirements.md` (FR + acceptance criteria) |
| sa | SA | `squad-sa` | skills contract-first, api-design, architecture-decision-records; agent architect | `architecture.md`, `api-contract.yaml`, `docs/adr/` |
| lead | Lead | `squad-lead` | agent code-architect | `implementation-plan.md` |
| qa-plan | QA | `squad-qa` (mode plan) | skill e2e-testing | `test-plan.md`, `test-cases.md` |
| backend | Backend | `squad-backend` | skills tdd-workflow, backend-patterns; agent build-error-resolver | code + unit/integration tests in `backend/` |
| frontend | Frontend | `squad-frontend` | skills tdd-workflow, frontend-patterns; agent build-error-resolver | code + component tests in `frontend/` |
| qa-verify | QA | `squad-qa` (mode verify) | agent e2e-runner | E2E in `e2e/`, `regression-report.md` |
| review | Reviewer | `squad-reviewer` | agents code-reviewer, security-reviewer, language/database reviewers | `review-report.md` |
| — | Squad Orchestrator | main session, `/squad` | — | `state.json`, next-stage decision |

## Hard rules

- Hand-off is **by file only**. Each role reads its upstream artifacts from disk, never the chat history.
- Every requirement, test and task carries an ID: `FR-001`, `AC-001`, `T-001`, `TC-001`. Downstream artifacts must reference upstream IDs (traceability).
- `api-contract.yaml` is the single source of truth between backend and frontend. Changing it after Gate B sends the flow back to `sa`.
- Docs roles write only inside `docs/squad/<feature>/` (SA also `docs/adr/`). Only `squad-backend` / `squad-frontend` touch source code, each in its own directory.
- Tests first (red → green → refactor), coverage ≥ 80% on changed code.
- Human gates: **A** after `po`, **B** after `lead` (plan + contract), **C** before commit. Never commit without Gate C.
- Artifacts are written in Vietnamese unless `state.json.language` says otherwise; code, identifiers and commit messages in English.

## Layout

```
backend/            # backend source + tests (stack chosen at stage sa)
frontend/           # frontend source + tests
e2e/                # end-to-end tests
docs/adr/                   # architecture decision records
docs/squad/<feature>/
  state.json        # orchestrator state machine
  product-requirement.md, requirements.md, architecture.md, api-contract.yaml,
  implementation-plan.md, test-plan.md, test-cases.md,
  regression-report.md, review-report.md
```
<!-- squad:end -->

## Project-specific layout override (mcp-data-platform, Gate B — 2026-10-01)

This repo has **no frontend**. Per Gate B decision on feature `mcp-data-platform`, source
code lives under `packages/` (a uv workspace of independent Python packages: one per MCP
server — `mcp_common`, `mcp_confluence`, `mcp_gitlab`, `mcp_opensearch`, `mcp_kibana`,
`mcp_cloudwatch`, `mcp_kafka`, `mcp_redis`, `mcp_sqs_sns`, `mcp_pgvector`, `mcp_ingest` —
per `docs/squad/mcp-data-platform/architecture.md`), **not** under `backend/` as the
generic Layout section above assumes. `squad-backend` writes under `packages/`, `infra/`,
`scripts/`, `ci/`, and (per implementation-plan.md's Q-L4) also `docs/spikes/`,
`docs/signoff/`, `eval/`. There is no `frontend/` stage for this feature.
