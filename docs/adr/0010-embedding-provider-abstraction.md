# ADR-0010: Embedding provider abstraction + model/dimension mặc định

**Date**: 2026-10-01
**Status**: proposed — **cần user xác nhận ở Gate B** (provider & model)
**Deciders**: SA (squad-sa) đề xuất; PO/user quyết (liên quan Open question 5 — chi phí/độ mới)

## Context

FR-012 cần sinh embedding cho nội dung crawl từ Confluence/GitLab/OpenSearch; FR-011 cần truy
vấn similarity trên cùng không gian vector. Hai họ lựa chọn: model local (sentence-transformers)
và API thương mại (OpenAI, Voyage, Cohere, AWS Bedrock Titan). Ràng buộc thực tế của dự án:
(a) dữ liệu là tài liệu nội bộ Confluence/GitLab — gửi ra API ngoài là vấn đề chính sách;
(b) nội dung **song ngữ Việt–Anh** nên model phải multilingual; (c) chi phí/độ trễ pipeline là
rủi ro đã nêu trong PRD; (d) `vector(N)` trong Postgres khoá cứng số chiều → đổi model = re-embed
toàn bộ.

## Decision (đề xuất)

- Định nghĩa port `EmbeddingProvider` trong `mcp_ingest/ports.py`:
  `embed_documents(list[str]) -> list[list[float]]`, `embed_query(str) -> list[float]`,
  thuộc tính `model_id`, `dimensions`, `max_input_tokens`, `normalize`.
- Hai adapter ở v1: `LocalSentenceTransformerProvider` và `HttpEmbeddingProvider` (OpenAI-compatible
  `/v1/embeddings`, dùng được cho OpenAI/Voyage/self-host TEI/vLLM). Chọn bằng
  `MCP_INGEST_EMBEDDING_PROVIDER=local|http`.
- **Mặc định đề xuất: local `BAAI/bge-m3`, 1024 chiều, cosine, normalize=true.** Lý do:
  multilingual (tốt cho tiếng Việt), không chi phí theo token, dữ liệu nội bộ không ra ngoài,
  chạy được CPU cho khối lượng crawl theo giờ.
- `model_id` và `dimensions` được ghi vào từng chunk và được validate khi khởi động:
  nếu cấu hình khác với dữ liệu đã lưu → server pgvector từ chối chạy với thông báo yêu cầu
  re-embed (không im lặng so sánh vector khác không gian).
- Query embedding: server `mcp-pgvector` cũng cần embed câu hỏi → dùng **cùng** provider/model,
  cấu hình chia sẻ; nếu provider là `local`, model được load lazy một lần cho mỗi process.

## Alternatives Considered

### Alternative 1: OpenAI `text-embedding-3-small` (1536 chiều)
- **Pros**: Chất lượng tốt, không cần hạ tầng, rẻ.
- **Cons**: Toàn bộ tài liệu nội bộ phải gửi ra API bên ngoài — cần phê duyệt chính sách;
  chi phí tăng theo khối lượng re-ingest; thêm phụ thuộc mạng vào pipeline.
- **Why not**: Rào cản chính sách dữ liệu nội bộ; nhưng được hỗ trợ sẵn qua `HttpEmbeddingProvider`
  nếu user chọn.

### Alternative 2: AWS Bedrock Titan Embeddings v2
- **Pros**: Cùng nhà cung cấp với CloudWatch/SQS đang dùng, dữ liệu trong VPC/AWS account.
- **Cons**: Cần Bedrock được bật ở region đang dùng; thêm quyền IAM cho pipeline (ghi chú:
  pipeline là thành phần duy nhất được phép có credential khác read-only).
- **Why not**: Không rõ Bedrock đã bật; để làm phương án 1B nếu user ưu tiên "không cài model local".

### Alternative 3: `intfloat/multilingual-e5-large` (1024 chiều) local
- **Pros**: Cũng multilingual, 1024 chiều, nhẹ hơn bge-m3.
- **Cons**: Yêu cầu prefix `query:`/`passage:` (dễ dùng sai → giảm chất lượng rõ rệt).
- **Why not**: bge-m3 xử lý văn bản dài tốt hơn và không cần prefix; e5 là phương án dự phòng
  cùng số chiều nên đổi không cần migrate schema.

## Consequences

### Positive
- Đổi provider không sửa pipeline hay server query (chỉ cấu hình + re-embed).
- Không chi phí per-token ở mặc định → giảm rủi ro chi phí nêu trong PRD.
- Số chiều 1024 dùng chung cho cả hai model local ứng viên → tránh migrate schema khi đổi.

### Negative
- Model local ~2 GB (bge-m3) phải tải về; cần `sentence-transformers` + `torch` → package
  `mcp-ingest` nặng (đặt trong optional dependency group `local-embeddings`).
- Embed CPU chậm hơn API → giới hạn khối lượng mỗi lần chạy; phải batch và checkpoint.

### Risks
- Chất lượng semantic search phụ thuộc model chưa được đo trên dữ liệu thật. Giảm thiểu:
  spike "embedding bake-off" ở đầu Phase 3, đo trên bộ câu hỏi mẫu của NFR-003 trước khi
  embed toàn bộ.
- Khoá cứng số chiều. Giảm thiểu: cột `embedding_model` + `ingest_runs` cho phép re-embed
  theo lô và một script `mcp-ingest reembed`.
