# mcp-data-platform — Regression Report · env dev (CHG-003, run 1, 2026-10-02)

> **Scope: CHG-003 (real ingestion + real egress, CLI-driven, 9-source runbook, Confluence first),
> independent QA verify, env `dev`.** This run verifies the 13-task CHG-003 slice (T-111…T-123): the
> single default-deny egress choke point (`mcp_common/egress.py`), real Confluence Cloud ingestion
> via the CLI and its generalisation to GitLab/OpenSearch/Jira, the least-privilege read-only
> credential handling + value-based token scrub wired into production (E-009), the HuggingFace model
> egress (separable, offline-flip), and the four ADR-0023 §6e adversarial/invariant tests. QA re-ran
> everything from the on-disk build and **did not trust the backend sign-off** (`docs/signoff/chg003.md`
> / the backend's own `make ci`); every number below was produced by a QA-run command this session,
> captured under `evidence/qa-dev/` with the `20261002-12*` timestamps.
>
> The CHG-001 slice (TC-078…TC-110) was verified PASS at `chg001-qa-verify` run 1 (that report's
> content is preserved in git history and in `state.json`; this file supersedes it for the CHG-003
> slice); the base TC-001…TC-077 slice is preserved at `evidence/legacy/regression-report.md`. This
> report covers the CHG-003 extension only.
>
> **Threshold discipline (L-002).** Opening HuggingFace egress **enables** the NFR-003 measurement; it
> does **not** prove semantic quality. This run asserts only the **τ-independent / honesty invariants**
> (egress default-deny, allow-list honoured, token-never-leaks, server read-only + stdio, read-only
> ingest-to-source, `calibration_status=uncalibrated`, no invented recall/τ). NFR-003 stays
> **UNVERIFIED** until a real golden-set bake-off (spike S2) is measured on a real model — even though
> egress now *can* be opened. No recall number or τ value is asserted or invented.

## Summary

| Level | Tool | Collected | Passed | Failed | Skipped | Notes |
|---|---|---|---|---|---|---|
| `make ci` FULL (lint→mypy→unit+coverage→contract→readonly) | `make ci` | — | **exit 0** | 0 | — | ruff `All checks passed!`; mypy `Success: no issues found in 187 source files`; `validate-contract` → `OK: … api-contract.yaml is a valid contract` |
| BE unit + integration (the `coverage` step) | `uv run pytest --cov …` | 2550 | **2344** | 0 | 206 | **coverage 90.08% (TOTAL, ≥ 80% DoD)**; 206 skips all reasoned (see below), 0 fail / 0 error |
| Read-only suite (the `readonly` step) | `make readonly` (`-k readonly -m "not live"`) | 2550 | **215** | 0 | 0 (2335 deselected) | every package's `assert_readonly_tool_surface` green |
| CHG-003 targeted TCs (egress #1/#2/#4, token-scrub, runbook, model, nfr003, isolation, cloud-cred) | `uv run pytest <18 files>` | 120 | **112** | 0 | 8 | 8 skips = pgvector-e2e arms (re-run with the container below) + the 2 `@live` arms; see Results |
| CHG-003 Confluence-Cloud + generalisation e2e — **real pgvector 0.8.6** | `uv run pytest <2 files>` (PGPASSWORD set) | 10 | **9** | 0 | 1 | TC-118/119/120/122/123 pass on the real container; TC-121 (`@live`) skips |
| TC-124 restricted real-ingested permission regression — **real pgvector 0.8.6** | `uv run pytest …chg003.py` (MCP_LIVE_TESTS=1 + PGPASSWORD) | 3 | **3** | 0 | 0 | restricted real-ingested doc blocked at ingest + never a candidate/pack/citation (E-007 not weakened) |
| Server read-only + stdio e2e (base 9 servers, real subprocess) | `uv run pytest e2e/test_stdio_readonly_e2e.py` + no-egress unit | 20 | **20** | 0 | 0 | TC-125/126 — 0 write tools, 0 new port, no server egress |
| TC-115 token-scrub mechanism + E-009 production wiring | `uv run pytest <2 files>` | 14 | **14** | 0 | 0 | opaque secret scrubs from result + stderr by construction only, baseline-negative proven |

**Coverage this run: 90.08%** (`TOTAL 11670 stmts, 973 miss, 90%`; `Required test coverage of 80.0%
reached. Total coverage: 90.08%`) — clears the ≥ 80% Definition-of-Done bar. CHG-003 changed-code
coverage: `egress.py` **98%**, `redact.py` **94%**, `mcp_ingest/connectors/confluence.py` 96%,
connectors `_common.py`/`__init__.py` 100%.

**The 206 skips (all reasoned, none a failure):**
- **157** × local-`initdb` `pg_server` fixture — `packages/conftest.py::_find_pg_bin()` only probes the
  Debian `/usr/lib/postgresql/*/bin/initdb` path; this is a macOS/Apple-silicon host, so the
  distro-binary package-level Postgres tests skip. The real-DB behaviour is instead exercised against
  the running `pgvector/pgvector:pg16` container by the Confluence-Cloud e2e + TC-124 below.
- **32** × `cannot reach mcp-dev-postgres: OperationalError` — the `docker_pg_factory` DSN is
  passwordless; the dev container requires a password. In `make ci` (no `PGPASSWORD`) these skip; this
  QA run **re-ran** them with `PGPASSWORD=<mcp_admin dev password>` set, and they **passed** (see the
  real-pgvector rows). Reasoned skip in CI, proven green on the container here.
- **14** × `@pytest.mark.live` (`MCP_LIVE_TESTS` unset) — need real remote credentials/VPN/online
  egress; skip with reason, not fabricated. Three of these (TC-124's suite) are a *container* probe,
  not a remote tenant, and were re-run green with `MCP_LIVE_TESTS=1` + the container.

### Real environment used

- Host: **macOS / Darwin, arm64 (Apple silicon)**; the `uv` workspace runs Python **3.12.6** in
  `.venv` (the system `python3` is 3.14.3 but is not used by the suite). `uv` **0.11.7**.
- Docker Engine present; container **`mcp-dev-postgres`** = **`pgvector/pgvector:pg16`**, published on
  `127.0.0.1:5433`, status **healthy** (up 18 h). Roles `mcp_admin`, `mcp_query_ro`, `mcp_ingest_rw`
  present. For the real-DB arms, QA set `PGPASSWORD` to the `mcp_admin` dev password; every real-DB
  arm creates a **throw-away database** (`t_<uuid>`) and drops it on teardown — the live `mcp_kb`
  database and the Oct-1 go-live data are **not touched** (verified by the `docker_pg_factory`
  contract).
- Branch `claude/zealous-johnson-yb3t2q`, HEAD `b898040`. QA touched only QA-owned paths
  (`6-verify/regression-report-dev.md`, this file; the `records/errors.md` E-009 verified
  record; evidence under `evidence/qa-dev/`); `packages/**`, `docs/adr/`, `state.json` untouched. No
  source code was edited.
- **No live creds, no live egress:** `MCP_EGRESS_ALLOWLIST` default-empty in `make ci`;
  `MCP_INGEST_ALLOW_LIVE_EGRESS` off; Confluence driven by `respx` fixtures + a fake read-only token;
  embeddings via `DeterministicFakeProvider`; the real `bge-m3` download is the `@live` TC-131 only.

## Results

### `make ci` FULL + read-only surface (independent re-run, not trusting the backend report)

| TC | Covers | Status | Evidence |
|---|---|---|---|
| — | `make ci` FULL exit 0: lint clean, mypy 187 files clean, 2344 passed / 0 failed / 206 reasoned skips, coverage 90.08%, contract valid, readonly 215/215 | pass | `evidence/qa-dev/20261002-120909-chg003-make-ci-full.txt` |
| TC-126 | every base server + Jira read-only surface over real stdio; 0 write tools; stdio round-trip unchanged; stderr-only logs | pass | `evidence/qa-dev/20261002-121334-chg003-perm-and-stdio-readonly.txt` (`test_TC_056…[9 servers]`, `test_TC_054_live_tool_surface_is_48_and_readonly`, DSL toggle +1) |

### The four ADR-0023 §6e adversarial / invariant tests (highest priority, L-001)

| §6e | TC | Property proven | Status | Evidence (node ids) |
|---|---|---|---|---|
| **#1 egress default-deny** | TC-113 | with the allow-list set, a request to an **unlisted** host is **REFUSED** (`EgressDenied`), **not** logged-and-allowed, and **no socket** is opened to it | **pass** | `…-chg003-targeted-tests.txt::test_egress_default_deny.py::test_empty_allowlist_denies_every_host`, `::test_unlisted_host_refused_even_when_allowlist_has_other_hosts`, `::test_denied_host_opens_no_socket`, `::test_build_client_refuses_unlisted_host_before_transport`, `::test_lookalike_host_is_not_a_match` |
| **#2 allow-list honoured** | TC-114 (+ TC-111 positive, TC-112 single-choke-point) | an allow-listed host (`*.atlassian.net`, `huggingface.co`) is permitted; an **unconfigured** host **fails closed with no connection attempted** (fail-closed *before* dial); exactly **one** `check_egress` choke point, **no bypass** | **pass** | `::test_egress_allowlist.py::test_configured_atlassian_host_is_permitted`, `::test_huggingface_host_is_permitted_on_the_model_path`, `::test_unconfigured_host_fails_closed`, `::test_allowlisted_host_proceeds_through_guarded_client`, `::test_unconfigured_host_attempts_no_connection_through_guarded_client`; TC-112 single choke point: `test_chg003_signoff_checks.py::test_egress_is_one_default_deny_choke_point`; TC-111 real-pgvector positive: `test_confluence_cloud_e2e.py::test_TC118…` (pull proceeds through `check_egress`) |
| **#3 token never leaks (E-003) + E-009 production wiring** | TC-115 | force an error on the credential path; the token is **absent** from the tool result **and** the stderr log — `scrub()` both ways; **AND** the production wiring: `register_secret`/`register_dsn_secret` is **actually CALLED at the 11 client constructors**, so an **opaque** token scrubs in production (not only in the mechanism unit test) | **pass** | `…-chg003-tc115-token-scrub-wiring.txt`: `test_token_never_leaks.py` 9 passed (mechanism, TC-115) + `test_token_scrub_wiring.py` 5 passed (wiring: `test_building_{confluence,gitlab,opensearch,pgvector}_client_registers_the_*` + the baseline-negative `test_opaque_secret_is_not_scrubbed_before_any_client_is_built`). Wiring confirmed by grep at `mcp_{confluence,jira,gitlab,opensearch,kibana,redis,kafka,cloudwatch,sqs_sns}/client.py` (`register_secret`) + `mcp_{pgvector,knowledge}/client.py` (`register_dsn_secret`). See E-009 verified record |
| **#4 server read-only + stdio unchanged, 0 ports** | TC-125 (+ TC-116/TC-126) | the 9 MCP servers + Jira keep **stdio**, open **no new listening socket**, make **no outbound call beyond their own upstream**; `doctor` refuses a write-capable Atlassian account | **pass** | `…-chg003-perm-and-stdio-readonly.txt`: `test_servers_no_egress_no_port.py` 4 passed (`test_server_packages_do_not_enable_egress_enforcement`, `test_default_build_client_installs_no_egress_hook`, `test_enforce_egress_client_adds_exactly_one_egress_hook`, upstream-reachable-without-allowlist) + e2e `test_stdio_readonly_e2e.py` 16 passed; TC-116 doctor-refusal: `test_credential_readonly_cloud.py::test_TC116_cloud_write_capable_account_is_refused_naming_the_write_op`, `::test_TC116_startup_gate_refuses_to_serve_a_write_capable_cloud_account` |

### FR-023 real Confluence Cloud ingestion via CLI + FR-026 generalisation (real pgvector 0.8.6)

| TC | Covers | Status | Evidence |
|---|---|---|---|
| TC-118 | **E2E** doctor → `mcp-ingest run --source confluence` → `status --json` → `kb_semantic_search`; counts > 0; citation `source_uri` resolves to `tnexwm.atlassian.net` — on **real pgvector**, fixtures + fake token | pass | `evidence/qa-dev/20261002-121322-chg003-e2e-real-pgvector.txt::test_TC118_confluence_cloud_e2e_doctor_run_status_then_search` |
| TC-119 | source unreachable mid-run ⇒ failure recorded, checkpoint does not advance, prior embeddings not corrupted, `status=partial/failed`, no fabricated content | pass | `…-e2e-real-pgvector.txt::test_TC119_unreachable_source_is_partial_and_leaves_the_checkpoint` |
| TC-120 | read-only-to-source: only **GET/HEAD** issued to Confluence; writes land **only** in `kb.*` under `mcp_ingest_rw`; zero write/update/delete reach the source | pass | `…-e2e-real-pgvector.txt::test_TC120_pull_is_read_only_to_source_and_writes_only_to_kb` |
| TC-122 | same doctor→run→status→verify shape for **GitLab / Jira**; OpenSearch off-by-default crawls nothing; unconfigured host denied; no connector-specific relaxation of egress/read-only | pass | `…-e2e-real-pgvector.txt::test_TC122_gitlab_runs_the_same_shape…`, `::test_TC122_jira_runs_the_same_shape…`, `::test_TC122_opensearch_is_off_by_default_and_crawls_nothing`, `::test_TC122_gitlab_pull_to_an_unconfigured_host_is_denied`, `::test_TC122_every_ingestable_build_enables_the_guard_no_relaxation` |
| TC-123 | an **allow-listed** OpenSearch index ingests through the same shape (default-deny lifts only for the configured index) | pass | `…-e2e-real-pgvector.txt::test_TC123_opensearch_allowlisted_index_ingests_through_the_same_shape` |
| TC-121 | **`@live`** real-tenant Confluence ingest against `tnexwm.atlassian.net` | **skipped-in-ci** | gated by `MCP_INGEST_ALLOW_LIVE_EGRESS=true` + CEO real token + VPN + online egress — not available here; `…-e2e-real-pgvector.txt::test_TC121_live_confluence_cloud_against_the_real_tenant SKIPPED` |

### FR-026 the 9-source runbook — two classes + live-only register/never-ingested

| TC | Covers | Status | Evidence |
|---|---|---|---|
| TC-123 (ingestable runbook) | the ingestable-class runbook shape (doctor→ingest→status→verify) exists per source in the runbook text | pass | `…-chg003-targeted-tests.txt::test_runbook_ingestable.py` (doctor + ingest-run accept × {confluence,gitlab,opensearch,jira}, status, dry-run/limit — 10 passed) |
| TC-127 | the ingest CLI **does not accept** a live-only source (cloudwatch/kibana/kafka/redis/sqs_sns); registry = exactly the 4 ingestable | pass | `…-chg003-targeted-tests.txt::test_runbook_live_only.py::test_TC127_live_only_source_is_refused_by_ingest[…]` (×5), `::test_TC127_registry_holds_exactly_the_four_ingestable_sources` |
| TC-128 | **live-only class**: doctor read-only ok, server appears with 0 write tools, registration fixture, and **no** `mcp-ingest run` issued ("integrate" = reachable + read-only + registered, not ingested) | pass (fixture arm) | `…-chg003-targeted-tests.txt::test_runbook_live_only.py::test_runbook_live_only_doctor_command_exists[…]`, `::test_runbook_live_only_surface_has_zero_write_tools[…]`, `::test_runbook_live_only_config_emit_block_is_pasteable[…]` |
| TC-128 (live-register arm) | the real registration + reachability against the live system | **skipped-in-ci** | operator step behind `MCP_INGEST_ALLOW_LIVE_EGRESS=true`; CI runs the fixture `tools/list` + registry assertion only (above) |
| TC-124 | **adversarial, E-007**: a **real-ingested** restricted Confluence doc is blocked at ingest (`kb.ingest_failures` blocked_by_policy), and for an un-granted caller is **never** a candidate / in the pack / cited — on **real pgvector** | pass | `evidence/qa-dev/20261002-121433-chg003-tc124-real-ingested-permission.txt` (3 passed): `test_TC124_restricted_real_ingested_doc_is_blocked_at_ingest`, `…_ungranted_caller_sees_no_real_ingested_content_at_the_chokepoint`, `…_restricted_content_never_a_candidate_pack_or_citation` |

### FR-027 real embedding-model download enabling NFR-003 measurement (L-002, model stub)

| TC | Covers | Status | Evidence |
|---|---|---|---|
| TC-129 | model egress **separable** from Atlassian egress; `HF_HUB_OFFLINE` flips online for the download step then back to offline; **0** outbound socket during serving; deferred-model case still ingests with the existing provider; a non-`huggingface.co` host on the model path is refused | pass | `…-chg003-targeted-tests.txt::test_model_download_offline_flip.py::test_TC129_hf_offline_flips_online_for_download_then_restores`, `::test_TC129_serving_opens_no_socket`, `::test_fake_provider_remains_the_default_test_path`; `test_model_egress_separable.py::test_TC129_model_path_allows_huggingface_only`, `::test_TC129_model_path_refuses_non_huggingface_host`, `::test_TC129_egresses_are_separable` |
| TC-130 | **CONDITIONAL / TBD (NFR-003/NFR-010, L-002)**: the eval harness runs and reports a **verdict distribution**; envelope `calibration_status=uncalibrated`; **recall is `None`**; the `# THRESHOLD TBD (NFR-010, L-002)` marker is grep-able; **no recall number or τ value is asserted or invented** | pass (honesty contract only) | `…-chg003-targeted-tests.txt::test_nfr003_conditional.py::test_TC130_harness_runs_and_reports_verdict_distribution_only`, `::test_TC130_report_is_uncalibrated_and_invents_no_recall_or_tau`, `::test_TC130_threshold_tbd_marker_is_grep_able`; marker confirmed in `grounding/eval_harness.py:43`, `grounding/confidence.py:45/47` |
| TC-131 | **`@live`** real `bge-m3` (~2 GB) download + real-model eval | **skipped-in-ci** | gated by `MCP_INGEST_ALLOW_LIVE_EGRESS=true` + `MCP_LIVE_TESTS` + online egress; `…-chg003-targeted-tests.txt::test_model_download_offline_flip.py::test_TC131_live_real_bge_m3_download SKIPPED` |

### Connector isolation + sign-off cross-checks (regression)

| TC | Covers | Status | Evidence |
|---|---|---|---|
| — | each connector calls only source read-only-allowlisted operations; no ingest module imports a read_api/tools layer (ADR-0012 A4); registry = v1 connectors + Jira | pass | `…-chg003-targeted-tests.txt::test_connector_isolation.py` (13 passed) |
| — | all nine sources have a doctor CLI; runbook source classes match the registry; egress is one default-deny choke point; model egress scoped to huggingface; ADR-0010 stays provisional; NFR-003 harness uncalibrated + invents no number | pass | `…-chg003-targeted-tests.txt::test_chg003_signoff_checks.py` (6 passed) |

## Failures triage

No failing test cases at any level this run (0 failed, 0 errors across `make ci`, the 112 targeted
tests, the 9 real-pgvector e2e arms, the 3 TC-124 real-ingested permission arms, the 20 read-only +
no-egress tests, and the 14 token-scrub tests).

| TC | Symptom | Suspected owner | Class |
|----|---------|-----------------|-------|
| — | none | — | — |

**Error ledger.** This PASS run verify-closes **E-mcp-data-platform-009** (S2 security, CHG-003) — the
value-based scrub mechanism is now wired at all 11 client constructors and a production-wiring test
proves an opaque secret scrubs from result + stderr by construction only; a `### E-mcp-data-platform-009
· verified · 2026-10-02` record is appended to `records/errors.md` (append-only). **E-mcp-data-platform-008**
(S3, test-plan E2E-gap for base Must FR-005 / Kibana) is left **open as-is** — it is deferred to the
CTO and is pre-existing (not a CHG-003 defect). No open S1/S2 remains for the CHG-003 slice.

## Verdict: PASS

`make ci` FULL is exit 0 independently (2344 passed / 0 failed / 206 reasoned skips, coverage 90.08%
≥ 80%, lint + mypy clean, contract valid, readonly 215/215). All **four ADR-0023 §6e** adversarial /
invariant tests hold: (#1) egress default-deny refuses an unlisted host and opens no socket; (#2) the
allow-list is honoured and an unconfigured host fails closed before dial through the single
`check_egress` choke point with no bypass; (#3) the token never leaks on a forced error — scrubbed
both ways — **and** the production wiring (E-009) is proven: `register_secret`/`register_dsn_secret` is
called at the 11 client constructors so an opaque token scrubs in production, not only in the
mechanism unit test; (#4) the 9 servers + Jira stay read-only-to-source + stdio with 0 new ports and
no server egress. The Confluence Cloud e2e (doctor→run→status→verify, citation resolving to
`tnexwm.atlassian.net`), its generalisation to GitLab/OpenSearch/Jira, the live-only
register-but-never-ingest contract, and the TC-124 permission regression on **real-ingested
restricted content** all pass on **real pgvector 0.8.6**. The model path is separable with the
offline-flip and no serving egress; TC-130 reports a verdict distribution with
`calibration_status=uncalibrated` and invents no recall/τ.

**@live / live-egress TCs explicitly NOT run (skipped-in-ci, never silently dropped):** **TC-121**
(live Confluence ingest against the real `tnexwm.atlassian.net`), **TC-131** (real `huggingface.co`
`bge-m3` ~2 GB download + real-model eval), and the **live-register arm of TC-128** (real registration
+ reachability against a live system). Reason: each needs the CEO's real read-only token + VPN +
online egress behind `MCP_INGEST_ALLOW_LIVE_EGRESS=true`, none available in `dev`. They are the
operator (CEO) steps for a live run, carried to the Gate-C manual checklist.

**Caveats carried to Gate C (none blocks this dev verdict; all are NFR-003/τ or real-tenant gaps, not
code defects):**

1. **NFR-003 semantic quality still UNVERIFIED** — even though egress now *can* be opened, no real
   golden-set recall has been measured. Both the fixture e2e and the real-pgvector arms use the
   `DeterministicFakeProvider`; they prove the ingest→store→verify and permission/read-only paths, not
   retrieval relevance. Real measurement needs the live `bge-m3` download (TC-131, `@live`) + the
   spike-S2 bake-off on a real company golden-set. Requires explicit PO/CEO risk-acceptance at CAB
   (DK1 / D-002 / E-004 residual).
2. **Confidence τ (FACT↔LOW_CONFIDENCE) uncalibrated** — `calibration_status=uncalibrated`; only the
   τ-independent invariants (no-evidence⇒UNKNOWN, CONFLICT exposes all, no-fabrication) are enforced.
   The `# THRESHOLD TBD` markers remain grep-able; τ is calibrated only on a measured golden-set.
3. **Real-tenant / real-model arms are `@live`** — TC-121, TC-131 and the live-register arm of TC-128
   are the Gate-C operator checklist (real Atlassian token, VPN, online egress, Claude Desktop
   registration NFR-005).
4. **Package-level Postgres skips** — 157 initdb + 32 passwordless-DSN skips on this macOS host; the
   same behaviours are covered end to end by the real-pgvector Confluence-Cloud e2e + TC-124
   (re-run green here with the container + `PGPASSWORD`). Making `packages/conftest.py::_find_pg_bin()`
   portable is a backend change outside QA's write scope.
5. **E-mcp-data-platform-008** (S3, base Must FR-005 / Kibana has no E2E TC) stays open, deferred to the
   CTO — pre-existing, non-blocking, not a CHG-003 defect.
