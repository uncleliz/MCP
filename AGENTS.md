<!-- squad:begin -->
## Squad workflow (OPC Kit, Kiro backend)

This repo is built by a role-based agent squad organised like a company. The user is the **CEO**.
The **Delivery Manager** is the `squad` agent (`.kiro/agents/squad.json`); start it with the `@squad`
prompt or `/agent swap squad`. It is the only role that talks to the CEO and dispatches the role agents
(`squad-cto`, `squad-po`, `squad-researcher`, `squad-sa`, `squad-ba`, `squad-lead`, `squad-qa`,
`squad-backend`, `squad-frontend`, `squad-reviewer`, `squad-release`) with the `use_subagent` tool.

- The CEO approves only **Gate 1** (the plan / option) and **Gate 2** (go-live at CAB).
- The **CTO** (`squad-cto`) makes every technical decision in between and escalates by `squad-decision-rights`.
- Process spec: the `squad` skill (`.kiro/skills/squad/SKILL.md`) and `.kiro/prompts/squad-delivery-manager.md`.
- Documents live under `docs/squad/` per `scripts/squad/layout.sh`; "done" is `scripts/squad/check.sh`.
- Hooks declared in each `.kiro/agents/*.json` enforce ownership, append-only ledgers, test integrity,
  no-force-push and the production-deploy guard.
- ECC skills/agents are vendored under `vendor/ecc/` (MIT; see `vendor/ecc/NOTICE.md`) — no live ECC plugin needed.
<!-- squad:end -->
