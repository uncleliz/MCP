---
name: squad-layout
description: Where every squad document lives — the phase-ordered feature folder (state.json, records/, 1-discovery … 9-retro, evidence/), the knowledge folder, naming rules, and the scripts that keep it disciplined (layout.sh path/classify/check/index, migrate-layout.sh). Read it before writing any file under docs/squad/; a hook refuses paths outside the layout.
---

# Document layout

**Source of truth:** `scripts/squad/layout.sh table` (every script and hook reads the same table). Never guess a
path: `scripts/squad/layout.sh path <key> <slug> [env|role|area]` prints it.

```
docs/squad/
├── README.md                         # GENERATED (layout.sh index) — never edit by hand
├── features/<slug>/                  # one feature, never moved or renamed
│   ├── state.json                    # Delivery Manager
│   ├── records/                      # append-only ledgers: decisions.md (CTO), errors.md (finder/fixer/verifier)
│   ├── 1-discovery/                  # product-requirement.md, market-research.md, options.md, decision-brief.md
│   ├── 2-gate1/plan-approval.md      # CEO's words, written by the Delivery Manager
│   ├── 3-spec/requirements.md
│   ├── 4-design/                     # architecture.md, api-contract.yaml
│   ├── 5-plan/implementation-plan.md
│   ├── 6-verify/                     # test-plan.md, test-cases.md, regression-report-<dev|uat|pre>.md, review-report.md
│   ├── 7-release/                    # release-log.md, cab-pack.md
│   ├── 8-gate2/cab-approval.md       # CEO's words, written by the Delivery Manager
│   ├── 9-retro/retro.md
│   └── evidence/<stage>/<YYYYMMDD-HHMMSS>-<desc>.<ext>   # logs, screenshots, reports (committed to git)
└── knowledge/                        # see squad-knowledge
    ├── lessons.md, distill-log.md    # append-only
    ├── roles/<squad-role|all>.md     # compiled handbooks
    ├── packs/<name>-<date>/          # exported (knowledge.sh export)
    └── imported/<pack>/              # imported, waiting for a distill
docs/adr/NNNN-<title>.md              # architecture decisions (coord.sh next-adr)
.kiro/rules/squad-learned-<area>.md # learned rules (distill)
```

## Rules
- Lower-case kebab-case ASCII names; fixed file names from the table; environments as fixed suffixes.
- No copies or version words in names (`-v2`, `final`, `old`, `copy`, `backup`, `tmp`, `draft`, `new`): git keeps versions.
- Evidence: `evidence/<stage>/<YYYYMMDD-HHMMSS>-<desc>.<ext>`, `<stage>` is a stage name (qa-pre, deploy-uat, …);
  no sub-folders. Evidence is committed; files over 50 MB are flagged (GitHub refuses 100 MB — use git LFS).
  Cite evidence by path in reports and release-log entries.
- Never move a document after it exists; ids and links depend on stable paths.
- `scripts/squad/layout.sh check` must pass (doctor and CI run it). A project still on the pre-2.2 flat layout
  must run `scripts/squad/migrate-layout.sh` before any new feature starts.
