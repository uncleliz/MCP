---
name: squad-retro
description: How the squad learns across features — the blameless retrospective after every feature (go-live, rejection or incident) written to retro.md, and the lessons ledger docs/squad/knowledge/lessons.md that every role reads before starting. Used by squad-cto in mode retro and by the /squad orchestrator.
---

# Retrospective and lessons ledger

A one-person company has no team memory besides files. The retro turns what happened in one feature into
short, role-tagged rules the next feature applies automatically (see `squad-protocol` §1.4).

## 1. When
- After C5 done (go-live success), after `closed` (CEO rejected at a gate), and after every C4 incident.
- Lean track: short retro (≤ 10 lines in retro.md, ≤ 2 lessons). Full track: full retro.

## 2. Input (read only — facts, not opinions)
`state.json` (history, loops), `errors.md` and `scripts/squad/errors.sh summary`, `decisions.md`, regression reports, review reports, `release-log.md`,
`plan-approval.md` (baseline) and the actual dates/costs/tokens recorded in history. The orchestrator may give
the feature folder as a path inside a worktree; read from there, but write `retro.md` and `knowledge/lessons.md` in the
main checkout (`docs/squad/features/<feature>/9-retro/retro.md`, `docs/squad/knowledge/lessons.md`).

## 3. `docs/squad/features/<feature>/9-retro/retro.md`
```markdown
# <Feature> — Retrospective (<ISO date>)
## Outcome            — delivered / rejected / rolled back; go-live date vs baseline; cost vs baseline
## Flow metrics       — lead time (goal → go-live), time in each phase, loops used per counter, RETURNs per stage, escalations
## Token use          — table: stage | dispatches | tokens (from state.json history) | model; total vs budget; the 3 most expensive stages and why
## Quality metrics    — defects found per env (dev/uat/pre/prod), review findings by severity, rollbacks, flaky TCs
## Defects            — from `scripts/squad/errors.sh list --feature <slug>`: per S1/S2 and per recurrence, one line
                        "E-id: introduced in X, escaped Y, root cause, prevention in place?"; ledger-wide RECURRING
                        patterns from `errors.sh summary` and which stage keeps letting them through
## What worked        — ≤ 3 bullets, each with evidence
## What hurt          — ≤ 3 bullets, each with evidence and root cause (5 whys, blameless: process, not role)
## Lessons            — the L-ids added to knowledge/lessons.md
## Incident postmortem — only after C4: timeline, detection, impact, root cause, why tests/review/PRE missed it, fix route
```

## 4. `docs/squad/knowledge/lessons.md` (append-only ledger shared by all features)
Create it with a title line if missing. Append; never rewrite earlier entries. Merging, retiring and promoting
lessons is the distill's job (`squad-knowledge`), not the retro's.
```markdown
## L-<nnn> · <squad-role|all> · <feature> · <YYYY-MM-DD>      (L-<nnn> from scripts/squad/coord.sh next-lesson)
- Rule: <one imperative sentence the role can apply, e.g. "add a TC for token expiry on every auth flow">
- Why: <evidence ids: E-…, D-…, TC-…, R-…>
- Area: <code area>   (optional — the distill may promote it to a learned rule)
```
A lesson is kept only if it is **actionable by one role**, **generalises** beyond this feature, and is
**not already covered** by an existing lesson or by the kit's own rules. Maximum 3 lessons per retro.
A RECURRING ledger pattern (same category introduced in the same stage across features) **must** produce a
lesson for the stage that keeps letting it escape, or a `KIT:` proposal if the kit's checks should catch it.
Where tokens went is a first-class lesson source: a stage that costs far more than its share (repeated loops,
re-reading large files, a helper agent spawned twice) usually has a process fix.
After the retro the orchestrator runs `scripts/squad/knowledge.sh due`; a due distill compresses the lessons
into handbooks and learned rules (`squad-knowledge`).

## 5. Kit improvements
If a lesson is really a defect of the kit (a rule that is wrong, a template gap), write it under
"What hurt" with the prefix `KIT:` — the CEO decides whether to change the kit templates.
