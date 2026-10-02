---
name: squad-model-routing
description: Which model tier each squad role runs on and when the orchestrator may change it. Three tiers — HIGH (judgement/architecture), MID (reason + produce), LOW (mechanical, token-heavy) — chosen per role so the squad spends the big model only where it decides outcomes and cheap models where it reads and types. Read by the /squad orchestrator at dispatch; the tier is baked into each agent's config at install.
---

# Model routing — right model for each role

The orchestrator-executor economics are simple: a strong model should plan and judge; cheap models should
do the token-heavy reading and typing. Running one model for everything either overpays (everyone on the
big model) or underperforms (everyone on a cheap one). The squad assigns a **tier** per role.

## The three tiers

| Tier | Why | Roles |
|---|---|---|
| **HIGH** | Judgement, architecture, decisions that shape everything downstream | `squad-cto`, `squad-sa`, the Delivery Manager |
| **MID** | Reason and produce — design-shaped work and code | `squad-lead`, `squad-reviewer`, `squad-ba`, `squad-backend`, `squad-frontend` |
| **LOW** | Mechanical, token-heavy: reading, running scripts, filling templates | `squad-po`, `squad-qa`, `squad-release`, `squad-researcher` |

How the tier is applied per backend:
- **Claude Code:** the tier is the `model:` in each agent's frontmatter (`opus` = HIGH, `sonnet` = MID,
  `haiku` = LOW). The orchestrator may override per dispatch via the Agent tool's `model` param.
- **Kiro:** the installer resolves `KIRO_MODEL_HIGH` / `KIRO_MODEL_MID` / `KIRO_MODEL_STD` into each
  agent JSON; an empty tier falls back (MID→STD) or to the session default.
- **OpenCode:** same with `OPENCODE_MODEL_HIGH` / `_MID` / `_STD` (`provider/model`); empty MID → STD,
  empty → the globally-configured model.

## Rules the orchestrator follows at dispatch
1. Dispatch each role at its tier above. Record the model actually used in the `history` note (so
   `ecc:cost-tracking` and the retro can see it).
2. **Lean track (`tier: standard`) — allowed to save tokens:** you may drop a *non-core* role one tier
   (e.g. Reviewer MID→LOW, Lead MID→LOW) when the feature is small and low-risk. State it in the history note.
3. **Never downgrade** `squad-cto`, `squad-sa`, or the independent `general-purpose` checkers used by
   santa-method (D2/D3). These guard correctness and go-live; they always run at HIGH.
4. **Full track (`tier: large`)** uses the tiers as-is; raise a role to HIGH only with a reason in the
   history note (e.g. a security-critical review).
5. If a backend has no model configured for a tier, the fallback applies (MID→STD, then the session/global
   default). Do not block on a missing tier.

## Note on the Reviewer at MID
The Reviewer runs at MID by default. Correctness is still guarded by the independent HIGH checkers in
santa-method (D2/D3) and the CTO's own HIGH review. For a feature where review quality is the main risk
(subtle cross-file invariants, security surface), raise `squad-reviewer` to HIGH for that feature and note why.

## Cost caveat (measure, don't assume)
Cheaper-per-token is not cheaper-per-task: a weaker model that re-reads and retries can cost more. Judge a
tier change by cost-per-completed-task over your own features, not by the price sheet. When in doubt on a
risky feature, keep the higher tier.
