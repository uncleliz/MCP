---
name: squad-errors
description: The squad's error ledger — every defect that escaped the stage that made it is recorded in docs/squad/features/<feature>/records/errors.md (append-only) by the role that found it, root-caused by the role that fixed it, and closed by the role that verified it; scripts/squad/errors.sh turns all features' ledgers into patterns to avoid. Used by QA, Reviewer, Release, CTO (open), BE/FE/SA/BA/QA (fix), QA/Reviewer/Release (verify), every role before starting, and the retro.
---

# Error ledger

`knowledge/lessons.md` keeps the few rules worth remembering; the error ledger keeps **every defect**, so the squad can
count, find recurring patterns, check that each one was really fixed and prevented, and stop repeating them.

## 1. What goes in (and what does not)
Record a defect when work that a stage had **handed off as done** turns out to be wrong:
- a test case fails in QA verify (class backend | frontend | contract | spec — one entry per root symptom, not per TC);
- the reviewer reports a CRITICAL or HIGH finding;
- a smoke test fails, a deploy breaks an environment, a production rollback happens;
- the CTO RETURNs approved work for a defect (design, plan, contract) or a CEO-baselined estimate is broken by > threshold.
Not recorded: Definition-of-Done failures caught before hand-off, flaky tests without a product cause, style nits.
**Recurrence first:** before opening, run `scripts/squad/errors.sh list --category <cat>` (or Grep
`docs/squad/features/*/records/errors.md`) — if the same mistake happened before, set `Recurrence of:` to that id.

## 2. File and records — `docs/squad/features/<feature>/records/errors.md`
Append-only (a hook refuses edits to earlier text). Create it with `# <Feature> — Error ledger` if missing.
Ids: `E-<feature>-<nnn>` (`scripts/squad/errors.sh next <feature>`). Three record types, each written by the
role that knows the facts:

**Open** — by the finder (QA, Reviewer, Release, CTO):
```markdown
## E-<feature>-<nnn> · <S1|S2|S3|S4> · <YYYY-MM-DD>
- Category: requirement | design | contract | code | test | data | security | config | deploy | estimate | process
- Found: <stage, e.g. qa-uat> · by: <role>
- Introduced: <stage that made the mistake, e.g. backend, sa, ba>
- Escaped: <earlier stages that should have caught it, comma-separated, or none>
- Symptom: <observable, one line, with numbers>
- Evidence: <TC/R/D ids, report or log path>
- Owner of fix: <role>
- Recurrence of: <E-id | none>
```
**Fix** — by the role that fixed it (backend, frontend, sa, ba, qa for a test defect):
```markdown
### E-<feature>-<nnn> · fix · <YYYY-MM-DD>
- By: <role>
- Root cause: <why it was introduced AND why the escaped stages missed it — blameless, process not person>
- Fix: <commit, files or artifact ids>
- Prevention: <the regression test (name with the E-id), check, rule or template change that stops a repeat>
```
**Verified** — by the role that proved the fix (QA re-run, Reviewer next round, Release smoke) — or
**accepted** by the CTO for a residual S3/S4 it decides to ship with (cite the decision):
```markdown
### E-<feature>-<nnn> · verified · <YYYY-MM-DD>
- By: <role>
- Evidence: <report/run and TC ids that now pass>
```
```markdown
### E-<feature>-<nnn> · accepted · <YYYY-MM-DD>
- By: squad-cto
- Reason: <why shipping with it is acceptable> (D-<nnn>)
```
A defect is **closed** when it has a verified or accepted record; **fixed** (fix record only) still counts as open.

## 3. Severity
| | meaning | gate effect |
|---|---|---|
| S1 | data loss, security breach, production down or rolled back | must be closed before CAB; retro root-causes it |
| S2 | a Must AC broken, a P1 journey fails, a contract break | must be closed before CAB; retro root-causes it |
| S3 | Should/Could AC broken, workaround exists | may ship only if accepted by the CTO; listed in cab-pack residual risks |
| S4 | cosmetic or minor | same as S3 |

## 4. Rules
- One entry per root symptom; several TCs failing for one cause share one entry (list them in Evidence).
- A fix without `Prevention` is not done: a regression test named `E-<id> …` is the default prevention.
- Escaped stages are honest: if review or QA dev should have caught it, say so — that is how the process improves.
- Never edit or delete a record; a wrong record is corrected by a new record of the same id.

## 5. Using the ledger
- **Before starting**, every role reads `scripts/squad/errors.sh summary --role <role>` (roles without Bash: Grep
  `^- Category:` / `^- Prevention:` in `docs/squad/features/*/records/errors.md` for their categories) and avoids the recurring
  patterns; QA adds a regression TC for each recurring pattern touching this feature.
- **Gates:** `check.sh ready cto-cab|cab-pack|ceo-golive` fails while the feature has an open S1/S2;
  `check.sh cabpack` requires every open S3/S4 in "Residual risks".
- **Retro:** reads `errors.sh summary` and `errors.sh list --feature <slug>`; every S1/S2 and every recurrence gets
  a root-cause line in retro.md and, if it generalises, a lesson; a category that keeps escaping the same stage
  is a `KIT:` improvement candidate.
