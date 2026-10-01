---
name: squad-env-promotion
description: Entry and exit criteria for each environment in the squad release path (dev → UAT → PRE → CAB → PROD → watch), used by squad-cto to promote and by squad-release/squad-qa to know what evidence is required.
---

# Environment promotion criteria

Every criterion must be backed by evidence in the feature folder. "Probably fine" is a fail.

| Step | Exit criteria (all required) | Evidence |
|---|---|---|
| **dev → checkpoint commit** | all BE/FE tests green; coverage ≥ 80% on changed code; review verdict APPROVE (no open CRITICAL/HIGH) | regression-report-dev.md, review-report.md |
| **→ UAT deploy** | dev checkpoint done; migrations are reversible | review-report.md, release-log.md |
| **UAT exit → PRE (D2)** | smoke on UAT passes; every P1 TC passes on UAT; no open CRITICAL/HIGH; no open S1/S2 in errors.md; no unexplained flaky TCs; data migrations applied cleanly | regression-report-uat.md, release-log.md |
| **PRE exit → CAB (D3)** | smoke on PRE passes; every TC passes on PRE; every NFR threshold met with measured values; security review has no open CRITICAL/HIGH; production audit has no blocker; **rollback rehearsed on PRE with measured time-to-rollback**; every rollback trigger maps to an SLI the watch can read (`squad-observability`), with a sample read-out from PRE; **no open S1/S2 in errors.md**, every open S3/S4 accepted by the CTO | regression-report-pre.md, review-report.md, release-log.md |
| **CAB → PROD** | CEO approved Gate 2 (`cab-approval.md` exists); deployment window respected | cab-approval.md |
| **PROD watch → done** | smoke on PROD passes and no rollback trigger fires during the observation window in cab-pack.md | release-log.md |

With `ENVIRONMENTS=pre,prod` (no UAT) the UAT rows are skipped: the dev checkpoint leads straight to the PRE
deploy (its entry criteria are those of "→ UAT deploy"), and the PRE exit must also cover the UAT exit criteria
(every P1 TC, migrations applied cleanly). D2 does not exist; D3 keeps two checkers.

## Rollback triggers (defaults; cab-pack.md may tighten them)
- smoke test fails on PROD;
- error rate above baseline × 2 for 5 minutes, or any health check failing for 3 consecutive checks;
- a P1 user journey fails in canary checks.
On a trigger: rollback immediately (no approval needed), verify with smoke, notify, then the CTO records an `incident` decision and a retro with postmortem (`squad-retro`). A new Gate 2 is required before the next production attempt.

## Evidence freshness
Evidence counts only for the commit being promoted: regression reports and smoke results must name the
same version as the release-log entry for that environment. A new commit after a report → re-run it.
