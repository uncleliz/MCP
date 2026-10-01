# CAB approval — mcp-data-platform

- **Decision:** APPROVED (go-live)
- **Approved by:** CEO
- **At:** 2026-10-01T18:28+07:00
- **Scope:** 9 read-only MCP servers (Confluence, GitLab, OpenSearch, Kibana, CloudWatch,
  Kafka, Redis, SQS/SNS, Postgres+pgvector) + ingest/embedding pipeline, per-user local stdio.
- **Target environment:** local (CEO's machine) — Docker Compose infra + MCP servers over stdio.
  This architecture has no shared hosted prod; "go-live" = the local deployment the CEO runs.
- **Go-live ref:** branch claude/zealous-johnson-yb3t2q @ 0363fcc, tag release/mcp-data-platform-20261001.
- **CEO words:** "hãy deploy trên docker local và demo cho tôi" — approve go-live, deploy on local
  Docker, then demo.

## Risk acceptance (CTO D-002 conditions)
- **ĐK1 (accepted):** CEO accepts shipping v1 with NFR-003 semantic quality UNVERIFIED (recall 0.95 is
  ANN-index correctness under the fake provider; real measurement blocked by HF egress + ADR-0010).
  To be measured during CHG-001 when egress/model are resolved.
- **ĐK2:** fix R-006/R-007 migration-locking before CHG-001 epic E1 (next schema change on populated data).
- **ĐK3:** Redis dev ACL (R-013) never reused for staging/shared.
- **ĐK4:** follow-ups R-005-secondary / R-012 / R-016.
- Smoke + 30-min observation must be green post cut-over; any rollback trigger → immediate rollback.

## Source: 7-release/cab-pack.md, 8-gate2/gate-brief.md, records/decisions.md (D-002)
