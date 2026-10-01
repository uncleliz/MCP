---
name: squad-knowledge
description: How the squad compresses what the team has learned into small, role-scoped, enforceable knowledge — the four-tier ladder (events → lessons → role handbooks → learned rules and checks), the distill procedure the CTO runs every few features, the applied-lesson signal that retires unused knowledge, and export/import of knowledge packs between the team's projects. Used by squad-cto (modes retro and distill), the /squad orchestrator, and every role when it reads its handbook.
---

# Team knowledge — compression ladder

Knowledge climbs a ladder. Each rung is shorter and cheaper to use than the one below; the top rung costs
nothing to read because a machine enforces it. Raw history is never deleted — only the *active* layers are compressed.

| Tier | What | Where (scripts/squad/layout.sh) | Who reads it |
|---|---|---|---|
| 0 Events | defects, decisions, retros | `features/<slug>/records/errors.md`, `records/decisions.md`, `9-retro/retro.md` | the retro and the distill only |
| 1 Lessons | one actionable rule per lesson, append-only | `knowledge/lessons.md` (`L-nnn`) | the distill; roles only until their first handbook exists |
| 2 Handbooks | the active lessons of one role, compiled and capped | `knowledge/roles/<squad-role>.md`, `knowledge/roles/all.md` | **every role, before starting** |
| 3 Enforced | learned rules for a code area, checks, hooks | `.kiro/rules/squad-learned-<area>.md` (loads only when that code is touched); `KIT:` proposals for check.sh / hooks | nobody — the harness applies them |

Compression moves knowledge **up**: merge similar lessons, turn an area-specific lesson into a learned rule, turn a
recurring defect class into a check, and retire what no longer applies.

## Formats (validated by `scripts/squad/check.sh`)
**Lesson** — appended by the retro (≤ 3 per feature) or by a distill (merged lessons):
```markdown
## L-<nnn> · <squad-role|all> · <feature-slug|k-nnn> · <YYYY-MM-DD>
- Rule: <one imperative sentence the role can apply>
- Why: <evidence ids: E-…, D-…, TC-…, R-…>
- Supersedes: <L-ids it replaces>          (optional — a merge)
- Area: <code area, e.g. auth>               (optional — candidate for a learned rule)
```
Ids come from `scripts/squad/coord.sh next-lesson`. **Status records** (distill only), appended after the lesson:
```markdown
### L-<nnn> · retired · K-<nnn> · <YYYY-MM-DD>
- Reason: <no longer true / unused in N features / covered by …>
### L-<nnn> · promoted · K-<nnn> · <YYYY-MM-DD>
- To: <.kiro/rules/squad-learned-<area>.md | KIT: check.sh <target> | KIT: hook <name>>
```
A lesson is **active** unless superseded, retired or promoted (`scripts/squad/knowledge.sh active`).

**Handbook** — rewritten by each distill, never edited by hand; ≤ `HANDBOOK_MAX_LINES` rules (default 40):
```markdown
# Handbook · <squad-role|all> · K-<nnn> · <YYYY-MM-DD>
> Compiled by distill K-<nnn>. Do not edit by hand.
- <imperative rule> [L-012, E-otp-login-003]
```
Every rule cites active lessons or other source ids; no prose, no history.

**Learned rule** — for knowledge that applies to one code area only:
```markdown
---
paths:
  - "<backend-dir>/auth/**"
---
# Learned · <area> · K-<nnn>
- <imperative rule> [L-012]
```
1–15 rules per file; it loads only when an agent touches matching files.

**Distill log** — `knowledge/distill-log.md`, append-only, one entry per distill:
```markdown
## K-<nnn> · <YYYY-MM-DD>
- Features: <slugs covered by this distill>
- Input: <n> active lessons, <n> defects (<n> recurring), imported packs: <names|none>
- Merged: <L-a + L-b → L-c, … | none>
- Promoted: <L-x → squad-learned-auth.md, … | none>
- Retired: <L-y (reason), … | none>
- KIT: <proposed checks/hooks for the CEO | none>
- Handbooks: <role=lines, …>
- Active lessons: <before> → <after>
```

## When a distill runs
`scripts/squad/knowledge.sh due` → after every `DISTILL_EVERY` finished features (default 3), when active lessons
exceed 40, or on `/squad distill`. It runs in the main checkout under the `main` lock, after the retro.

## Distill procedure (squad-cto, mode distill)
1. Read `knowledge.sh status`, `knowledge.sh active`, `knowledge.sh usage`, `knowledge.sh pending`,
   `errors.sh summary`, and every pack under `knowledge/imported/`. Get the id: `knowledge.sh next-k`.
2. **Merge** lessons that say the same thing: append one new lesson with `Supersedes:`.
3. **Promote** — a lesson tied to one code area → a learned rule (then append `### … · promoted`); a recurring
   defect class a script could detect → a `KIT:` proposal (the CEO decides whether to change the kit).
4. **Retire** lessons that are no longer true, are covered elsewhere, or were applied 0 times while
   ≥ 5 features finished since they were written (`usage`) — with a reason.
5. **Imported packs:** adopt a pack rule only by writing it as a new lesson (`Why:` cites the pack name); then the
   pack folder stays as evidence.
6. **Compile handbooks:** for each role (and `all`), the active lessons that apply, as one-line rules with
   source ids, most important first, within the line limit. A role with no lessons gets no handbook.
7. Append the distill-log entry; `scripts/squad/check.sh` must pass for `lessons`, `distill-log`, every
   handbook and every learned rule written.

## Applied signal
When a role uses a handbook rule, it lists the lesson ids under `applied:` in its HANDOFF; the orchestrator
stores them in the history entry. `knowledge.sh usage` counts them — knowledge nobody applies decays.

## Packs (team scope, across projects)
`scripts/squad/knowledge.sh export <team-name>` writes `knowledge/packs/<name>-<date>/` (manifest, active lessons,
handbooks, learned rules). Another project imports it with `knowledge.sh import <dir>` into `knowledge/imported/`;
nothing applies until that project's next distill adopts it. Packs are committed like any other document.
