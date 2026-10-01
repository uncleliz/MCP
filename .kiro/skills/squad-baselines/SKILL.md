---
name: squad-baselines
description: How the squad inherits the organisation's existing product and technology — the two CEO-owned living baselines (platform-baseline.md for the approved tech stack/vendors/patterns, business-baseline.md for the product's capabilities, domain entities, journeys and invariants), the deviation score that decides when a new feature may extend the baseline freely versus when it needs a detailed ADR and CEO approval, and the inheritance loop that grows the baselines after each go-live. Read by squad-po and squad-sa in Discovery, by squad-cto in plan-review/retro, and by the /squad orchestrator at Gate 1.
---

# Platform & business baselines — inheritance

A product is built once and extended many times. Every new feature must **inherit what already
exists** — build on the approved platform, reuse existing business capabilities — and must not drift
out of the organisation's standards without an explicit, reasoned decision by the CEO. Two living,
**CEO-owned** documents carry that memory across features:

| Baseline | File (`scripts/squad/layout.sh`) | What it records |
|---|---|---|
| **Platform** | `docs/squad/knowledge/platform-baseline.md` | approved tech: languages, frameworks, datastores, infrastructure, architecture patterns, vendors / paid services, data-boundary rules |
| **Business** | `docs/squad/knowledge/business-baseline.md` | the product's capabilities + the services that implement them, domain entities + relations, main user journeys, **business invariants** (rules a new feature must not break), constraints & boundaries |

**Ownership (hard rule).** Only the **Delivery Manager** writes these files, and only from the CEO's
Gate-1 decision (same discipline as `plan-approval.md`). A hook refuses any other writer. Roles **read**
them before starting and **propose** changes through an ADR-deviation and their HANDOFF — they never edit
a baseline directly.

## When each role uses the baselines

- **Discovery — `squad-po` (frame/brief) and `squad-sa` (options):** read both baselines first. For the
  goal, decide what is **reused / extended** from the existing platform and business, and what (if anything)
  would go **outside** them. Compute the **deviation score** (below) and record it.
- **`squad-cto` (plan-review):** if the deviation score is at or above the threshold, or the feature
  conflicts with a business invariant, require an **ADR-deviation** and `escalate_to_ceo`.
- **Gate 1 — the CEO:** the `gate-brief.md` states the deviation and any proposed baseline change. The CEO
  approves the plan **and** decides which new tech/business facts enter the baseline; the Delivery Manager
  records that under "Baseline changes approved" in `plan-approval.md`.
- **Retro / distill — `squad-cto` proposes, Delivery Manager applies:** after a successful go-live, the
  tech/business facts the CEO approved at Gate 1 are folded into the baselines. The baselines grow only
  from what the CEO has approved.

## Deviation score (0–100, hard + soft)

Add the points for every kind of deviation the feature introduces; cap the total at 100.

**Hard (objective — a reviewer can point at it):**

| Deviation | Points |
|---|---|
| New paid vendor / external service outside the baseline | 40 |
| Data leaving the company boundary (new data egress) | 40 |
| Breaking change to a contract other teams already use | 30 |
| New language / framework / datastore outside the baseline | 25 |
| New architecture pattern (e.g. monolith → event-driven) | 20 |

**Soft (CTO judgement, 0–15):** how far the design departs from the existing architecture and how little it
reuses — low reuse / notable architectural drift adds up to 15.

The score is **cumulative and capped at 100**; most single hard deviations already clear the threshold.

## Threshold → who decides

- **Below the threshold (default 10) and no business invariant touched** → the **CTO decides alone**; the
  feature extends the baseline freely. Record the score and the reused assets in `decision-brief.md`; no ADR
  or escalation needed. (A feature that stays entirely inside the stack approved at install also sits here.)
- **At or above the threshold, or it conflicts with a business invariant** → an **ADR-deviation is mandatory**
  and the CTO **escalates to the CEO** (`squad-decision-rights`). The CEO decides at Gate 1 whether to accept
  the deviation (and thereby extend the baseline) or stay inside it.

The threshold is `DEVIATION_THRESHOLD_PCT` in `config.env` (default 10); `plan-approval.md` may override it
for a feature. The CTO may ask the CEO to adjust the default as the organisation learns (a baseline change
like any other).

## Business invariants are a hard stop

A business invariant in `business-baseline.md` is a rule the product guarantees today (e.g. "a payment is
never captured without an authorised hold", "personal data is never shown to an unauthenticated caller").
If a feature would **break** an invariant, that is always an escalation regardless of the numeric score — the
CTO cannot wave it through. The CEO either amends the invariant (a baseline change) or the feature is
re-scoped to preserve it.

## ADR-deviation (written by `squad-sa`, in `docs/adr/`)

When the score clears the threshold or an invariant is touched, the SA writes an ADR whose title marks it a
deviation, with these sections:

```markdown
# ADR-NNNN: <title> (deviation)
## Status            — proposed (CEO decides at Gate 1)
## Baseline affected — platform | business | both; which entries
## Deviation score   — <n>/100, itemised (hard points + soft points, with reasons)
## Invariant impact  — none | <which invariant and how>
## In-baseline options considered — what staying inside the baseline would cost / why it is insufficient
## Decision sought   — accept deviation (extend baseline) | stay in baseline | re-scope
## Cost & risk        — of the deviation (build, run, lock-in, security)
## Recommendation     — the SA/CTO recommendation for the CEO
```

ADR numbers come from `scripts/squad/coord.sh next-adr`. The ADR is proposed; it becomes the record of what
the CEO decided once Gate 1 is approved.

## Bootstrapping the baselines

- At install, the kit seeds empty, templated `platform-baseline.md` and `business-baseline.md` (project-owned;
  never overwritten on re-install). The installer may also record the stack approved at setup.
- On the **first** `/squad` feature in a project that already has code, the squad traces the codebase
  (brownfield exploration the SA/Lead already do) and the Delivery Manager presents a **proposed** baseline in
  the Gate-1 `gate-brief.md` for the CEO to approve. From then on the baselines are authoritative.

## File templates (headings checked by `scripts/squad/check.sh`)

`platform-baseline.md`:
```markdown
---
doc: platform-baseline
owner: CEO
updated_at: <ISO datetime>
---
# Platform baseline
## Approved languages & frameworks
## Approved datastores
## Approved infrastructure & deployment
## Approved architecture patterns
## Approved vendors & paid services
## Data-boundary rules
## Change log            — <date> · <what entered/left> · approved at Gate 1 of <feature>
```

`business-baseline.md`:
```markdown
---
doc: business-baseline
owner: CEO
updated_at: <ISO datetime>
---
# Business baseline
## Capabilities          — capability → the service/module that implements it
## Domain entities       — entity → key relations
## Main user journeys    — numbered, one line each
## Business invariants   — rules a new feature must not break (INV-nnn)
## Constraints & boundaries
## Change log            — <date> · <what entered/changed> · approved at Gate 1 of <feature>
```
