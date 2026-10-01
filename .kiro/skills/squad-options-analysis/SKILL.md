---
name: squad-options-analysis
description: How the squad builds and compares at least three solution options for the CEO — mandatory option types, weighted scoring, cost and time estimates, recommendation and sensitivity. Used by squad-sa (options.md) and squad-po (decision-brief.md).
---

# Options analysis

## Mandatory option set
- **≥ 3 viable options** (full track; **≥ 2** on the lean track, see `squad-tracks`), and among them:
  - at least one that **reuses or buys** an existing solution found in market-research.md (internal asset, OSS or vendor);
  - at least one that **builds** (lean track: may be "extend what exists" instead);
- plus a **baseline row "do nothing / defer"** (not counted in the three) stating the cost of not acting.
Options must be genuinely different (architecture, sourcing or scope), not three sizes of the same thing.

## `options.md` layout (headings are checked by `scripts/squad/check.sh options`)
```markdown
# <Feature> — Options
## Option A — <short name>        (one section per option: A, B, C …)
## Baseline — do nothing / defer
## Scoring                         — the weighted table below
## Recommendation
## Sensitivity
```

## For each option
| field | how |
|---|---|
| Summary | 2–3 lines, plain language |
| Sketch | 5 lines + optional Mermaid |
| Fit to Musts | per Must: full / partial / no |
| Time to go-live | calendar estimate with the main assumption |
| Build effort | person-days or agent-days, with range (low–high) |
| Run cost | monthly infra + licences, "as of <date>" |
| Risks | top 3 with likelihood (L/M/H) |
| Lock-in / reversibility | how hard to switch later |
| Operating burden | what a one-person company must run and watch (services, on-call, upgrades, vendor contracts) |
| Security & data | data classification touched, where data lives, new trust boundaries |

## Scoring (1–5 per criterion, weights sum to 100)
| criterion | default weight |
|---|---|
| Value / fit to Musts | 30 |
| Time to value | 20 |
| Total cost (build + 12 months run) | 20 |
| Risk (delivery, security, vendor) | 15 |
| Maintainability, operating burden & strategic fit | 15 |
Show the table with raw scores and the weighted total. If weights are changed, say why.

## Recommendation
- The recommended option and **why in 3 bullets**.
- **Sensitivity:** would the ranking change if the top uncertainty (cost, time or a key assumption) is off by 30%?
- **What would change our mind** — the evidence that would flip the recommendation.

## Honesty rules
- No invented numbers: every estimate has a basis (research source, comparable task, vendor price page).
- Ranges beat false precision. Mark unknowns `TBD — needs validation via <method>`.
