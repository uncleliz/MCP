# Spike S3 — Có cần hybrid search (`tsvector` + RRF) không? (T-086)

Trạng thái: **QUY TRÌNH ĐÃ VIẾT, ĐO ĐẠC CHƯA LÀM.** Spike này là "sau Phase 3" và cần **corpus thật
đã ingest bằng model embedding thật**; cả hai chưa có trong container của squad (không có nguồn
thật, không tải được trọng số model; xem S1/S2). Không có số liệu nào bên dưới là đo thật. Kết luận
có/không thêm migration `0007` **chưa thể đưa ra**; mọi ô kết quả để trống có chủ đích.

Câu hỏi (R8): truy vấn từ khoá chính xác (tên hàm, mã lỗi, tên queue, `ERR_xxx`) có bị vector-only
bỏ sót tới mức cần `tsvector` + Reciprocal Rank Fusion (RRF) không? Ở v1, rủi ro được giảm nhẹ bằng
việc Claude vẫn có `gitlab_search_code` / `opensearch_search_logs` cho tra cứu chính xác.
Plan **không** yêu cầu implement hybrid ở đây — chỉ đo và kết luận kèm ước lượng công.

## 1. Điều kiện tiên quyết

- Corpus thật đã ingest (Phase 3 sign-off phần thủ công xong), model đã chốt ở S2 và ghi trong ADR-0010.
- Số chunk tối thiểu để kết quả có ý nghĩa: corpus thật của team (không phải dữ liệu mẫu). Ghi số
  document/chunk thực tế vào bảng kết quả.
- Người chấm ground truth hiểu domain (chỉ định chunk/tài liệu nào "đúng" cho từng truy vấn).

## 2. Bộ truy vấn (≥ 20, từ khoá chính xác)

Lấy từ lịch sử tìm kiếm thật của team, không bịa. Mỗi nhóm tối thiểu 5 truy vấn:

| Nhóm | Ví dụ dạng truy vấn | Vì sao vector-only dễ hụt |
|---|---|---|
| Tên định danh trong code | `RetryPolicy.compute_backoff`, `payments_dlq_handler` | token hiếm, embedding làm mờ |
| Mã lỗi / mã trạng thái | `ERR_PAY_4021`, `SQLSTATE 40001` | chuỗi ngắn không có ngữ nghĩa |
| Tên tài nguyên hạ tầng | `payments-events-dlq`, `consumer group billing-sync` | trùng một phần với nhiều queue/topic |
| Câu tự nhiên (đối chứng) | "retry thanh toán hoạt động ra sao" | vector-only vốn tốt: dùng để kiểm hybrid *không làm xấu đi* |

Lưu vào `eval/hybrid_queries.yaml` (không commit nếu chứa nội dung nội bộ), schema:
`{id, text, group, relevant: [source_id, ...]}`; `relevant` là `source_id` của document đúng
(không dùng `chunk_id`, đổi sau mỗi lần re-chunk).

## 3. Cách đo

1. **Vector-only**: chạy `kb_semantic_search` (hoặc trực tiếp `semantic_search` của `mcp_pgvector`)
   với `top_k = 10`, `min_similarity = 0`; ghi hạng đầu tiên chứa một `source_id` đúng.
2. **Hybrid (thử nghiệm, KHÔNG commit vào schema)**: trên bản sao DB, thêm cột tạm
   `tsv tsvector GENERATED ALWAYS AS (to_tsvector('simple', content)) STORED` + index GIN, rồi
   gộp hai danh sách bằng RRF (`score = Σ 1 / (60 + rank)`) cho top-10. Dùng cấu hình `simple`
   (không stemming) vì token là định danh/mã lỗi; nếu corpus tiếng Việt chiếm đa số, thử thêm
   `unaccent`.
3. Chỉ số: **recall@10** (có ≥ 1 `source_id` đúng trong 10 đầu), **MRR@10**, và độ trễ p50/p95 của
   cả hai đường. Tính riêng theo nhóm truy vấn.
4. Kiểm hybrid không làm xấu nhóm "câu tự nhiên" (đối chứng): recall không giảm quá mức chấp nhận.

Công cụ: có thể tái dùng khung đo của `scripts/recall_benchmark.py` (hàm `measure`, truy vấn
bằng `source_id` thay vì so với brute force). Đây là mở rộng nhỏ, làm khi có corpus.

## 4. Bảng kết quả (điền khi có dữ liệu thật)

Corpus: `____` document / `____` chunk, model `____`, ngày đo `____`.

| Nhóm | #truy vấn | recall@10 vector-only | recall@10 hybrid | MRR@10 vector-only | MRR@10 hybrid |
|---|---|---|---|---|---|
| Tên định danh trong code | | | | | |
| Mã lỗi / mã trạng thái | | | | | |
| Tên tài nguyên hạ tầng | | | | | |
| Câu tự nhiên (đối chứng) | | | | | |
| **Tổng (≥ 20)** | | | | | |

Độ trễ: vector-only p50/p95 = `____`; hybrid p50/p95 = `____`.

## 5. Tiêu chí quyết định (đề xuất, chờ PO/SA xác nhận)

- **Thêm hybrid** nếu recall@10 của nhóm từ khoá chính xác tăng ≥ 0.15 tuyệt đối so với vector-only
  **và** nhóm đối chứng không giảm quá 0.03 **và** p95 hybrid vẫn nằm trong ngân sách 25 giây của
  tool call (NFR-002). Các con số này là điểm xuất phát, chưa được PO duyệt (`# THRESHOLD TBD`).
- **Không thêm** nếu mức tăng nhỏ hơn: chuyển hướng dẫn Claude dùng `gitlab_search_code` /
  `opensearch_search_logs` cho tra cứu chính xác (đã có ở v1) và ghi vào prompt `semantic_synthesis`.

## 6. Ước lượng công nếu quyết định "có" (không thực hiện trong plan này)

| Hạng mục | Ước lượng |
|---|---|
| Migration `0007_hybrid_search.sql`: cột `tsv` sinh tự động (stored) + index GIN; `mcp_query_ro` đã có `SELECT` | 0.5 ngày |
| Câu SQL mới trong `mcp_pgvector/sql.py` (RRF hai CTE) + tham số `mode: vector\|hybrid` ở `kb_semantic_search` — **thay đổi contract (minor)**: SA phải sửa `api-contract.yaml`, quay lại stage `sa` | 1 ngày + vòng SA |
| `mcp-ingest`: không đổi (cột sinh tự động từ `content`) ngoài việc build index lần đầu (R12: `maintenance_work_mem`) | 0.25 ngày |
| Test: ground truth, recall hybrid so với vector-only trên bộ mẫu, regression cho `empty`/filter (ADR-0011 A3) | 1.5 ngày |
| Tài liệu runbook + cập nhật eval | 0.5 ngày |
| **Tổng** | **~4 ngày công** (không tính vòng SA/Gate cho thay đổi contract) |

Rủi ro: kích thước index GIN trên toàn bộ `content`; ảnh hưởng thời gian `INSERT` của ingest;
hai danh sách có thang điểm khác nhau nên phải dùng RRF theo hạng, không cộng điểm thô.

## 7. Việc còn lại để đóng spike

- [ ] Có corpus thật + model thật (sau sign-off Phase 3 phần thủ công).
- [ ] Lập `eval/hybrid_queries.yaml` ≥ 20 truy vấn kèm `relevant`.
- [ ] Chạy đo, điền bảng mục 4, đối chiếu tiêu chí mục 5.
- [ ] Ghi kết luận có/không và, nếu "có", mở task cho SA (đổi contract) — không tự thêm cột.
