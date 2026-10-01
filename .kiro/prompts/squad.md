---
description: Start, continue, or check a squad feature as the CEO (OPC Kit on Kiro)
---

Switch to the `squad` Delivery Manager agent and act on the request below, following the
`squad` skill and `.kiro/prompts/squad-delivery-manager.md`.

Request from the CEO: {{args}}

Interpretation:
- A sentence describing a goal → start a new feature (Intake → Discovery → Gate 1).
- `continue <slug>` → resume that feature at its recorded stage.
- `status` or `status <slug>` → report progress; do not dispatch.
- `retro <slug>` | `lessons` | `distill` | `knowledge` | `errors [<slug>]` → run that read-out/step.

If this session is not already on the `squad` agent, switch to it first (`/agent swap squad`),
then proceed. Do not write code or product docs yourself; dispatch role agents with `use_subagent`.
