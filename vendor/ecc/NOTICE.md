# Vendored ECC skills and agents

This directory contains skills and agents copied **verbatim** from the ECC project
(the agent harness operating system), so that OPC Kit works without a live dependency
on the ECC plugin and so every supported AI harness (Claude Code, Kiro, …) can share
the same workflow knowledge.

- Upstream: https://github.com/affaan-m/ECC
- Version vendored: 2.2.2
- Git commit: c70874fae9eb0e5ad0365beb7e2955899fd1d30f
- License: MIT — see `LICENSE` in this directory (Copyright (c) 2026 Affaan Mustafa)

Only the skills and agents actually referenced by OPC Kit's squad roles are vendored,
not the full ECC catalogue. The ECC MIT license permits copying and adaptation provided
the copyright notice and license text are preserved; both are kept in `LICENSE`.

To refresh these files from a newer ECC, re-run the kit's vendor step
(`tools/vendor-ecc.sh`) against an installed ECC plugin, or copy the relevant
`skills/<name>/` and `agents/<name>.md` from the upstream repository.

The `ecc:` identifiers used throughout the squad agents and skills resolve to:
- `ecc:<name>` skill  → `vendor/ecc/skills/<name>/SKILL.md`
- `ecc:<name>` agent  → `vendor/ecc/agents/<name>.md`
