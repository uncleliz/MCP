# ADR-0008: Chọn SDK cho OpenSearch, AWS, Redis, Postgres/pgvector

**Date**: 2026-10-01
**Status**: accepted
**Deciders**: SA (squad-sa)

## Context

Khác với Confluence/GitLab (REST đơn giản, ADR-0007), bốn nhóm nguồn còn lại có protocol
riêng mà tự implement là không hợp lý: OpenSearch (DSL + auth SigV4/basic), AWS CloudWatch &
SQS/SNS (SigV4, pagination, error model), Redis (RESP3), Postgres (wire protocol + pgvector type).
Vẫn phải bảo đảm read-only (BR-001) và dùng được timeout budget chung (ADR-0006).

## Decision

| Nguồn | SDK | Ràng buộc read-only |
|---|---|---|
| OpenSearch | `opensearch-py` (async `AsyncOpenSearch`) | wrapper chỉ cho `search`, `count`, `indices.get_mapping`, `cat.indices`; từ chối body chứa `script`, `scripted_metric`, `runtime_mappings`; không bao giờ gọi `index/update/delete/bulk/reindex` |
| Kibana | thin REST client (`httpx`) — không có SDK Python chính thức | chỉ `GET /api/saved_objects/_find`, `GET /api/saved_objects/{type}/{id}`, `GET /api/status`; header `kbn-xsrf` chỉ cần cho POST nên không dùng |
| CloudWatch | `boto3` (`logs`, `cloudwatch`) | allowlist API: `DescribeLogGroups`, `FilterLogEvents`, `StartQuery`/`GetQueryResults`/`StopQuery`, `ListMetrics`, `GetMetricData`, `DescribeAlarms`, `DescribeAlarmHistory`; IAM role read-only |
| SQS/SNS | `boto3` (`sqs`, `sns`) | allowlist: `ListQueues`, `GetQueueUrl`, `GetQueueAttributes`, `ListDeadLetterSourceQueues`, `ListQueueTags`, `ListTopics`, `GetTopicAttributes`, `ListSubscriptionsByTopic`. **Không** `ReceiveMessage` (đổi visibility timeout) |
| Redis | `redis-py` (`redis.asyncio`) | allowlist theo tên command; ACL user chỉ đọc; `KEYS` bị cấm, chỉ dùng `SCAN`/`HSCAN`/`SSCAN` |
| Postgres + pgvector | `psycopg` 3 (async) + `pgvector[psycopg]` | server query dùng role `mcp_query_ro`, mở transaction `READ ONLY`, `statement_timeout=15s`, chỉ chạy câu SQL tham số hoá đã viết sẵn — **không có tool chạy SQL tuỳ ý** |

Redis command allowlist v1: `GET, MGET, STRLEN, GETRANGE, TYPE, TTL, PTTL, EXISTS, SCAN,
HGET, HMGET, HGETALL, HSCAN, HLEN, LRANGE, LLEN, SMEMBERS, SSCAN, SCARD, ZRANGE, ZCARD,
XRANGE, XLEN, XINFO STREAM, OBJECT ENCODING, MEMORY USAGE, DBSIZE, INFO, ACL WHOAMI`.
ACL tương ứng (do vận hành cấp):
`ACL SETUSER mcp_ro on >… ~* -@all +get +mget +strlen +getrange +type +ttl +pttl +exists +scan +hget +hmget +hgetall +hscan +hlen +lrange +llen +smembers +sscan +scard +zrange +zcard +xrange +xlen +xinfo +object|encoding +memory|usage +dbsize +info +acl|whoami`.

`StartQuery`/`StopQuery` của CloudWatch Logs Insights được coi là read-only vì không thay đổi
dữ liệu log; ADR ghi nhận tường minh để review sau này không bất ngờ.

## Alternatives Considered

### Alternative 1: `elasticsearch-py` cho OpenSearch
- **Pros**: Quen hơn.
- **Cons**: Client 8.x chủ động từ chối/ cảnh báo khi nói chuyện với cluster OpenSearch.
- **Why not**: `opensearch-py` là client đúng cho cluster OpenSearch.

### Alternative 2: `aioboto3`/`aiobotocore` thay `boto3`
- **Pros**: Async thật, không chiếm thread.
- **Cons**: Bám phiên bản botocore chặt, dễ vỡ khi nâng cấp; lợi ích nhỏ khi mỗi tool call chỉ
  gọi 1–3 API.
- **Why not**: Dùng `boto3` trong `asyncio.to_thread()` là đủ và ổn định hơn.

### Alternative 3: Expose một tool `postgres_query(sql)` read-only
- **Pros**: Linh hoạt tối đa cho Claude.
- **Cons**: Bề mặt injection lớn; phải tự parse SQL để chặn CTE ghi (`WITH x AS (DELETE …)`);
  đọc được mọi bảng ngoài kho embedding.
- **Why not**: FR-011 chỉ cần semantic search; rủi ro không tương xứng lợi ích. Ghi nhận là
  "rejected by design" trong architecture.md.

## Consequences

### Positive
- Mỗi nguồn dùng client chuẩn của nó → ít bug protocol, ít công.
- Allowlist theo API/command là dữ liệu (list hằng) nên test được và review được bằng diff.

### Negative
- `boto3` sync → cần `asyncio.to_thread`, thêm một lớp bọc trong `mcp_common.aws`.
- Không có tool SQL tuỳ ý → mọi truy vấn mới trên pgvector đòi thêm tool (thay đổi contract).

### Risks
- `redis-py` vẫn có method ghi trong runtime (cùng rủi ro như ADR-0007 nhưng chấp nhận vì
  không có client read-only-only). Giảm thiểu: wrapper allowlist + ACL user read-only + test
  assert wrapper từ chối `SET/DEL/EXPIRE/FLUSHALL`.

## Amendments (sau design review 2026-10-01)

### A1 — ACL Redis phải cho phép `ACL GETUSER` (điều kiện của ADR-0003 A1)
Lớp phòng ngự 3 của ADR-0003 được nâng thành **điều kiện khởi động**: server phải tự đọc
quyền của chính user Redis đang dùng và từ chối serve nếu user đó có category ghi. Muốn vậy
ACL phải cấp thêm `+acl|getuser` (allowlist v1 chỉ có `ACL WHOAMI`, không đủ — `WHOAMI` chỉ
trả về tên user, không trả về quyền).

ACL sau sửa (thêm `+acl|getuser` vào cuối):
```
ACL SETUSER mcp_ro on >… ~* -@all +get +mget +strlen +getrange +type +ttl +pttl +exists +scan
  +hget +hmget +hgetall +hscan +hlen +lrange +llen +smembers +sscan +scard +zrange +zcard
  +xrange +xlen +xinfo +object|encoding +memory|usage +dbsize +info +acl|whoami +acl|getuser
```
Command allowlist phía client thêm `ACL GETUSER` tương ứng.

### A2 — OpenSearch cấm thêm `scroll` và `point_in_time` (state phía server)
Hai API này **tạo state trên cluster** (scroll context / PIT context giữ segment lại), cùng
loại side effect đã khiến `script` bị cấm. Bổ sung vào danh sách từ chối cạnh
`script`/`scripted_metric`/`runtime_mappings`. Hệ quả: phân trang sâu dùng `search_after`
(ADR-0012 A4 đã chuyển checkpoint ingest sang `search_after` vì đúng lý do này).

### A3 — Kiểm chứng credential lúc khởi động (ADR-0003 A1)
| Nguồn | Kiểm lúc khởi động |
|---|---|
| Postgres | `SHOW transaction_read_only` = `on` **và** `has_table_privilege('kb.chunks','INSERT') = false` |
| Redis | `ACL WHOAMI` + `ACL GETUSER` không có category ghi (xem A1) |
| AWS | assert tool allowlist so với `sts get-caller-identity`; nếu có quyền, `iam:SimulatePrincipalPolicy` cho một action ghi và assert `implicitDeny` |

Thêm `sts:GetCallerIdentity` (và tuỳ chọn `iam:SimulatePrincipalPolicy`) vào IAM policy
read-only — cả hai đều không đọc/ghi dữ liệu nghiệp vụ.

### A4 — `StartQuery` phải có `StopQuery` shielded, và SDK đồng bộ cần executor có biên
- CloudWatch Logs Insights: `StopQuery` chạy trong `try/finally` + `asyncio.shield`, nếu
  không query bị bỏ rơi ở trạng thái running khi deadline huỷ coroutine (ADR-0006 A3).
- `boto3` là client **đồng bộ** chạy trong `asyncio.to_thread`: `asyncio.timeout` không huỷ
  được thread. Dùng `ThreadPoolExecutor` riêng `max_workers=4` cho mỗi server, không dùng
  executor mặc định (ADR-0006 A1). Timeout boto3 chốt lại: `connect 3 / read 7 /
  max_attempts 2`.
- Postgres: `connect_timeout=3`, `statement_timeout=15s`. Redis: connect 2s / read 5s.

## Amendments (reconcile contract_issue từ squad-backend, 2026-10-01)

### A5 — Redis: ACL cấp thêm `+select` (hỗ trợ `db` 0..15 như contract)
Contract cho `redis_scan_keys`/`redis_get_key`/`redis_key_info` nhận `db` 0..15. `redis-py` mở
connection tới db > 0 bằng cách gửi `SELECT <db>` trong handshake; ACL A1 không có `+select`
nên mọi call với `db > 0` trả `forbidden`. **Quyết định: thêm `+select` vào ACL**, không thu
hẹp contract về db 0.

- `SELECT` chỉ đổi trạng thái của **chính connection** (không ghi dữ liệu, không thuộc
  `@write`/`@dangerous`), nên không làm yếu NFR-001. Startup check (A3) không coi `+select` là
  quyền ghi (không nằm trong tập lệnh ghi của `acl_write_grants`).
- `SELECT` **không** được thêm vào command allowlist mà tool gọi qua `execute_command`: nó chỉ
  được phát bởi connection factory của client (một connection/pool riêng cho mỗi `db`). Tool
  không bao giờ tự gửi `SELECT`/`SWAPDB`/`MOVE`.
- Thu hẹp về db 0 bị loại: phá contract đã qua Gate B (breaking change với một field đã công bố)
  để tránh một quyền không có rủi ro ghi.

ACL sau sửa (thêm `+select`):
```
ACL SETUSER mcp_ro on >… ~* -@all +get +mget +strlen +getrange +type +ttl +pttl +exists +scan
  +hget +hmget +hgetall +hscan +hlen +lrange +llen +smembers +sscan +scard +zrange +zcard
  +xrange +xlen +xinfo +object|encoding +memory|usage +dbsize +info +acl|whoami +acl|getuser
  +select
```
Việc code: `infra/redis/users.acl` thêm `+select`; test live/integration cho `db=1`; hint
`"user ACL cần +select"` trong `client._guard` giữ nguyên (vẫn đúng với ACL cũ của người vận hành).

### A6 — SQS: `sqs:ListQueueTags` thuộc IAM policy read-only
`sqs_get_queue_attributes{include_tags: true}` gọi `ListQueueTags`. API này đã nằm trong
allowlist ở bảng Decision và trong `x-upstream` của contract; amendment này chốt rằng nó cũng
phải có trong **IAM policy read-only** do vận hành cấp (chỉ đọc metadata tag của queue, không
đọc message, không đổi visibility). Chỉ được gọi khi `include_tags=true`; thiếu quyền ⇒ cả
call trả `error.code=forbidden` (không bao giờ trả kết quả thiếu tag mà không báo, không bịa tag).

### A7 — OpenSearch `opensearch_search_dsl`: guardrail chốt theo bản đã implement
- **Feature flag**: `MCP_OPENSEARCH_ALLOW_DSL=false` mặc định ⇒ tool không được đăng ký. Bề mặt
  mặc định: `mcp-opensearch` 5 tool, Phase 2 = 24 tool, toàn hệ = 48 tool (bật flag: 6/25/49).
- **Phân trang**: `size` ∈ 0..100, mặc định = `limit`, `size > limit` bị **kẹp** về `limit`
  (kèm warning); `from` ∈ 0..900 (không bị ép ≤ `limit`); `from + size ≤ 1000`; vượt ⇒
  `invalid_input` (gợi ý `search_after`). Câu cũ "`size`/`from` bị ép ≤ `limit`" bị thay.
- **`search.allow_expensive_queries`**: là **cluster setting**, không có tham số per-request ⇒
  bỏ khỏi guardrail của tool (câu cũ không implement được). Guardrail thực sự là deny-list đệ
  quy (A2 + ADR-0003 A3) cộng `timeout` phía cluster đặt từ `timeout_s`. Nếu muốn chặn query
  đắt ở mức cluster, người vận hành đặt setting đó trên cluster.
- **`aggs`**: vẫn nằm trong allowlist khoá cấp cao nhất (tương thích DSL) nhưng **kết quả
  aggregation không được trả** — response chỉ có document hits; khi upstream có
  `aggregations`, tool thêm `meta.warnings` hướng dẫn dùng `opensearch_aggregate`. Không thêm
  slot aggregation vào response (tránh mở một kênh trả dữ liệu không có citation theo item).
