# Plan approval — CHG-001 Company Knowledge (CEO Gate 1)

- **Decision:** APPROVED — Option C (full recommendation accepted)
- **Approved by:** CEO
- **At:** 2026-10-01T22:10+07:00
- **Source:** 1-discovery/decision-brief.md, 1-discovery/options.md, docs/adr/0017-chg001-company-knowledge-deviation.md

## CEO words
"tôi đồng ý toàn bộ khuyến nghị" — approve Option C with all four recommended answers.

## What is approved (Option C)
Build the Company Knowledge layer on top of the live 9-source read-only base, **keeping NFR-005 stdio-only**:
- Company MCP gateway-boundary **in-process** (keep stdio; HTTP+SSE deferred to v1.1).
- Jira as source #10 — thin REST read-only client (ADR-0007), Cloud/Server flavor split.
- Hybrid-RAG in a single Postgres: tsvector + pgvector + RRF + metadata, inherit spike S3.
- Reranker: **local cross-encoder bge-reranker-v2-m3 loaded offline** (`HF_HUB_OFFLINE=1`); RRF-only fallback flag. NO paid API (vendors=none kept).
- Relationship store: recursive CTE in Postgres (not a separate graph DB).
- Document versioning, entities/relationships, knowledge summaries.
- **Permission enforced server-side** before context assembly (spec §24/§43), one choke point + adversarial test (L-001).

## The four Gate-1 decisions (all accepted as recommended)
1. **Option / DP1-NFR-005:** C — gateway in-process, **keep stdio**.
2. **DP2 reranker:** local offline (no paid-API deviation).
3. **NFR-003:** accept UNVERIFIED until measured within CHG-001 (E3, after HF egress cleared), continuing DK1/D-002.
4. **Baseline inheritance:** YES — amend baselines with Jira (source #10), Hybrid-RAG + local reranker, gateway-as-boundary in-process, 4 new Postgres data domains, server-side permission (inverting prior C6/BR-003 no-RBAC assumption).

## Deviation / ADR
- Deviation 100/100 accepted at Gate 1. ADR-0017 (deviation) to move proposed → accepted by SA.
- Carry retro follow-ups: DK2 (fix migration-locking R-006/R-007 before epic E1), DK3 (Redis dev ACL never reused for shared).

## Note — CEO enhancement request (CHG-002, separate)
At the same message the CEO proposed a broader 10-building-block model (Ingestion, Knowledge Model, RAG,
Grounding, Memory, State, Provenance, Governance, Evaluation, Observability) + Orchestrator/Harness, phased 1–4,
and asked to research + assess feasibility. That is recorded separately as CHG-002 (see records/decisions.md) and
does NOT change this CHG-001 approval; CHG-001 Option C proceeds, CHG-002 goes through its own research → Gate 1.


---

# Plan approval — CHG-003 (real ingestion + egress, CLI-driven, 9-source runbook)

> CEO duyệt Gate 1 ngày 2026-10-02, **Option B**. CTO sizing: D-006 (deviation ~86/100, lean track).
> Nguồn: records/decisions.md D-006; gate-brief CHG-003 ở 2-gate1/gate-brief.md (phần CHG-003).

## CEO quyết (nguyên văn ý định)
Hoãn go-live CHG-001; cài đặt **ingestion thật** + **mở egress thật**, giao tiếp qua **CLI**, có **hướng dẫn tích hợp cả 9 source**, **ưu tiên Confluence trước**. Chọn **Option B**: mở egress Atlassian **và** mở luôn egress HuggingFace để tải model embedding thật.

## Phạm vi được duyệt
1. **Mở egress ra `*.atlassian.net`** cho đường **ingest pull** tới Confluence Cloud `https://tnexwm.atlassian.net`. Chấp nhận **Atlassian là vendor thật**.
2. **Mở egress ra `huggingface.co`** để tải model embedding thật (gỡ chặn đo **NFR-003**; nối DK1/D-002/ADR-0010).
3. **Credential**: 1 **API token read-only least-privilege** cho `tnexwm.atlassian.net` (+ email tài khoản), lưu qua **env / *_FILE**, **không commit, không log**, scrub() cả 2 chiều (L-001/E-003); `doctor` từ chối tài khoản có quyền ghi.
4. **Runbook CLI tích hợp 9 source, Confluence trước**:
   - *Ingestable (4)*: Confluence, GitLab, OpenSearch, Jira → `mcp-<src> doctor` → `mcp-ingest run` → `status` → verify bằng `kb_semantic_search`.
   - *Live-only (5)*: CloudWatch, Kibana, Kafka, Redis, SQS/SNS → `mcp-<src> doctor` → đăng ký `claude_desktop_config.json` → `tools/list` smoke. "Tích hợp" = reachable + read-only + registered, **không** ingest.

## Bất biến vẫn giữ (không được nới)
- 9 MCP server **read-only-to-source** + **stdio** (NFR-005), không mở port mạng mới.
- Egress chỉ mở cho **ingest pull** + **tải model**; server trả lời client vẫn qua stdio.
- Permission choke point #1 + grounding gate #2 (CHG-001) **không đổi**.
- Token read-only; ingest ghi **chỉ** vào `kb.*` dưới role `mcp_ingest_rw`.

## Deviation & escalation
- Deviation ~86/100 (2 hard deviation: no-egress, vendors=none). Rule 1+3 fire → đã escalate và CEO duyệt.
- Track = **lean** (năng lực đã build+review; chỉ nới ranh giới policy qua ADR-deviation, không cần ≥3 option).

## Baseline sẽ cập nhật sau go-live
- platform-baseline: vendors += Atlassian (Confluence Cloud, read-only token); egress allow-list += `*.atlassian.net`, `huggingface.co` (ingest/model path).
- business-baseline: ingestion thật từ Confluence (nguồn tài liệu #1).

## Next
squad-sa (mode design) → ADR-deviation CHG-003 (egress allow-list + Atlassian vendor + credential handling + HF model egress) + runbook 9 source → BA/lead/qa-plan → backend → verify → review → go-live (Gate 2 mới, vì CHG-001 cab-approval cũ không phủ change này).
