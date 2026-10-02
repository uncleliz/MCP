# Company Knowledge (CHG-001) — Market & Technology Research

> Feature: `mcp-data-platform` · Change: CHG-001 (Company Knowledge layer trên 9 nguồn read-only đã go-live)
> Mode: **full** (tier=large) · Author: squad-researcher · Date: 2026-10-01
> Scope: tìm bằng chứng cho 5 điểm quyết định mở để SA ra ≥ 3 options. **Không** khuyến nghị một lựa chọn
> duy nhất (việc của SA/PO). Mọi nhận định có nguồn `[n]`; thiếu nguồn → "unverified".

## Question(s) researched
1. **MCP Gateway — build vs buy**: có gateway/router/proxy MCP open-source, miễn phí nào làm auth / authz /
   policy / audit / rate-limit / routing cho nhiều MCP server, hợp với stdio-first + tương lai HTTP+SSE?
2. **Reranker — local vs API**: cross-encoder chạy local (bge-reranker, mxbai, Qwen3…) vs API (Cohere…),
   dưới ràng buộc egress HF 403 + read-only + chạy local.
3. **Hybrid retrieval trong Postgres**: RRF + full-text (`tsvector`) + pgvector trong một DB — Postgres làm
   được tới đâu so với engine ngoài?
4. **Relationship store**: bảng quan hệ Postgres + recursive CTE vs graph DB riêng (Apache AGE) cho
   entities/relationships.
5. **Jira ingestion**: thư viện/SDK đọc-only cho Jira (Cloud vs Server/DC), incremental sync bằng `updated`.

## Internal assets already available
Tài sản đã có trong repo/công ty có thể tái dùng (nguồn: đọc repo, không phải web):
- **pgvector store đã go-live** — schema `kb` (documents / chunks / versions / ingest_runs / ingest_source_state /
  ingest_failures), HNSW `vector_cosine_ops` (m=16, ef_construction=64), `vector(1024)`, upsert idempotent theo
  `content_hash`, tombstone, hai role `mcp_ingest_rw` / `mcp_query_ro`. [ADR-0011] — CHG-001 mở rộng schema này
  (versions/entities/relationships/summaries/permissions) thay vì dựng DB mới.
- **pgvector 0.8+ với iterative scan** đã bắt buộc trong code để chống false-negative của HNSW post-filter
  (`hnsw.iterative_scan = relaxed_order`, `ef_search = GREATEST(64, 8*top_k)`). [ADR-0011 A3] — nền sẵn cho
  metadata-filter + hybrid.
- **Embedding provider abstraction** — port `EmbeddingProvider` (`local` SentenceTransformer | `http`
  OpenAI-compatible), mặc định **tạm thời** `BAAI/bge-m3` 1024d; chưa đo được vì egress HF bị chặn. [ADR-0010, ADR-0010 A1]
- **Read-only defense-in-depth 5 lớp** + startup credential proof + transport-level GET/HEAD assertion
  (`mcp_common.http`). [ADR-0003, A1, A2] — mọi tool mới của CHG-001 phải đi qua đúng choke point này (L-001).
- **Thin REST read-only client pattern** cho Confluence & GitLab (`httpx.AsyncClient`, allowlist endpoint, raise
  `NotPermittedError` với method ≠ GET/HEAD), **cố ý KHÔNG dùng** `atlassian-python-api`/`python-gitlab` vì chúng
  mang bề mặt ghi vào process. [ADR-0007] — tiền lệ trực tiếp cho Jira client (DP5).
- **Live MCP đã có** cho 9 nguồn (GitLab/Confluence…); Live Jira sẽ nối vào cùng khuôn `read_api`/`client` split. [ADR-0007 A3]
- **Spike S3 hybrid-search** — đã viết quy trình đo RRF + `tsvector` + ước lượng công ~4 ngày, **nhưng chưa đo**
  (thiếu corpus thật + model thật). [S3] — DP3 kế thừa trực tiếp.
- **MCP transport**: stdio-only (NFR-005), MCP Python SDK. [ADR-0002] — ràng buộc cho DP1.
- **Confluence flavor = Cloud** (chốt Gate B); **Kafka client** confluent-kafka; corpus hiện team-only (chưa RBAC
  theo người dùng, ADR-0016 Part 2). [state.json decisions] — ảnh hưởng DP1 (server-side permission) & DP5.

## Landscape

### DP1 — MCP Gateway (auth/authz/policy/audit/rate-limit/routing cho nhiều MCP server)
| Giải pháp | Type | Fit Must-have (gateway responsibilities §6 spec) | Cost | Licence | Maturity (as of 2026-10-01) | Security posture | Nguồn |
|---|---|---|---|---|---|---|---|
| **IBM ContextForge (mcp-context-forge)** | OSS | Cao: federation nhiều MCP server, auth Basic/JWT/SSO, RBAC, rate-limit, retries, audit log viewer, OTel tracing, virtual servers, tool discovery | Miễn phí | **Apache-2.0** | ~4.6k★, 892 forks, 3,243 commits, 7,000+ test, release 1.0.0-RC; maintainer chính IBM (Mihai Criveti) | SSRF guard (fail-closed), JWT bắt buộc secret, secure-by-default flags, content size limit; airgapped mode | [1] |
| **springai-mcp-gateway (oalles)** | OSS | TB: hợp nhất nhiều MCP server sau một endpoint, OAuth 2.1 Authorization Server (JWT) | Miễn phí | kiểm trên repo (unverified) | nhỏ hơn ContextForge; Spring Boot/Java | OAuth 2.1 | [2] |
| **mcp-gateway (matthisholleville)** | OSS | TB: proxy có middleware auth/authz/rate-limit/observability | Miễn phí | kiểm trên repo (unverified) | cộng đồng nhỏ | middleware permission | [3] |
| **mcp-auth-proxy (sigbit)** | OSS | Hẹp: chỉ OAuth 2.1/OIDC drop-in trước MCP server (không routing/RRF) | Miễn phí | kiểm trên repo (unverified) | nhỏ | OAuth 2.1/OIDC | [4] |
| **Build riêng (gateway nội bộ)** | Build | Khớp 100% stdio-only + read-only choke point hiện có; chỉ làm đúng phần cần | Công sức nội bộ | n/a (nội bộ) | kế thừa `mcp_common` đã có | tái dùng ADR-0003 defense-in-depth | [ADR-0003, ADR-0002] |

Lưu ý fit: spec §43/§6 đòi gateway làm SSO → identity → permission filter server-side. **Tất cả OSS gateway trên
đều HTTP/SSE-first** (ContextForge nói "stdio transport available for server-side use" nhưng mô hình vận hành là
một service HTTP :4444) [1]. Điều này **mâu thuẫn với NFR-005 stdio-only** và chính Alternative 3 của ADR-0003 đã
bác "proxy read-only chung trước mọi nguồn" vì phá vỡ stdio + thêm SPOF [ADR-0003]. SA phải cân: giữ bất biến
stdio (build gateway mỏng trong-process / thư viện) vs chấp nhận một service HTTP mới (ContextForge) khi spec dự
kiến tương lai HTTP+SSE.

### DP2 — Reranker (local vs API)
| Model | Type | Fit (multilingual VI-EN, chạy local, read-only) | Cost | Licence | Maturity | Egress/security | Nguồn |
|---|---|---|---|---|---|---|---|
| **BAAI/bge-reranker-v2-m3** | OSS self-host | Cao: cross-encoder đa ngữ, cùng họ bge-m3 đang dùng cho embedding | Free (GPU/CPU nội bộ) | **Apache-2.0** | 568M, mạnh trên MIRACL/MTEB | **Trọng số tải từ HF → egress 403 đang chặn**; chạy local sau khi có weights | [5][6] |
| **mixedbread mxbai-rerank-large-v2** | OSS self-host (+API tùy chọn) | Cao: Apache-2.0 đa ngữ | Free self-host; API metered | **Apache-2.0** | ~2B param; có base/xsmall | Weights từ HF; API là egress ngoài | [5] |
| **Qwen3-Reranker (0.6B/4B/8B)** | OSS self-host | Cao: đa ngữ (CN-EN mạnh), nhiều cỡ chọn RAM | Free | **Apache-2.0** | release 2025-06, họ Qwen3/Alibaba | Weights từ HF | [5] |
| **ColBERTv2** | OSS self-host | TB: late-interaction, **base English-only**; index per-token 30–100× lớn hơn | Free | **MIT** | chuẩn late-interaction, RAGatouille | Weights từ HF | [5] |
| **Jina Reranker v2 base multilingual** | Weights NC / API | Khớp code+đa ngữ **nhưng** weights **CC-BY-NC-4.0** → dùng thương mại phải qua API | API metered | **CC-BY-NC-4.0 (weights)** | nhanh, function-calling | API = egress ngoài; **non-commercial** | [5] |
| **Cohere Rerank 4 (Fast/Pro)** | SaaS API | Khớp chất lượng/đa ngữ, không cần GPU | Trả tiền/search | **Closed** | hosted; Bedrock liệt Rerank 3.5 | **Gửi query+doc ra API ngoài** → vi phạm chính sách dữ liệu nội bộ + egress; vendors=none | [5] |
| **Voyage rerank-2.5** | SaaS API | Khớp enterprise search | Trả tiền/token | **Closed** | MongoDB mua Voyage 02/2025 | Egress ngoài | [5] |

### DP3 — Hybrid retrieval trong Postgres (RRF + tsvector + pgvector)
| Cách làm | Type | Fit | Cost | Licence | Maturity | Nguồn |
|---|---|---|---|---|---|---|
| **tsvector + pgvector + RRF trong 1 Postgres** | Pattern/OSS | Cao: không hạ tầng mới, đúng store hiện có; RRF chỉ cần rank nên khỏi chuẩn hoá điểm BM25 vs cosine | Miễn phí | Postgres/pgvector OSS | pattern phổ biến 2025–26; nhiều hướng dẫn 1-query | [7][8][9][11][12] |
| **pgvector iterative index scan (≥ 0.8) cho hybrid** | OSS | Cần cho leg vector khi có filter — **đã bật trong code hiện tại** | Miễn phí | pgvector OSS | yêu cầu pgvector ≥ 0.8.0 | [12][ADR-0011 A3] |
| **ParadeDB (pg_search, BM25 thật)** | OSS extension | Cao hơn về ranking: `ts_rank` chỉ xét per-document, không có corpus-wide BM25 statistics | Miễn phí | kiểm licence repo (unverified) | extension Postgres, cộng đồng tăng | [10] |
| **Engine ngoài (OpenSearch/Elastic đã có cho log)** | Hạ tầng có sẵn | TB: tách store, phá nguyên tắc "Postgres là knowledge store" (spec §4.4) | — | — | OpenSearch đã nằm trong 9 nguồn | [spec §4.4] |

Cảnh báo chất lượng ranking: native `tsvector`/`ts_rank` chỉ xét từng document cô lập, **không** có thống kê
corpus-wide như BM25 — đủ cho khớp định danh/mã lỗi (dùng cấu hình `simple`, không stemming) nhưng không phải
BM25 thực thụ [10][S3].

### DP4 — Relationship store (recursive CTE vs Apache AGE)
| Cách làm | Type | Fit (entities/relationships §21–22, traversal nông 2–3 hop) | Cost | Licence | Maturity | Nguồn |
|---|---|---|---|---|---|---|
| **Bảng quan hệ Postgres + recursive CTE** | Pattern/OSS | Cao: graph nông/nhỏ trả về đơn vị ms; typed rows, không impedance mismatch; không hạ tầng mới | Miễn phí | Postgres OSS | WITH RECURSIVE trong chuẩn SQL từ 1999; dùng rộng cho org-chart/graph | [13][14][15][16] |
| **Apache AGE (openCypher trên Postgres)** | OSS extension | TB-Cao cho traversal sâu/nhiều hop; nhưng Cypher-trong-SQL trả untyped, thêm lớp parse | Miễn phí | kiểm licence repo (Apache; unverified cụ thể) | extension bên thứ ba; SQL/PGQ chuẩn đang được bàn | [16][17] |
| **pg_igraph / extension graph** | OSS extension | Nhanh hơn CTE ~3.6× full-traversal cây 335k node; 21.5× shortest-path chuỗi 100k | Miễn phí | kiểm licence (unverified) | benchmark cộng đồng | [18] |
| **Graph DB riêng (Neo4j)** | Hạ tầng mới | Chỉ lợi khi graph sâu/lớn; thêm store = phá "Postgres là store" + vendors=none | Có bản free/trả tiền | GPL/commercial (Neo4j) | trưởng thành | [15] |

Bằng chứng quy mô: hướng dẫn 2025–26 đều kết luận graph nông 2–3 hop hoặc nhỏ thì recursive CTE + index trả
single-digit ms, "PostgreSQL đã có graph engine" [13][14][15]; graph DB riêng chỉ thắng rõ khi traversal rất sâu
hoặc shortest-path trên trăm nghìn node [18][9]. Entities/relationships của spec (service → depends_on → service,
documented_by, related_to) là **nông** → CTE đủ cho V1, AGE là phương án khi chứng minh cần.

### DP5 — Jira ingestion (read-only, Cloud vs Server/DC, incremental)
| Cách làm | Type | Fit | Cost | Licence | Maturity | Nguồn |
|---|---|---|---|---|---|---|
| **Thin REST client tự viết (theo ADR-0007)** | Build | Cao: cùng khuôn Confluence/GitLab, bề mặt ghi = 0, dùng `mcp_common.http` + allowlist | Công nội bộ | n/a | tiền lệ đã go-live | [ADR-0007] |
| **Jira Cloud REST — Search for issues using JQL (GET)** | API nguồn | Cao cho Cloud: incremental bằng `updated >= last_run`; **phân trang `nextPageToken`** (đã thay `startAt` deprecated) | Free (API) | API Atlassian | hiện hành 2025–26 | [19][20][21] |
| **`atlassian-python-api` / `jira` SDK** | OSS SDK | TB: nhanh nhưng **mang method ghi vào process** → mâu thuẫn ADR-0007 Alt 1 | Free | kiểm licence (unverified) | trưởng thành | [ADR-0007] |
| **FetchJiraIssues (NiFi processor) làm tham chiếu pattern** | OSS tham chiếu | Tham chiếu incremental: state cursor theo timestamp, basic auth email/API token; **không phát hiện xoá** | Free | Apache NiFi | 2025.10 | [22] |

Ràng buộc Cloud quan trọng: (a) API search là **eventually-consistent / read-optimized view có lag** →
incremental theo `updated` có thể trả issue "stale" hoặc trùng ở ranh giới trang [23][24][26]; Atlassian có
`reconcileIssues`/Search-and-Reconcile để giảm [19]. (b) `max_result_window` mặc định 10,000 → phải phân trang
bằng token, không nhảy offset lớn [25]. (c) Server/DC dùng `startAt` cũ còn Cloud dùng `nextPageToken` → client
phải tách theo flavor giống Confluence Cloud vs Server/DC [20][ADR-0007]. (d) **Xoá ở nguồn không có trong feed
`updated`** → cần full-reconcile như tombstone của pgvector hiện tại [22][ADR-0011 A1].

## Shortlist (3–5) với evidence
- **DP1**: (1) ContextForge Apache-2.0 — OSS trưởng thành nhất, đủ auth/RBAC/rate-limit/audit/OTel [1]; (2)
  Build gateway mỏng in-process giữ stdio + tái dùng ADR-0003 [ADR-0002/0003]; (3) sigbit mcp-auth-proxy nếu chỉ
  cần auth drop-in [4]. Trade-off trục chính: **stdio-only vs service HTTP mới**.
- **DP2**: (1) bge-reranker-v2-m3 Apache-2.0, cùng họ embedding đang dùng [5][6]; (2) Qwen3-Reranker Apache-2.0
  đa cỡ [5]; (3) mxbai-rerank-v2 Apache-2.0 [5]. Cả ba **đều phải tải weights từ HF (đang 403)** → tải offline.
  API (Cohere/Voyage/Jina) **loại trừ mềm** vì egress + vendors=none + chính sách dữ liệu nội bộ.
- **DP3**: (1) tsvector + pgvector + RRF một DB (kế thừa S3) [7][8][9][12]; (2) thêm ParadeDB pg_search nếu cần
  BM25 thật [10]; (3) hướng dẫn Claude dùng `gitlab_search_code`/`opensearch_search_logs` cho tra cứu chính xác
  (phương án "không thêm hybrid" của S3) [S3].
- **DP4**: (1) recursive CTE trên bảng relationships [13][14][15]; (2) Apache AGE khi cần traversal sâu [16][17];
  (3) pg_igraph nếu đo thấy CTE chậm [18]. V1 nông → (1) là baseline.
- **DP5**: (1) thin REST client Jira Cloud GET + JQL `updated` + `nextPageToken` [19][20]; (2) tách flavor
  Cloud/Server-DC [20][ADR-0007]; (3) full-reconcile cho xoá [22][ADR-0011].

## Key constraints discovered
- **Egress HF bị chặn 403** (S2): mọi reranker local (bge/mxbai/Qwen3/ColBERT) cần nạp weights offline
  (`HF_HUB_OFFLINE=1`) — nếu không, DP2 bị chặn y hệt embedding bake-off S2 [ADR-0010 A1].
- **Read-only tuyệt đối** (BR-001/NFR-001, L-001): tool mới của Knowledge/Live Jira phải qua một choke point có
  adversarial test; "server-side permission" (spec §24/§43) phải enforce trước khi lắp context pack, không dựa
  Claude [ADR-0003][spec §43].
- **stdio-only (NFR-005)**: gateway OSS HTTP-first xung đột; ADR-0003 Alt 3 đã bác proxy chung vì phá stdio + SPOF.
- **vendors=none** (platform-baseline): bất kỳ API trả tiền (Cohere/Voyage/Jina API) là **deviation** cần ADR +
  CEO approve ở Gate 1.
- **Licence copyleft/NC**: Jina reranker weights **CC-BY-NC-4.0** → không deploy trong sản phẩm thương mại nếu
  không qua API [5]; Neo4j (GPL/commercial) [15]. Flag để SA loại sớm.
- **pgvector ≥ 0.8** bắt buộc cho hybrid có filter (iterative scan) — đã có [12][ADR-0011 A3].
- **Số chiều 1024 khoá cứng DDL** → đổi model embedding/reranker không đổi chiều lưu; reranker không ghi vector
  nên không ảnh hưởng schema [ADR-0010][ADR-0011].
- **Jira Cloud eventual consistency + nextPageToken + no-delete-in-feed** → incremental phải chịu lag, phân trang
  token, và reconcile xoá riêng [19][22][25][26].

## Build-vs-buy observations
- **DP1 Gateway**: "buy/adopt" ContextForge có sẵn mọi responsibility spec §6 với Apache-2.0 + maturity cao [1],
  nhưng là **service HTTP** → tốn một bất biến kiến trúc (stdio-only) và thêm SPOF. "Build" mỏng giữ bất biến và
  tái dùng ADR-0003 nhưng phải tự viết auth/audit/rate-limit. Đây là trade-off kiến trúc, không phải chi phí tiền.
- **DP2 Reranker**: "build/self-host" là mặc định bắt buộc theo chính sách (local + no-egress + Apache-2.0 có
  sẵn); "buy" (API) bị vendors=none + egress chặn → chỉ là phương án đối chứng.
- **DP3/DP4**: nghiêng "build trên Postgres" vì store đã có, không hạ tầng mới (spec §4.4). Extension (ParadeDB,
  AGE, pg_igraph) là nước đi giữa khi đo thấy giới hạn.
- **DP5 Jira**: "build thin client" là tiền lệ đã accepted (ADR-0007) — SDK đầy đủ bị loại vì bề mặt ghi.

## Sources
1. IBM ContextForge (mcp-context-forge) — GitHub README (Apache-2.0, auth/RBAC/rate-limit/OTel, stdio+SSE+streamable HTTP, ~4.6k★, 7000+ tests). https://github.com/IBM/mcp-context-forge — truy cập 2026-10-01.
2. oalles/springai-mcp-gateway — Spring Boot MCP gateway, OAuth 2.1. https://github.com/oalles/springai-mcp-gateway — 2026-10-01.
3. matthisholleville/mcp-gateway — proxy gateway, middleware auth/authz/rate-limit/observability. https://github.com/matthisholleville/mcp-gateway — 2026-10-01.
4. sigbit/mcp-auth-proxy — OAuth 2.1/OIDC drop-in proxy cho MCP. https://github.com/sigbit/mcp-auth-proxy — 2026-10-01.
5. "Best Rerankers for RAG 2026: 7 Models Compared" — futureagi.com (licence/size/multilingual: bge-v2-m3 Apache-2.0 568M, mxbai Apache-2.0, Qwen3 Apache-2.0, ColBERTv2 MIT English-only, Jina CC-BY-NC-4.0, Cohere/Voyage closed). https://www.futureagi.com/blog/best-rerankers-for-rag-2026/ — 2026-10-01.
6. BAAI/bge-reranker-v2-m3 model card (Apache-2.0, multilingual, 568M, teacher cho distilled students). https://huggingface.co/BAAI/bge-reranker-v2-m3 — 2026-10-01.
7. "Building Hybrid Search for RAG: pgvector + FTS + RRF" — dev.to. https://dev.to/lpossamai/building-hybrid-search-for-rag-combining-pgvector-and-full-text-search-with-reciprocal-rank-fusion-6nk — 2026-10-01.
8. "Hybrid Search in 100 Lines: BM25 + pgvector with RRF Merge" — dev.to. https://dev.to/gabrielanhaia/hybrid-search-in-100-lines-bm25-pgvector-with-rrf-merge-58cn — 2026-10-01.
9. "Hybrid retrieval in one Postgres query: RRF over tsvector + pgvector" — dev.to (RRF chỉ cần rank, khỏi chuẩn hoá). https://dev.to/vinay_kumarks_9d8ca4e45/hybrid-retrieval-in-one-postgres-query-rrf-over-tsvector-pgvector-2hm — 2026-10-01.
10. "Hybrid Search in PostgreSQL: The Missing Manual" — paradedb.com (giới hạn ts_rank vs BM25 corpus-wide). https://www.paradedb.com/blog/hybrid-search-in-postgresql-the-missing-manual — 2026-10-01.
11. timescale/pg-aiguide — postgres-hybrid-text-search SKILL (BM25 + vector + RRF). https://github.com/timescale/pg-aiguide/blob/main/skills/postgres-hybrid-text-search/SKILL.md — 2026-10-01.
12. EnterpriseDB — Hybrid search (pgvector iterative index scans cần ≥ 0.8.0; FTS không cần extension). https://www.enterprisedb.com/docs/aidb/latest/knowledge-bases/hybrid-search/ — 2026-10-01.
13. "Recursive CTEs: SQL's Hidden Graph Traversal Engine" — towardsdatascience.com. https://towardsdatascience.com/six-degrees-of-sql/ — 2026-10-01.
14. "Building a personal knowledge graph with just PostgreSQL (no Neo4j)" — dev.to (CTE typed rows vs AGE untyped Cypher). https://dev.to/micelclaw/4o-building-a-personal-knowledge-graph-with-just-postgresql-no-neo4j-needed-22b2 — 2026-10-01.
15. "Neo4j vs PostgreSQL: Pick the Right Store for Graph Data" — markaicode.com (graph nông 2–3 hop → CTE single-digit ms). https://markaicode.com/vs/neo4j-vs-postgres/ — 2026-10-01.
16. "Representing graphs in PostgreSQL with SQL/PGQ" — enterprisedb.com (Apache AGE là extension bên thứ ba; SQL/PGQ đang bàn). https://www.enterprisedb.com/blog/representing-graphs-postgresql-sqlpgq — 2026-10-01.
17. Apache AGE (nhắc trong [16]) — openCypher trên Postgres; cần kiểm licence/maturity cụ thể trên trang dự án (unverified).
18. "Your PostgreSQL Already Has a Graph Engine" — dev.to (pg_igraph ~3.6× / 21.5× nhanh hơn CTE ở quy mô lớn). https://dev.to/ineron/your-postgresql-already-has-a-graph-engine-you-just-have-to-build-it-2ng7 — 2026-10-01.
19. Atlassian — Search and Reconcile (Jira Cloud; reconcileIssues, consistency). https://developer.atlassian.com/cloud/jira/platform/search-and-reconcile/ — 2026-10-01.
20. Atlassian KB — Run JQL search query using Jira Cloud REST API (GET; nextPageToken thay startAt deprecated). https://confluence.atlassian.com/jirakb/run-jql-search-query-using-jira-cloud-rest-api-1289424308.html — 2026-10-01.
21. Atlassian Community — Best practices High-Volume export (incremental `updated >= last_run_date`). https://community.atlassian.com/forums/Jira-questions/Best-practices-for-High-Volume-Full-History-Data-Export-Issues/qaq-p/3183029 — 2026-10-01.
22. Snowflake Openflow — FetchJiraIssues processor (incremental timestamp cursor, basic auth email/API token, **không phát hiện xoá**). https://docs.snowflake.com/en/user-guide/data-integration/openflow/processors/fetchjiraissues — 2026-10-01.
23. Atlassian dev community — How new API search/jql works regarding data consistency (replication lag). https://community.developer.atlassian.com/t/how-new-api-search-jql-works-regarding-data-consistency/95158 — 2026-10-01.
24. Atlassian support — Inconsistent paginated API search result while using JQL (trùng kết quả ở ranh giới trang). https://support.atlassian.com/jira/kb/inconsistent-paginated-api-search-result-while-using-jql/ — 2026-10-01.
25. Atlassian support — Handling JQL REST API searching exceeding max_result_window (mặc định 10,000). https://support.atlassian.com/jira/kb/handling-jql-rest-api-searching-exceeding-max_result_window-in-jira/ — 2026-10-01.
26. Atlassian Community — Data Inconsistency in nextPageToken-based Pagination. https://community.atlassian.com/forums/Jira-questions/Issue-with-Data-Inconsistency-in-nextPageToken-based-Pagination/qaq-p/3066609 — 2026-10-01.

Repo-internal (không đánh số web): ADR-0002 (stdio), ADR-0003 (read-only defense-in-depth), ADR-0007 (thin REST
clients), ADR-0010 (embedding abstraction + HF 403), ADR-0011 (pgvector schema/HNSW/iterative scan), ADR-0016
(visibility/future RBAC), spike S3 (hybrid-search), state.json (Gate B decisions), lessons L-001/L-002/L-003.

## Confidence & gaps
- **Cao**: licence rerankers [5][6]; ContextForge features/licence/maturity [1]; khả năng Postgres làm RRF+FTS+
  vector một DB [7][8][9][11][12]; recursive CTE đủ cho graph nông [13][14][15]; Jira Cloud GET+JQL incremental +
  nextPageToken + no-delete-in-feed [19][20][22][25][26].
- **Trung bình / cần verify**:
  - Licence/maturity chính xác của springai-mcp-gateway, matthisholleville/mcp-gateway, mcp-auth-proxy, Apache AGE,
    pg_igraph, ParadeDB — mỗi mục đánh dấu "kiểm trên repo (unverified)"; SA/BE phải mở repo xác nhận SPDX trước khi
    đưa vào option. [2][3][4][10][17][18]
  - **ContextForge chạy stdio-only tới đâu** (README nói "available for server-side use" nhưng mô hình là HTTP
    :4444) — cần spike nhỏ xác nhận có thể nhúng không mở cổng mạng, nếu không thì vi phạm NFR-005. [1]
  - **Số đo thật DP2/DP3 vẫn UNVERIFIED**: chưa có corpus thật + model thật (egress HF 403); mọi recall/latency của
    reranker và hybrid là chưa đo — kế thừa đúng gap của S2/S3 và L-002 (đừng đọc proxy như NFR). [S2][S3][ADR-0010 A1]
  - Chi phí API Cohere/Voyage/Jina **không ghi** vì chính sách vendors=none loại trừ mềm; nếu CEO cho phép
    deviation, PO phải lấy giá "as of" trực tiếp từ trang pricing.
- **Cách verify**: mở từng repo kiểm `LICENSE`/`CHANGELOG`/last-release; spike nhúng ContextForge stdio; sau khi gỡ
  egress HF (hoặc nạp offline) chạy lại S2 bake-off + S3 hybrid-measure + một bake-off reranker trên bộ câu hỏi
  NFR-003 thật; đo CTE vs AGE trên relationship graph thật của team.

```
HANDOFF
feature: mcp-data-platform
mode: full
status: done
artifacts: [market-research.md]
shortlist: [IBM-ContextForge, build-thin-gateway, mcp-auth-proxy, bge-reranker-v2-m3, Qwen3-Reranker, mxbai-rerank-v2, tsvector+pgvector+RRF, ParadeDB-pg_search, recursive-CTE, Apache-AGE, jira-thin-rest-client]
sources: 26
gaps:
  - Licence/maturity chưa xác minh của springai-mcp-gateway, matthisholleville/mcp-gateway, mcp-auth-proxy, Apache AGE, pg_igraph, ParadeDB (mo repo kiem SPDX)
  - ContextForge co chay stdio-only khong mo cong mang khong (NFR-005) — can spike nhung
  - So do that reranker (DP2) & hybrid (DP3) van UNVERIFIED do egress HF 403 + chua co corpus/model that (ke thua S2/S3, L-002)
  - Gia API Cohere/Voyage/Jina chua ghi (vendors=none loai tru mem; chi lay neu CEO cho deviation o Gate 1)
```
