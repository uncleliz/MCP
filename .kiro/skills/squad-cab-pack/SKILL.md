---
name: squad-cab-pack
description: Standard for the squad's CAB (Change Advisory Board) request — the go-live change record the CEO approves at Gate 2, modelled on ITIL change management. Used by squad-release to write cab-pack.md and by squad-cto to judge CAB readiness.
---

# CAB pack (`docs/squad/features/<feature>/7-release/cab-pack.md`)

One page the CEO can approve in five minutes; every claim links to evidence.

```markdown
# <Feature> — Change Request for Go-live
| Field | Value |
|---|---|
| Change ID | CHG-<feature>-<yyyymmdd> |
| Version | <tag / commit> |
| Type | normal / standard / emergency |
| Risk rating | low / medium / high  (from the matrix below) |
| Proposed window | <date, time, timezone, duration> |
| CTO recommendation | READY_FOR_CAB — decisions.md D-<nnn> |

## 1. What changes and why       — 5 lines, business value, link plan-approval.md
## 2. Scope                       — FR ids delivered; anything deferred
## 3. Evidence
- UAT: regression-report-uat.md — <pass/total>   (or "n/a — ENVIRONMENTS=pre,prod")
- PRE: regression-report-pre.md — <pass/total>, NFR measured values
- Review: review-report.md — open findings by severity
- Defects: errors.md — <found> found, <closed> closed; open: none S1/S2, S3/S4 listed under Residual risks
- Production audit: <result>
## 4. Impact                      — users/systems affected, downtime (expected 0?), data changes
## 5. Deployment plan             — ordered steps (scripts/squad/deploy.sh prod or pipeline job), who/what runs them, duration
## 6. Rollback plan               — triggers, steps (scripts/squad/rollback.sh prod), rehearsed on PRE: <time-to-rollback>, data rollback method
## 7. Post-go-live verification   — smoke + canary checks, observation window (default 30 min), success criteria, the SLI read-out command (`squad-observability`)
## 8. Communication               — who is notified before/after, channels
## 9. Residual risks              — accepted MEDIUM/LOW findings and every open E-id (S3/S4, accepted by the CTO) with why
## 9a. Baseline check             — scope, cost and schedule vs plan-approval.md (delta %), CTO decisions that changed them
## 10. CEO decision               — filled by the orchestrator in cab-approval.md, not here
```

## Risk matrix
| | low impact | medium impact | high impact |
|---|---|---|---|
| **low likelihood** | low | low | medium |
| **medium likelihood** | low | medium | high |
| **high likelihood** | medium | high | high |
A **high** rating needs an explicit mitigation per risk and a shorter observation window trigger set.

## Readiness rule
Do not mark the pack ready unless every PRE exit criterion in `squad-env-promotion` holds.
