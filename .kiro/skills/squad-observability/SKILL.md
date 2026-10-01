---
name: squad-observability
description: Minimum observability standard a squad feature must ship with so production can be watched and auto-rolled back — health endpoint, structured logs, the SLIs behind the rollback triggers, alerts and where they are read. Used by squad-sa (architecture.md "Observability"), squad-backend/squad-frontend (implementation), squad-release (smoke and watch) and squad-cto (D3).
---

# Observability standard

Auto-rollback is only as good as the signals behind it. If a signal is not designed, built and verified
on PRE, the release cannot be watched — and D3 must not pass.

## 1. What SA designs (architecture.md → `## Observability`)
| Item | Minimum |
|---|---|
| Health | `GET /health` (liveness, no dependencies) and `GET /ready` (checks DB/queues/external deps); both cheap and unauthenticated or on an internal port. Non-HTTP: an equivalent CLI/heartbeat check |
| SLIs | For each P1 user journey: request rate, error rate (5xx or failed operations / total), latency p95. Name the metric or the log query that computes each |
| Baseline | How the "error rate above baseline" in the rollback triggers is measured (source, window) |
| Logs | Structured (JSON) with `timestamp, level, request_id / correlation_id, operation, outcome, duration_ms`; no secrets, tokens or PII |
| Alerts | Which SLI breach alerts, threshold, where it is read (tool, dashboard URL, or command) |
| Access for the squad | The exact command or URL `smoke.sh` and `squad-release` (watch) use to read health and SLIs per environment |
If the project has no monitoring stack, the fallback is: health endpoints + structured logs + a script
command that computes error rate and p95 from logs over the last N minutes. Say so explicitly.

## 2. What engineers build
- Backend: the health/ready endpoints, structured logging with a correlation id propagated from the
  request, metrics or log fields for every SLI, error logging at the boundary (never swallow errors).
- Frontend: report uncaught errors and failed API calls with the correlation id (to the existing error
  tracker, or to the console in dev); no PII in reported payloads.
- Tests assert that health/ready respond and that an error path logs `outcome=error` with the id.

## 3. What release verifies
- `smoke.sh <env>` checks health, ready and 2–3 P1 journeys.
- On PRE, record once in release-log.md: the SLI read-out command and a sample of its output
  (proves the watch can see production-like signals).
- On PROD watch: read SLIs at the start (baseline), then at least every 5 minutes during the observation
  window; compare with the rollback triggers in `squad-env-promotion` / cab-pack.md.

## 4. D3 check (CTO)
READY_FOR_CAB requires: health + ready verified on PRE, every rollback trigger mapped to a readable SLI,
and the sample read-out present in release-log.md.
