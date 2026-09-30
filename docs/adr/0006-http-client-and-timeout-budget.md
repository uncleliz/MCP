# ADR-0006: httpx + tenacity và timeout budget chuẩn (chốt ngưỡng NFR-002)

**Date**: 2026-10-01
**Status**: accepted
**Deciders**: SA (squad-sa); ngưỡng cần PO/Lead xác nhận ở Gate B (Open question 4)

## Context

NFR-002 yêu cầu mỗi server nối tới dịch vụ remote/nội bộ (Confluence, GitLab, OpenSearch,
Kibana, CloudWatch) phải phát sinh lỗi timeout/connection **tường minh, phân biệt được, trong
thời gian có giới hạn** thay vì treo vô hạn — rủi ro này đã quan sát thực tế (PRD: "Rủi ro
truy cập mạng nội bộ", có MCP server nội bộ khác bị timeout do thiếu VPN). requirements.md
để ngưỡng là TBD và giao SA/Lead chốt. Default của `requests`/`httpx` là **không có timeout**
nếu không truyền tường minh → chính là trạng thái treo cần loại bỏ.

## Decision

- Client HTTP chung: `httpx.AsyncClient` (async khớp với MCP SDK) được tạo bởi
  `mcp_common.http.build_client()`, **luôn** truyền `httpx.Timeout` tường minh; không cho phép
  package nào tự tạo `AsyncClient` (test kiến trúc chặn import trực tiếp).
- Retry: `tenacity` với chính sách chỉ áp cho request idempotent (GET/HEAD) và chỉ cho
  `ConnectError`, `ReadTimeout`, HTTP 429, 502/503/504; exponential backoff có jitter;
  tôn trọng `Retry-After`.
- **Timeout budget chuẩn (chốt cho NFR-002)**, có thể override bằng env:

| Tham số | Mặc định | Env |
|---|---|---|
| connect timeout | 3s | `MCP_HTTP_CONNECT_TIMEOUT` |
| read timeout | 15s | `MCP_HTTP_READ_TIMEOUT` |
| write/pool timeout | 5s / 5s | `MCP_HTTP_WRITE_TIMEOUT`, `MCP_HTTP_POOL_TIMEOUT` |
| số lần retry | 2 (tổng 3 lần gọi) | `MCP_HTTP_MAX_RETRIES` |
| backoff | 0.5s → 1s, jitter ±25% | `MCP_HTTP_BACKOFF_BASE` |
| **deadline tổng cho một tool call** | **25s** | `MCP_TOOL_DEADLINE` |

  Deadline tổng được áp bằng `asyncio.timeout` ở lớp decorator tool, nên kể cả client của
  nguồn không phải HTTP (Kafka, Redis, Postgres, boto3) cũng bị chặn: Kafka metadata 10s,
  Redis socket connect 2s / read 5s, Postgres `connect_timeout=3` + `statement_timeout=15s`,
  boto3 `connect_timeout=3, read_timeout=15, retries={'max_attempts':3,'mode':'standard'}`.
- Lỗi mạng được map thành `upstream_timeout` / `upstream_unavailable` với
  `error.details.hint = "kiểm tra VPN/kết nối nội bộ tới <host>"` để phân biệt rõ với lỗi nghiệp vụ.

## Alternatives Considered

### Alternative 1: `requests` đồng bộ
- **Pros**: Đơn giản, quen.
- **Cons**: Chặn event loop của MCP SDK async; không hỗ trợ HTTP/2, connection pool async.
- **Why not**: SDK là async; dùng sync sẽ phải chạy thread pool cho mọi call.

### Alternative 2: Tự viết retry thay vì thêm `tenacity`
- **Pros**: Bớt một dependency.
- **Cons**: Backoff + jitter + phân loại lỗi retryable + `Retry-After` là logic dễ sai và phải test kỹ.
- **Why not**: `tenacity` nhỏ, thuần Python, không dependency chuyển tiếp; đổi lấy độ đúng đắn là hợp lý.

### Alternative 3: Dựa vào `httpx.AsyncHTTPTransport(retries=n)`
- **Pros**: Không cần thêm lib.
- **Cons**: Chỉ retry lỗi kết nối, không retry 429/5xx.
- **Why not**: Không phủ được các case retry cần thiết.

## Consequences

### Positive
- NFR-002 có ngưỡng số cụ thể để QA viết test (giả lập endpoint không tới được và assert
  lỗi `upstream_timeout` trong < deadline).
- Mọi nguồn — kể cả non-HTTP — đều bị chặn bởi một deadline duy nhất ở lớp tool.

### Negative
- Retry làm worst case của một tool call ≈ 3 × read timeout; vì vậy deadline tổng (25s) cố tình
  nhỏ hơn 3 × 15s để dừng sớm và trả lời Claude thay vì retry đến cùng.

### Risks
- Nguồn chậm hợp lệ (OpenSearch query nặng, CloudWatch Logs Insights) có thể vượt deadline.
  Giảm thiểu: nâng `MCP_TOOL_DEADLINE_<TOOL>` cho đúng tool đó (xem A2), **không** cho
  `timeout_s` vượt deadline.

## Amendments (sau design review 2026-10-01)

### A1 — `asyncio.timeout` KHÔNG huỷ được SDK đồng bộ: cần thread pool có biên
Phát biểu ban đầu ("deadline áp bằng `asyncio.timeout` nên cả client non-HTTP cũng bị chặn")
**sai** với `boto3` và `confluent-kafka`, hai client chạy trong `asyncio.to_thread`:
`asyncio.timeout` huỷ *coroutine đang await*, còn thread vẫn tiếp tục chạy call botocore/librdkafka.
Với `connect_timeout=3, read_timeout=15, max_attempts=3`, thread có thể sống ~54s **sau khi**
tool đã trả `upstream_timeout` sạch sẽ. Executor mặc định của asyncio là `min(32, cpu+4)`
thread; vài call bị kẹt làm cạn executor và **mọi tool call sau đó bị block trước cả khi tới
deadline** — đúng cái treo mà NFR-002 cấm, lại còn vô hình vì các call đầu đã trả lỗi đẹp.

Quyết định: mỗi server dùng **`ThreadPoolExecutor` riêng, có biên** (`max_workers=4`) với
queue giới hạn; queue đầy → trả ngay `upstream_unavailable` kèm
`details.hint="đang có call tới <host> bị treo"`. Không dùng executor mặc định cho SDK đồng bộ.

### A2 — Bất biến số học của budget (bản chốt lại)
```
sum(per-attempt timeout × attempts) + tổng backoff  <  timeout_s  <  MCP_TOOL_DEADLINE
```
Bộ số cũ vi phạm chính nó (3 × 15s = 45s > 25s). Bộ số chốt lại:

| Tham số | Giá trị mới | Ghi chú |
|---|---|---|
| connect timeout | 3s | giữ nguyên |
| read timeout | **7s** | giảm từ 15s để 3 lần thử vẫn nằm trong budget |
| số lần thử | 3 (2 retry) | 3 × (3+7) = 30s… vẫn quá → xem dòng dưới |
| **số lần thử (chốt)** | **2 (1 retry)** | 2 × (3+7) + 1s backoff = 21s |
| `timeout_s` (nội bộ tool) | mặc định 20s, **max 22s** | phải nhỏ hơn deadline ≥ 3s để kịp dựng `status=partial` |
| `MCP_TOOL_DEADLINE` | 25s | chốt ngoài cùng |
| boto3 | `connect 3 / read 7 / max_attempts 2` | tổng ≤ 20s |
| Kafka | `socket.timeout.ms=8000`, `metadata.request.timeout.ms=8000` | |
| Postgres | `connect_timeout=3`, `statement_timeout=15s` | |
| Redis | connect 2s / read 5s | |

Tool cần lâu hơn (`cloudwatch_run_logs_insights`, `opensearch_search_dsl`) nâng
`MCP_TOOL_DEADLINE_<TOOL>` (ví dụ 60s) **và** `timeout_s` của nó được validate theo deadline
riêng đó — chứ không phải cho `timeout_s` vượt deadline chung như bản đầu.

### A3 — Retry sleep bị kẹp theo budget còn lại; cleanup phải shielded
- `Retry-After: 120` từng là thời gian chết thuần: nay `sleep = min(retry_after, remaining_budget − 1s)`;
  nếu không còn đủ budget thì trả `rate_limited` ngay kèm `retry_after_s` để Claude biết nên
  hỏi lại sau.
- Khi deadline huỷ `cloudwatch_run_logs_insights`, `StopQuery` phải chạy trong
  `try/finally` + `asyncio.shield`, nếu không query Insights bị bỏ rơi ở trạng thái running.
