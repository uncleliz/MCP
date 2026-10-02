# ADR-0023 — CHG-003: open real egress for the ingest pull path + a real embedding model (deviation)

- **Status:** proposed
- **Date:** 2026-10-02
- **Deciders:** CEO (Gate 1, 2026-10-02 — Option B), CTO (sizing D-006)
- **Change:** CHG-003
- **Supersedes/relates:** ADR-0017 (CHG-001 deviation, vendors=none / no-egress kept), ADR-0007 (thin REST read-only client + startup read-only check), ADR-0010 (embedding provider abstraction, model provisional), ADR-0003 (read-only defense-in-depth), ADR-0005 (secrets via env/`*_FILE`, stderr-only logs), ADR-0015 (redaction / `scrub()`), ADR-0012 (ingest pipeline as CLI).
- **Deviation record** per `squad-baselines`. This ADR records what the CEO decided at Gate 1; it does not itself open egress or create a credential.

---

## 1. Context — the invariant being relaxed

Everything built so far (base 9-source + CHG-001) was validated under four locked invariants, two of which CHG-003 relaxes:

- **no-egress** — the running system makes no outbound network calls off the host (reranker/embedding run with `HF_HUB_OFFLINE=1`; real sources were stubbed: a demo HTTP stub for Confluence, `DeterministicFakeProvider` for embeddings, HuggingFace returned 403).
- **vendors=none** — platform-baseline "Approved vendors: none"; no external SaaS is reached or authenticated to at runtime.

CHG-003 removes the stub: it opens outbound network to a named external SaaS (`*.atlassian.net`), introduces a real external vendor (Atlassian), stores a credential (read-only API token + account email), and opens `huggingface.co` so a real embedding model can be downloaded — which makes NFR-003 measurable for the first time.

**Target, confirmed by the CEO:** Confluence **Cloud** at `https://tnexwm.atlassian.net` (flavor = Cloud, matches the locked baseline `confluence_flavor=Cloud`).

## 2. Baseline affected

| Baseline | Field | Before | After (if accepted) |
|---|---|---|---|
| platform-baseline | Approved vendors | none | **+ Atlassian** (Confluence Cloud, read-only API token) |
| platform-baseline | Egress allow-list | none (no-egress) | **+ configured source hosts for the ingest PULL path** (default-deny; `*.atlassian.net` first; GitLab / OpenSearch / Jira hosts as configured) **+ `huggingface.co`** for a one-time embedding-model download |
| business-baseline | Document sources | stubbed | **real ingestion from Confluence** (`tnexwm.atlassian.net`) as document source #1 |

The 9 MCP servers + Jira MCP keep their invariants (read-only-to-source, stdio, no new network port). Egress is for the **ingest pull** and the **model download** only; servers still answer the client over stdio.

## 3. Itemised deviation score (per `squad-baselines`, cap 100)

Measured against `2-gate1/plan-approval.md` (CHG-001 Option C, the CEO-approved live baseline; platform-baseline.md is still template-TBD, so per `squad-baselines` bootstrapping the Gate-1 approval is authoritative). Mirrors D-006.

| Deviation (hard) | Points | Invariant broken |
|---|---|---|
| Data leaving the company boundary (new egress) | 40 | no-egress — ingest pulls from `https://tnexwm.atlassian.net` over the public internet |
| New external vendor / service outside the baseline | 40 | vendors=none — Atlassian Cloud becomes a real external dependency the running system reaches + authenticates to, with a stored API token |
| Soft (CTO judgement) | +6 | High reuse (Confluence connector, ingest pipeline, pgvector store, read-only + stdio all stay exactly as built); the change is a configuration/operations boundary flip, not a code redesign — low architectural drift keeps soft points modest |
| **Total (cap 100)** | **≈86** | ≫ `DEVIATION_THRESHOLD_PCT = 10`; each 40-point hard deviation alone is far above threshold |

Escalation rules **1** (work drifts outside the approved option — Option C premise was no-egress) and **3** (new vendor Atlassian + egress + stored credential) fire → this is a CEO Gate-1 matter, which is why it was escalated and approved at Gate 1 (D-006, plan-approval.md CHG-003 section).

## 4. Business-invariant impact

No business invariant is violated. Read-only-to-source is preserved: ingest **reads** the source and **writes** only the internal corpus (`kb.*`) under role `mcp_ingest_rw` — it never mutates Confluence. The grounding gate (#2) and permission choke point (#1) from CHG-001 are unchanged. The relaxation touches only the platform data-boundary + vendor invariants recorded in §2.

## 5. In-baseline option considered (and why it is insufficient)

**"Stay stubbed"** — keep the demo HTTP stub + fake embedding provider, no egress, no vendor. This keeps vendors=none / no-egress intact but means the system never ingests a single real document and NFR-003 stays permanently unmeasurable. It directly refutes the CEO's stated goal (make the system real, Confluence first). There is no in-baseline way to reach a real external SaaS; the fork is binary — open the real boundary or stay stubbed — so no ≥3-option analysis applies (lean track, D-006).

## 6. Decision

Accept, scoped to the smallest possible relaxation:

### (a) Egress allow-list policy — default-deny

- The egress allow-list is **the configured source hosts for the ingest PULL path, and nothing else**. Default-deny: any outbound host not on the allow-list is refused.
- First host: `*.atlassian.net` (Confluence Cloud `tnexwm.atlassian.net`). The other ingestable source hosts (GitLab, OpenSearch, Jira) are added **only as configured** for their connectors; an unconfigured host is denied.
- `huggingface.co` is on the allow-list **only** for the controlled embedding-model download step (see (d)).
- The **9 MCP servers themselves make NO outbound call beyond their own upstream read API** and keep **stdio** — no MCP server opens a new network port. Egress is a property of the `mcp-ingest` pull path and the one-time model download, not of the servers answering the client.
- Enforcement is a single structural choke point on the ingest/model egress path with an adversarial test (L-001): a host not on the allow-list must be refused.

### (b) Atlassian as a named vendor

Atlassian Cloud (Confluence) is accepted as a real external vendor. No new **paid** tier is assumed — the existing tenant's read API is used; run-cost delta ≈ $0. If a paid tier turns out to be required, Rule 3 fires again on cost → back to the CEO.

### (c) Credential handling — least-privilege, read-only, env/`*_FILE` only

- A single **least-privilege, READ-ONLY** Atlassian Cloud API token for `tnexwm.atlassian.net` + the account email + minimal read scopes.
- Stored via **env or `*_FILE` only** (`MCP_CONFLUENCE_API_TOKEN` or `MCP_CONFLUENCE_API_TOKEN_FILE`, per ADR-0005 `*_FILE` convention — already supported in `mcp_common.config`). **Never committed** (`.env` / `*.env` are git-ignored; verified at the Oct-1 go-live), **never logged**.
- `scrub()` applies on **both directions** — the tool/result boundary and the error/log path (L-001 / E-003, ADR-0015). The token never appears in stdout, logs, or tool results.
- **`doctor` refuses a write-capable account**: the startup read-only check (ADR-0003 A1 / ADR-0007 A2) proves the token authenticates **and** cannot write, and `build_server()` refuses to serve otherwise. This reuses the already-built `ConfluenceClient.credential_check` (`packages/mcp_confluence/src/mcp_confluence/client.py`), which samples page `operations` and refuses if any write operation is permitted. No new mechanism is introduced for Confluence Cloud; the same pattern covers Jira. Escape hatch `MCP_ALLOW_UNVERIFIED_CREDENTIALS=true` logs WARN each start and must not be used for the real token.

### (d) HuggingFace model egress — separable, pinned

- Opening Atlassian does **not** open `huggingface.co`. The two egresses are **separable** sub-decisions at the same Gate 1 (the CEO approved both under Option B).
- Egress to `huggingface.co` is for the **controlled embedding-model download step only**: `HF_HUB_OFFLINE` flips from `1` to online **only** for that step, then back to offline for all serving. The running MCP servers stay offline.
- **Model pin:** ADR-0010 stays **provisional** (`BAAI/bge-m3`, 1024d, cosine) until the embedding bake-off (spike S2) can actually run — which it now can, because egress is open. CHG-003 **enables** the measurement; it does not finalise the model. ADR-0010 is promoted to accepted only after S2 runs on a real model and the number is measured (ties to NFR-003 / DK1 / D-002). Dimension stays 1024d so switching candidate models needs no vector-space migration.

### (e) Adversarial / invariant tests (to be built by backend, QA writes the contract)

Required, each at its own single choke point with an adversarial input (L-001):

1. **Egress default-deny proven** — with the allow-list set, an attempt to reach a host **not** on the list is refused (not silently allowed).
2. **Allow-list honoured** — a configured source host (`*.atlassian.net`) is reached; an un-configured host is denied.
3. **Token never leaks** — the token value never appears in logs, stdout, or any tool result (scrub both ways; adversarial: force an error on the credential path and assert the token is absent).
4. **Server read-only unchanged** — the 9 MCP servers + Jira stay read-only-to-source and stdio; no new listening socket; `doctor` refuses a write-capable Atlassian account.

## 7. Honest NFR-003 labelling (L-002)

Opening egress **enables** measurement of NFR-003 (semantic-retrieval quality); it does **not** by itself prove quality. Until the bake-off (S2) runs on real Confluence content with a real model and produces a measured number, NFR-003 stays **UNVERIFIED** and any retrieval metric must be labelled for exactly what it measures (ANN-index correctness ≠ semantic quality). No gate may read the proxy as proof.

## 8. Cost / risk

- **Build:** ≈1–3 agent-days — reuses the existing Confluence connector + `mcp-ingest` CLI; the real work is this ADR, the runbook, an egress allow-list guard + its adversarial test, and confirming the `doctor` read-only check covers Confluence Cloud live.
- **Run cost:** ≈ $0/month delta (existing Atlassian tenant; no new paid tier). HF model download is one-time bandwidth + local RAM/CPU (bge-m3 ≈ 2 GB).
- **Risks:** (i) a mislabelled/over-privileged token could read restricted content — mitigated by least-privilege read-only token + `doctor` refusing a write-capable account + corpus team-only default-deny (ADR-0016); (ii) egress widening beyond the ingest path — mitigated by default-deny allow-list + adversarial test; (iii) reading an unmeasured retrieval proxy as quality proof — mitigated by L-002 labelling (§7).

## 9. Recommendation

Accept Option B as approved: open `*.atlassian.net` for the ingest pull + accept Atlassian as a vendor + store a least-privilege read-only token (parts a–c), **and** open `huggingface.co` for the controlled model download (part d), with the four adversarial/invariant tests (part e) as a condition of done. Keep the two egresses separable in configuration so the Atlassian path can run even if the model download is deferred. Promote this ADR proposed → accepted once the egress allow-list guard + adversarial tests are green and the CEO's Gate-1 words are recorded in `plan-approval.md` (DM owns that write).
