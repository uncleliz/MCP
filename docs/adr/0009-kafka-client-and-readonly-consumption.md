# ADR-0009: Kafka client + giao thức tiêu thụ read-only

**Date**: 2026-10-01
**Status**: accepted (2026-10-01) — `confluent-kafka>=2.15,<3`, theo spike S4 ([`docs/spikes/S4-kafka-client.md`](../spikes/S4-kafka-client.md))
**Deciders**: SA (squad-sa) đề xuất; PO/user quyết

## Context

FR-007 cần đọc topic metadata, consumer group/lag và **peek** message sample, kèm bảo đảm
không produce message và không làm lệch offset của consumer khác (FR-007 AC-003). Ba lựa chọn
client Python: `confluent-kafka` (binding librdkafka), `kafka-python`, `aiokafka`.
Yêu cầu kỹ thuật quyết định: cần `AdminClient` để lấy topic config, partition, watermark và
`list_consumer_group_offsets` (tính lag) — không phải client nào cũng có đủ.

## Decision

Dùng **`confluent-kafka`** với giao thức read-only sau:

- Chỉ khởi tạo `AdminClient` và `Consumer`; **không import/khởi tạo `Producer` ở bất kỳ đâu**
  (test kiến trúc grep chặn `from confluent_kafka import Producer`).
- Metadata/lag: `AdminClient.list_topics`, `describe_configs`, `list_consumer_groups`,
  `describe_consumer_groups`, `list_consumer_group_offsets` (chỉ đọc), `Consumer.get_watermark_offsets`.
- Peek message: `Consumer` với `group.id = f"mcp-readonly-{uuid4()}"`,
  `enable.auto.commit=false`, `auto.offset.reset=error`, dùng **`assign()`** (không `subscribe()`)
  để không tham gia consumer group rebalance, `seek()` tới offset/timestamp yêu cầu, đọc n
  message rồi `close()`. Không gọi `commit()` ở bất kỳ nhánh code nào.
- Số lượng message và kích thước bị chặn (`limit ≤ 100`, `max_bytes ≤ 256 KiB`), value được
  decode theo `value_format=auto|json|utf8|base64` và đi qua redaction (ADR-0015).
- Auth: hỗ trợ `PLAINTEXT`, `SASL_SSL/SCRAM-SHA-512`, `SASL_SSL/PLAIN` qua cấu hình; ACL phía
  Kafka nên chỉ cấp `Describe` + `Read` trên topic pattern cần thiết.

## Alternatives Considered

### Alternative 1: `kafka-python` (pure Python)
- **Pros**: Không cần binary wheel, cài đặt dễ nhất, `KafkaAdminClient` có sẵn.
- **Cons**: Nhịp bảo trì chậm, hỗ trợ broker mới và KIP mới trễ; `list_consumer_group_offsets`
  có nhưng ít được kiểm chứng ở broker mới; hiệu năng kém hơn.
- **Why not**: Rủi ro tương thích broker nội bộ; nhưng **là phương án thay thế tốt nếu wheel
  của librdkafka gây vấn đề khi cài**.

### Alternative 2: `aiokafka`
- **Pros**: Async thuần, khớp event loop MCP.
- **Cons**: Admin API hạn chế (thiếu `describe_configs` đầy đủ, lag phải tự tính), cộng đồng nhỏ hơn.
- **Why not**: Thiếu năng lực admin mà FR-007 cần.

### Alternative 3: Gọi Kafka qua REST Proxy / Kafka UI API
- **Pros**: Chỉ cần HTTP, dùng luôn client chung.
- **Cons**: Cần một service proxy tồn tại và được cấp quyền; REST Proxy có endpoint produce
  → phải chặn ở tầng khác; thêm hop.
- **Why not**: Không rõ team có REST Proxy; thêm phụ thuộc hạ tầng.

## Consequences

### Positive
- Đủ năng lực cho topic metadata + consumer lag + peek mà không cần service phụ.
- `assign()` + không commit → offset của consumer khác không bị ảnh hưởng, đúng FR-007 AC-003.

### Negative
- `confluent-kafka` là extension C → cần wheel phù hợp (có wheel macOS arm64 và manylinux);
  môi trường lạ có thể phải build librdkafka.
- Client sync → phải bọc trong `asyncio.to_thread`.

### Risks
- Nếu cài đặt `confluent-kafka` thất bại trên máy dev, phải đổi sang `kafka-python` — vì vậy
  client Kafka được đặt sau một interface `KafkaReader` trong `mcp_kafka/ports.py` để đổi
  implementation không lan ra tool layer.
- Peek trên topic có message rất lớn có thể vượt `max_bytes`; trả `status=partial` + cảnh báo.

## Amendments (sau design review 2026-10-01)

### A1 — Auto-create topic là một thao tác GHI: phải chặn ở ba chỗ
`AdminClient.list_topics(topic="x")` gửi metadata request **theo tên topic**; broker có
`auto.create.topics.enable=true` (mặc định của nhiều bản phân phối) sẽ **tạo topic đó**. Nguy
hiểm nhất là chính test âm của FR-007 AC-002 ("topic không tồn tại → not_found") đi đúng vào
đường này: bộ test read-only có thể tự tạo topic trên cluster thật. Bắt buộc cả ba:

1. `allow.auto.create.topics=false` trên **cả** `Consumer` và `AdminClient`.
2. **Không bao giờ** truyền `topic=` vào metadata request — gọi cluster-wide
   `list_topics()` rồi filter client-side theo tên.
3. ACL phía Kafka deny `Create` trên cả `Cluster` và `Topic` (bổ sung cho
   `Describe` + `Read` đã nêu ở Decision).

Test: một case assert `kafka_describe_topic` với tên topic không tồn tại trả `not_found`
**và** không có metadata request nào mang tham số `topic`.

### A2 — Kiểm chứng cấu hình lúc khởi động (ADR-0003 A1/A3)
`build_server()` gọi `describe_acls` nếu ACL cho phép; nếu không được phép, tối thiểu assert
cấu hình client đang dùng (`allow.auto.create.topics=false`, `enable.auto.commit=false`,
không có `Producer` nào được khởi tạo) trước khi serve.

### A3 — `confluent-kafka` là client đồng bộ: cần executor có biên (ADR-0006 A1/A2)
librdkafka chạy trong `asyncio.to_thread`, nên `asyncio.timeout` **không** huỷ được call đang
chạy. Dùng `ThreadPoolExecutor` riêng `max_workers=4`, queue giới hạn; queue đầy → trả ngay
`upstream_unavailable`. Timeout chốt: `socket.timeout.ms=8000`,
`metadata.request.timeout.ms=8000` (nằm trong budget 21s của ADR-0006 A2).

Ghi chú về trạng thái: ADR này vẫn **proposed** — spike S4 (đầu Phase 2) chốt
`confluent-kafka` hay `kafka-python`. Cả ba amendment trên áp dụng cho **cả hai** lựa chọn;
`kafka-python` có tham số tương đương (`allow_auto_create_topics=False`) nhưng phải kiểm lại
vì mặc định của nó là `True`.

## Amendments (chốt sau spike S4, 2026-10-01)

### A4 — Accepted với `confluent-kafka`; ghi chú từ S4
- Spike S4 (T-032) chốt **`confluent-kafka>=2.15,<3`** (đo bản 2.15.1 / librdkafka 2.15.1, wheel
  nhị phân manylinux, không cần build). `kafka-python` giữ làm phương án dự phòng sau port
  `KafkaReader` (`mcp_kafka/ports.py`); đổi client không chạm `read_api.py`/`tools.py`.
- `metadata.request.timeout.ms` **deprecated / "Not used"** trong librdkafka 2.15. Vẫn đặt 8000
  cho đúng A3, nhưng ràng buộc thật là `socket.timeout.ms=8000` **cộng tham số `timeout` của
  từng call** (`list_topics`, `request_timeout` của Admin call, `get_watermark_offsets`) — tất cả
  phải nằm trong budget 21s của ADR-0006 A2.
- `confluent_kafka.Producer` luôn import được ⇒ bảo đảm "không Producer" là **test quét AST mã
  nguồn**, không dựa vào việc không cài.
- Lý do phụ loại `kafka-python`: mặc định `allow_auto_create_topics=True` và
  `request_timeout_ms=30000` — dễ phá R17/NFR-002.
- Rủi ro còn lại: S4 không có broker; hành vi trên broker thật (TC-025, TC-027, describe/list
  group offsets) do test `@pytest.mark.live` của T-048 xác nhận.
