# ADR-0020: Hybrid-RAG trong một Postgres — tsvector + pgvector + RRF + reranker local offline + context-compression

**Date**: 2026-10-01
**Status**: proposed — hình dạng chốt ở design; đóng accepted khi đo được chất lượng (NFR-003, chặn bởi egress HF)
**Deciders**: SA (squad-sa) đề xuất; CTO quyết trong scope CHG-001 Option C (ADR-0017)

## Context

CHG-001 Option C thay "semantic search đơn giản" của nền bằng **Hybrid-RAG đầy đủ**: cần
recall tốt cho **cả** truy vấn ngữ nghĩa (câu hỏi tự nhiên) **và** truy vấn từ khoá chính xác
(tên hàm, mã lỗi, định danh) — điểm yếu đã ghi ở R8/spike S3 của nền. Bất biến: **một store
Postgres** (spec §4.4), **không egress / vendors=none** (ADR-0017), **stdio in-process**
(NFR-005). Reranker và embedding phải **nạp offline** (`HF_HUB_OFFLINE=1`, ADR-0010 A1) vì
egress HuggingFace đang 403.

## Decision

Hybrid-RAG là một **subsystem in-process trong Postgres hiện có**, pipeline retrieval:

```
query → (a) vector: pgvector HNSW cosine (ADR-0011)   ─┐
        (b) keyword: tsvector + GIN, config `simple`   ─┤→ RRF hợp nhất (k=60)
        (c) metadata filter (source_type/container/updated/visibility) ┘
      → rerank: cross-encoder local bge-reranker-v2-m3 (offline; RRF-only fallback có cờ)
      → context-compression (cắt theo token-budget, GIỮ provenance — spec §13)
      → context-pack assembler  ← đây CŨNG là grounding gate B4 (ADR-0018), choke point DUY NHẤT
```

- **Keyword** = cột `tsvector` generated + GIN index, config `simple` (không stemming) để khớp
  mã lỗi/định danh; truy vấn `websearch_to_tsquery`.
- **Fusion** = Reciprocal Rank Fusion (`1/(k+rank)`, `k=60` mặc định config) — không cần chuẩn
  hoá điểm giữa hai không gian (cosine vs ts_rank).
- **Reranker** = `BAAI/bge-reranker-v2-m3` (Apache-2.0, cùng họ bge-m3), nạp **offline**; cờ
  `MCP_RAG_RERANKER_ENABLED` — tắt/weights thiếu ⇒ **RRF-only** + `reranker=disabled` trong
  envelope (minh bạch, không giả chất lượng — L-002). Chạy trên host CPU/GPU, **không API**.
- **Context-compression** cắt theo token-budget nhưng **không bao giờ nén mất provenance**
  trước grounding gate (ADR-0018 §6 GT-7).
- Toàn bộ **in-process** của runtime stdio; không service, không datastore mới.

## Alternatives Considered

### Alternative 1: Giữ semantic-only (như nền)
- **Pros**: Không thêm gì.
- **Cons**: Recall kém cho tra cứu từ khoá chính xác (R8) — không đáp ứng Must hybrid của spec §55.
- **Why not**: Option C duyệt hybrid đầy đủ.

### Alternative 2: Engine ngoài (Elasticsearch/OpenSearch BM25 + vector riêng)
- **Pros**: BM25 corpus-wide chuẩn.
- **Cons**: Datastore/service mới = deviation + operating burden cho công ty một người; phá "một store".
- **Why not**: Vi phạm bất biến "một Postgres"; không tương xứng.

### Alternative 3: Reranker API trả tiền (Cohere/Voyage/Jina)
- **Pros**: Chất lượng NDCG cao, không cần RAM/GPU.
- **Cons**: **egress + vendor trả phí** (phá vendors=none/no-egress, +40 deviation ADR-0017); dữ liệu nội bộ ra ngoài.
- **Why not**: CEO giữ vendors=none ở Gate 1; chỉ mở nếu CEO quyết riêng (không ở scope này).

### Alternative 4: Không rerank (chỉ RRF) ở v1
- **Pros**: Đơn giản, ít RAM.
- **Cons**: NDCG thấp hơn cross-encoder trên truy vấn khó.
- **Why not**: Giữ làm **fallback có cờ**, không làm mặc định; rerank local offline không thêm egress nên không có lý do bỏ.

## Consequences

### Positive
- Recall tốt cho cả hai loại truy vấn mà không rời Postgres/stdio/vendors=none.
- RRF-only fallback giữ hệ chạy khi weights chưa nạp (minh bạch qua envelope).

### Negative
- Reranker local = RAM/CPU host (~568M model) + độ trễ thêm mỗi truy vấn — nằm trong deadline tool (cần đo).
- Cột `tsvector` + GIN thêm dung lượng + chi phí ghi lúc ingest.

### Risks
- **Chất lượng chưa đo được** tới khi gỡ egress HF (NFR-003 UNVERIFIED, L-002/D-002) — ADR giữ `proposed`;
  không được đọc bất kỳ số nào là bằng chứng chất lượng tới khi eval trên golden-set thật.
- Rerank làm chậm → nếu vượt deadline, fallback RRF-only (cờ) + ghi warning.

## Links
- ADR-0010 (embedding/rerank local offline), ADR-0011 (pgvector schema/HNSW), ADR-0017 (CHG-001 Option C),
  ADR-0018 (grounding gate = context-pack assembler), ADR-0004 (envelope). Spec §13–14, §55. Lessons L-002.
