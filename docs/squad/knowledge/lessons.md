# Team lessons ledger

> Append-only. One actionable rule per lesson; every role reads this (or its compiled handbook)
> before starting. Merging/retiring/promoting is the distill's job, never the retro's.
> Format: `## L-nnn · <squad-role|all> · <feature-slug|k-nnn> · YYYY-MM-DD`.

## L-001 · all · mcp-data-platform · 2026-10-01
- Rule: When an artifact declares a safety guarantee (size cap, deny-list, redaction, read-only), enforce it at a single structural choke point every path must pass through, and add a test that feeds the adversarial input the guarantee claims to stop — a guarantee that lives only in a docstring or ADR is not enforced.
- Why: E-001, E-002, E-003 (all S2 security, all introduced in backend, all escaped qa-plan+qa-verify); R-001, R-002, R-003 — three HIGH findings sharing the root cause "promised in prose, not centralised, no adversarial test".
- Area: security

## L-002 · squad-sa · mcp-data-platform · 2026-10-01
- Rule: Never let one metric name two different measurements; when a number is a proxy (e.g. ANN-index correctness) and the real NFR it is near is unmeasured — especially if blocked by environment — label the proxy for exactly what it measures and mark the NFR UNVERIFIED right next to it, so no gate reads the proxy as proof.
- Why: E-004 (S1 design — "recall ≥ 0.95" read as NFR-003 proof though it only measured ANN correctness under a fake provider); R-004; D-002 (NFR-003 carried to Gate 2 as explicit residual risk).
- Area: rag-eval

## L-003 · all · mcp-data-platform · 2026-10-01
- Rule: Resolve references to relocatable artifacts (contracts, docs, fixtures) path-tolerantly — search known locations instead of hard-coding one path — so a layout `git mv` or `migrate-layout.sh --force` cannot break CI or tooling.
- Why: R-025 (dead doc links from references pointing at pre-move paths — same path-drift class) and the layout migration that `git mv`'d 11 files (state 2026-10-01T16:43 layout_migrated) breaking CI on the old `api-contract.yaml` path, fixed only by a layout-tolerant `find_contract_path` (state 2026-10-01T17:20 rerun_done); recorded in D-003.
- Area: tooling
