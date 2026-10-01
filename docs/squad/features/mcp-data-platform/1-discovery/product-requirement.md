# MCP Data Platform — Product Requirement

## Problem
- Ai đau: cá nhân người yêu cầu, toàn bộ team kỹ thuật/dev, và on-call/SRE khi điều tra sự cố. Bất kỳ thành viên team nào cần tra cứu thông tin kỹ thuật/vận hành đều là đối tượng chịu ảnh hưởng.
- Nỗi đau quan sát được: phải mở và chuyển đổi giữa nhiều tab/tool rời rạc (Confluence, GitLab, Kibana, CloudWatch, v.v.) để trả lời một câu hỏi hoặc điều tra một vấn đề; thông tin từ các nguồn không liên kết được với nhau, khiến việc tổng hợp chậm và dễ bỏ sót; khi có incident, thời gian điều tra kéo dài vì phải tự tay đối chiếu log, metric, code, tài liệu từ nhiều hệ thống khác nhau.
- Vì sao làm ngay bây giờ: có nhu cầu cấp thiết hiện tại (nỗi đau đang xảy ra hàng ngày), đồng thời đây là cơ hội để xây một nền tảng dài hạn (MCP layer) có thể mở rộng thêm nguồn dữ liệu và thêm người dùng/kênh truy cập trong tương lai.

## Users & personas
- **Requester/Power user**: người trực tiếp nêu yêu cầu, dùng Claude Desktop/Code hàng ngày để tra cứu thông tin kỹ thuật.
- **Dev/Engineer trong team**: cần tra cứu code (GitLab), tài liệu (Confluence) khi phát triển hoặc review.
- **On-call/SRE**: cần điều tra incident nhanh, tra cứu log/metric/trace liên quan (OpenSearch, Kibana, CloudWatch, Kafka, Redis) khi có sự cố.
- Tất cả người dùng đều truy cập qua Claude (Desktop/Code) với MCP server chạy local qua stdio — không có UI riêng.

## Goals / Non-goals

### Goals
- Cho phép Claude truy vấn trực tiếp, chỉ-đọc (read-only), dữ liệu thật từ 9 nguồn: Confluence, GitLab, OpenSearch, Kibana, CloudWatch, Kafka, Redis, SQS/SNS, Postgres+pgvector.
- Mỗi nguồn dữ liệu là một MCP server độc lập, chạy local qua stdio, tích hợp trực tiếp với Claude Desktop/Code.
- Xây dựng pipeline ingest/embedding riêng để crawl dữ liệu từ các nguồn khác, tạo embedding và lưu vào Postgres+pgvector, phục vụ truy vấn semantic qua MCP server pgvector.
- Kiến trúc không chặn đường mở rộng sau này sang multi-user/remote hosting (HTTP+SSE) — dù đây không phải yêu cầu triển khai của v1.
- Đảm bảo mọi câu trả lời của Claude dựa trên dữ liệu thật, có thể trích dẫn/trace được về nguồn gốc (không đoán, không bịa đặt).

### Non-goals
- Không xây dựng UI/web dashboard riêng; toàn bộ truy cập thông qua Claude và MCP tools.
- Không cho phép ghi/sửa/xoá dữ liệu ở bất kỳ nguồn nào trong 9 nguồn — toàn bộ là read-only tuyệt đối.
- Không triển khai multi-user/remote hosting (HTTP+SSE) ở v1 — chỉ đảm bảo kiến trúc không chặn hướng mở rộng này về sau.

## Success metrics
- Claude trả lời đúng và có trích dẫn nguồn thật (trace được về Confluence/GitLab/OpenSearch/.../pgvector) cho các câu hỏi thuộc phạm vi 9 nguồn dữ liệu — không có câu trả lời bịa đặt (hallucination) khi tool có dữ liệu liên quan.
  - Ngưỡng đo cụ thể (ví dụ: % câu hỏi có trích dẫn đúng, trên tập câu hỏi mẫu nào, trong khung thời gian nào): TBD — cần xác thực qua khảo sát/thử nghiệm thực tế sau khi MVP (Confluence + GitLab) ra mắt.
- Giảm thời gian tra cứu/điều tra so với cách làm thủ công hiện tại (mở nhiều tab, tự đối chiếu thông tin).
  - Số liệu nền (baseline thời gian điều tra thủ công hiện tại) và mục tiêu giảm (%, theo khung thời gian nào): TBD — cần xác thực qua đo lường trước/sau khi có nhóm observability/incident (OpenSearch, Kibana, CloudWatch, Kafka, Redis) đi vào sử dụng.

## Scope — MoSCoW

### Must (phased rollout — toàn bộ là Must-have của feature, chia theo thứ tự triển khai, không phải bị cắt giảm)

**Phase 1 — MVP (Knowledge & Code):**
- MCP server Confluence (read-only): tra cứu tài liệu, trang, nội dung.
- MCP server GitLab (read-only): tra cứu repo, code, merge request, issue, pipeline.

**Phase 2 — Observability & Incident:**
- MCP server OpenSearch (read-only): tra cứu index, log, search.
- MCP server Kibana (read-only): tra cứu dashboard, visualization liên quan.
- MCP server CloudWatch (read-only): tra cứu log, metric, alarm.
- MCP server Kafka (read-only): tra cứu topic, message, metadata.
- MCP server Redis (read-only): tra cứu key, value, cấu trúc dữ liệu cache.

**Phase 3 — Messaging & Vector (tách thành 2 hạng mục Must-have riêng biệt):**
- MCP server SQS/SNS (read-only): tra cứu queue, topic, message metadata.
- **MCP server Postgres+pgvector (query)**: truy vấn semantic/vector search chỉ-đọc trên dữ liệu đã được embedding.
- **Ingest/embedding pipeline** (hạng mục riêng, quy mô lớn hơn các MCP server khác): crawl dữ liệu từ các nguồn còn lại (Confluence, GitLab, OpenSearch, v.v.), tạo embedding, lưu vào Postgres+pgvector để phục vụ truy vấn semantic.

Thứ tự ưu tiên triển khai (để Lead dùng khi lập implementation-plan): Phase 1 → Phase 2 → Phase 3. Cả 9 nguồn dữ liệu và pipeline ingest/embedding đều là Must-have của feature tổng thể; việc chia phase chỉ phản ánh trình tự triển khai, không phải cắt giảm phạm vi.

### Should
- TBD — cần xác thực qua trao đổi thêm với team khi bắt đầu từng phase (ví dụ: khả năng lọc/giới hạn kết quả truy vấn theo quyền hạn người dùng, dù v1 giả định mọi thành viên team đều có quyền tra cứu như nhau).

### Could
- Khả năng mở rộng thêm nguồn dữ liệu mới ngoài 9 nguồn đã liệt kê, theo cùng mô hình MCP server read-only.
- Cơ chế cache/tối ưu hiệu năng truy vấn cho các nguồn có khối lượng dữ liệu lớn (OpenSearch, Kafka).

### Won't (this release)
- Không xây UI/web dashboard riêng.
- Không hỗ trợ ghi/sửa/xoá dữ liệu ở bất kỳ nguồn nào.
- Không triển khai multi-user/remote hosting (HTTP+SSE) trong v1.

## User journeys

1. Dev cần tra cứu tài liệu kỹ thuật (Confluence + GitLab — Phase 1)
   1. Dev đặt câu hỏi cho Claude về một tính năng/module.
   2. Claude gọi MCP server Confluence để tìm trang tài liệu liên quan.
   3. Claude gọi MCP server GitLab để tìm code/merge request liên quan.
   4. Claude tổng hợp câu trả lời, trích dẫn nguồn (link Confluence, GitLab).
   5. Dev nhận câu trả lời có thể trace được về nguồn thật, không cần mở tab riêng.

2. On-call điều tra incident (Observability — Phase 2)
   1. On-call nhận cảnh báo sự cố.
   2. On-call hỏi Claude về log/metric liên quan trong khung thời gian xảy ra sự cố.
   3. Claude gọi MCP server CloudWatch/OpenSearch/Kibana để lấy log, metric, dashboard liên quan.
   4. Claude gọi MCP server Kafka/Redis nếu cần kiểm tra trạng thái message/cache liên quan.
   5. Claude tổng hợp thông tin từ nhiều nguồn thành một bức tranh liên kết, trích dẫn nguồn cụ thể.
   6. On-call rút ngắn thời gian điều tra so với việc tự tay đối chiếu từng hệ thống.

3. Truy vấn semantic qua dữ liệu đã embedding (Messaging & Vector — Phase 3)
   1. Pipeline ingest/embedding crawl dữ liệu định kỳ từ các nguồn (Confluence, GitLab, OpenSearch, v.v.) và lưu embedding vào Postgres+pgvector.
   2. Người dùng đặt câu hỏi mang tính tổng hợp/ngữ nghĩa cho Claude.
   3. Claude gọi MCP server Postgres+pgvector để tìm các đoạn dữ liệu liên quan theo ngữ nghĩa.
   4. Claude gọi MCP server SQS/SNS nếu cần kiểm tra trạng thái message/queue liên quan.
   5. Claude trả lời kèm trích dẫn nguồn gốc của dữ liệu đã được embedding.

## Assumptions & risks

**Assumptions:**
- Mọi thành viên trong team có quyền truy cập và tra cứu như nhau đối với 9 nguồn dữ liệu (không có phân quyền chi tiết theo vai trò ở v1) — TBD — cần xác thực qua xác nhận chính sách truy cập nội bộ.
- Các dịch vụ Confluence, GitLab, OpenSearch, Kibana, CloudWatch là dịch vụ thật/remote đã tồn tại và có thể truy cập được từ môi trường chạy MCP server.
- Docker/docker-compose có sẵn trên máy để chạy hạ tầng dev/test cho Postgres+pgvector, Redis, Kafka, LocalStack (mock SQS/SNS).

**Risks:**
- **Rủi ro truy cập mạng nội bộ**: môi trường hiện tại đã quan sát có ít nhất 1 MCP server nội bộ khác bị timeout kết nối (có thể do cần VPN). Đây là rủi ro cần SA và Lead lưu ý khi thiết kế kết nối tới các nguồn dữ liệu thật/remote (Confluence, GitLab, OpenSearch, Kibana, CloudWatch), có thể ảnh hưởng đến khả năng demo/test end-to-end nếu không có VPN/network access phù hợp.
- Pipeline ingest/embedding có quy mô lớn, phụ thuộc vào nhiều nguồn dữ liệu khác nhau — rủi ro về độ trễ dữ liệu (embedding không realtime), rủi ro chi phí lưu trữ/tính toán embedding tăng theo thời gian. TBD — cần xác thực qua ước lượng của SA/Lead ở giai đoạn thiết kế.
- Chất lượng câu trả lời của Claude phụ thuộc vào độ đầy đủ/chính xác của dữ liệu trả về từ mỗi MCP server; nếu một nguồn dữ liệu trả về thiếu hoặc sai, rủi ro Claude suy luận sai dù có trích dẫn.

## Open questions

1. Ngưỡng đo cụ thể cho success metric "trả lời đúng, có trích dẫn nguồn" (ví dụ % trên tập câu hỏi mẫu, khung thời gian đo) là gì?
   - Owner: PO
   - Cách xác thực: định nghĩa tập câu hỏi mẫu và đo baseline sau khi Phase 1 (Confluence + GitLab) ra mắt.

2. Baseline thời gian tra cứu/điều tra thủ công hiện tại là bao lâu, và mục tiêu giảm (%) là bao nhiêu, trong khung thời gian nào?
   - Owner: PO (phối hợp với on-call/SRE team)
   - Cách xác thực: khảo sát hoặc đo lường thời gian điều tra incident thực tế trước và sau khi Phase 2 (observability) đi vào sử dụng.

3. Có cần phân quyền truy cập theo vai trò (role-based access) đối với 9 nguồn dữ liệu, hay mọi thành viên team đều có quyền như nhau?
   - Owner: PO (phối hợp với SA về chính sách bảo mật)
   - Cách xác thực: xác nhận chính sách truy cập nội bộ hiện có của team đối với từng nguồn dữ liệu.

4. Rủi ro kết nối mạng nội bộ/VPN tới các nguồn dữ liệu thật (đã quan sát ở MCP server nội bộ khác) có ảnh hưởng đến khả năng triển khai/test MCP server nào trong 9 nguồn không?
   - Owner: SA / Lead
   - Cách xác thực: kiểm tra kết nối thực tế tới từng dịch vụ (Confluence, GitLab, OpenSearch, Kibana, CloudWatch) từ môi trường phát triển/triển khai dự kiến, trước khi bắt đầu implementation của từng nguồn.

5. Chi phí và độ trễ chấp nhận được cho pipeline ingest/embedding (Phase 3) là gì — dữ liệu cần "fresh" tới mức nào?
   - Owner: PO (phối hợp với SA về thiết kế pipeline)
   - Cách xác thực: thảo luận với team về yêu cầu độ mới của dữ liệu semantic search khi bắt đầu thiết kế Phase 3.
