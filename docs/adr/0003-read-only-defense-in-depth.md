# ADR-0003: Read-only theo chiều sâu (defense in depth) trên cả 9 nguồn

**Date**: 2026-10-01
**Status**: accepted
**Deciders**: SA (squad-sa)

## Context

BR-001, FR-014 và NFR-001 yêu cầu bảo đảm tuyệt đối: không tool nào của 9 server có thể
ghi/sửa/xoá dữ liệu ở nguồn, và 100% thao tác mutating phải bị từ chối. Chỉ "không viết tool
ghi" là chưa đủ: một số SDK có side effect ngầm (SQS `ReceiveMessage` đổi visibility timeout,
Kafka consumer commit offset, OpenSearch `_search` có thể chạy painless script), và credential
quá rộng cho phép lỗi lập trình tương lai trở thành lỗi dữ liệu.

## Decision

Áp dụng 5 lớp phòng ngự, mỗi lớp phải thoả độc lập:

1. **Không đăng ký tool mutating**: tool surface của mỗi server được khai báo tường minh và
   chốt trong `api-contract.yaml` + file snapshot `tools.snapshot.json` của từng package.
2. **Allowlist ở tầng client**: mỗi client nội bộ chỉ được gọi tập operation/endpoint/command
   trong allowlist (`mcp_common.readonly.enforce`), mọi thứ khác raise `NotPermittedError`.
   Ví dụ: Redis allowlist theo tên command; OpenSearch chỉ `_search/_count/_cat/_mapping`;
   boto3 chỉ các API `Describe*/Get*/List*/Filter*`.
3. **Credential/role bị thu hẹp ở nguồn**: DB role `mcp_query_ro` chỉ có `SELECT` +
   `default_transaction_read_only=on`; Redis ACL user chỉ có command đọc; IAM role chỉ có
   action read; PAT/API token của Confluence & GitLab được cấp ở mức read-only.
4. **Cấm cả side effect gián tiếp**: không khởi tạo Kafka `Producer` ở bất kỳ đâu; Kafka
   consumer dùng `assign()` + `enable.auto.commit=false` + group.id dùng-một-lần; **không**
   expose `sqs:ReceiveMessage` (vì nó đổi visibility timeout) — SQS/SNS chỉ metadata;
   OpenSearch từ chối body có `script`, `scripted_metric`, `_delete_by_query`, `_update*`.
5. **Test bắt buộc trước sign-off mỗi phase**: một fixture chung
   (`mcp_common.testing.assert_readonly_tool_surface`) enumerate tool list thực tế, so với
   snapshot + deny-regex (`create|update|delete|put|post|write|set|del|publish|send|produce|purge|merge|push|expire|flush|drop|truncate`),
   và một test gọi tên tool "ghi" không tồn tại để xác nhận bị reject (FR-014 AC-002).

## Alternatives Considered

### Alternative 1: Chỉ dựa vào "không viết tool ghi"
- **Pros**: Nhanh nhất.
- **Cons**: Không chống được side effect ngầm của SDK và lỗi hồi quy khi thêm tool mới.
- **Why not**: NFR-001 đòi bảo đảm 100% có kiểm chứng tự động.

### Alternative 2: Chỉ dựa vào credential read-only ở nguồn
- **Pros**: Bảo đảm mạnh nhất về mặt hệ thống.
- **Cons**: Không phải nguồn nào cũng scope được tốt (Kibana saved objects API, Confluence
  Server PAT); phụ thuộc cấu hình ngoài repo, không kiểm chứng được trong CI.
- **Why not**: Cần nhưng chưa đủ; giữ làm lớp 3.

### Alternative 3: Proxy read-only chung trước mọi nguồn
- **Pros**: Một điểm kiểm soát.
- **Cons**: Thêm một service phải chạy → phá vỡ mô hình stdio-only của NFR-005, thêm SPOF.
- **Why not**: Ngoài scope v1.

## Consequences

### Positive
- Mọi FR-xxx AC-003 ("không có tool ghi") có cơ chế kiểm chứng tự động, không phải nhận định thủ công.
- Snapshot tool surface biến mọi thay đổi bề mặt thành một diff phải review (gắn với ADR-0013).

### Negative
- Không đọc được nội dung message SQS (chỉ metadata) → giảm khả năng debug; đã được
  requirements.md chấp nhận (FR-010 chỉ yêu cầu metadata).
- Allowlist làm mỗi client dài hơn và cần cập nhật khi thêm tool.

### Risks
- Lớp 3 phụ thuộc người vận hành cấp đúng credential. Xem Amendment A1 — đã nâng từ "báo cáo"
  thành "điều kiện tiên quyết lúc khởi động".

## Amendments (sau design review 2026-10-01)

### A1 — Lớp 3 là điều kiện khởi động, không phải báo cáo (nâng mức)
Trước đó lớp 3 chỉ được kiểm bởi lệnh `doctor` chạy tay, nên NFR-001 "100%" là phát biểu quá
mạnh: người dùng dán DSN `mcp_ingest_rw` vào `MCP_PGVECTOR_DSN`, hay URL Redis của user
default, vẫn qua được toàn bộ test tự động (lớp 1/2/5 chỉ kiểm **code**, không kiểm credential
đang dùng). Nay: `build_server()` **từ chối serve** nếu credential không tự chứng minh là
read-only:

| Nguồn | Kiểm lúc khởi động |
|---|---|
| Postgres | `SHOW transaction_read_only` = `on` **và** `has_table_privilege('kb.chunks','INSERT') = false` |
| Redis | `ACL WHOAMI` + `ACL GETUSER` không có category ghi (cần `+acl|getuser`, xem ADR-0008 A1) |
| AWS | assert tool allowlist so với `sts get-caller-identity`; nếu có quyền, `iam:SimulatePrincipalPolicy` cho một action ghi và assert `implicitDeny` |
| GitLab | `GET /personal_access_tokens/self` → scope ⊆ {`read_api`, `read_repository`} |
| Confluence | current-user check + assert token không có quyền tạo nội dung |
| Kafka | `describe_acls` nếu được phép; tối thiểu assert config client (xem A3) |

Escape duy nhất: `MCP_ALLOW_UNVERIFIED_CREDENTIALS=true` — tường minh và được log ở mức WARN
mỗi lần khởi động.

### A2 — Lớp 5 không dùng deny-regex theo tên tool nữa
Deny-regex cũ chứa `merge`, nên khớp **hai tool Phase 1 hợp lệ**
(`gitlab_list_merge_requests`, `gitlab_get_merge_request`) → test FR-014 đỏ ngay ngày đầu và
áp lực "sửa" bằng cách làm yếu regex. Nó cũng là kiểm tra yếu nhất có thể: một tool tên
`confluence_get_page` mà lại POST thì regex không thấy. Thay bằng hai kiểm tra **gắn với hành vi**:

1. **Assertion ở tầng transport** (`mcp_common.http`): mọi request đi ra phải là `GET`/`HEAD`,
   trừ allowlist tường minh `(host_pattern, method, path_pattern)` — hiện chỉ gồm
   `POST …/_search` và `POST …/_count` của OpenSearch. Assert này bật trong **mọi** unit test
   qua transport `respx`, nên bất kỳ call mutating mới nào cũng làm test đỏ bất kể tên tool.
2. **Assertion trên contract**: mọi operation trong `api-contract.yaml` phải có
   `x-readonly: true` + `x-side-effects: none`, và `tools.snapshot.json` ⊆ tập operation có
   `x-interface != cli`.

Heuristic theo tên chỉ còn là **warning** với allowlist tường minh cho `merge_request`.

### A3 — Lớp 4 bổ sung: chống tạo tài nguyên ngầm
- **Kafka auto-create topic là một thao tác GHI.** `AdminClient.list_topics(topic="x")` gửi
  metadata request theo tên topic, và broker có `auto.create.topics.enable=true` (mặc định của
  nhiều bản phân phối) sẽ **tạo topic**. Chính test âm của FR-007 AC-002 đi vào đường này.
  Bắt buộc: `allow.auto.create.topics=false` trên cả `Consumer` và `AdminClient`, **không bao
  giờ** truyền `topic=` vào metadata (gọi cluster-wide rồi filter client-side), và ACL deny
  `Create` trên Cluster + Topic. Xem ADR-0009 A1.
- **State phía server**: cấm `scroll` và `point_in_time` của OpenSearch (tạo state) — cùng lý
  do đã áp cho `script`. Ngoại lệ đã ghi nhận và có lý do: CloudWatch `StartQuery` (không đổi
  dữ liệu log, có `StopQuery` trong `finally`).
- **Confluence `body.export_view`** render macro phía server; một số macro gọi ra ngoài hoặc
  chạy việc nặng. Chuyển sang `body.storage`/`body.view` cho FR-001 (xem ADR-0007 A1).
