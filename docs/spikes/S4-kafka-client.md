# Spike S4 — Kafka client: `confluent-kafka` vs `kafka-python` (T-032)

Ngày chạy: 2026-09-30. Môi trường: container của squad (Linux x86_64, glibc 2.39, Python 3.12.3,
`uv` 0.8.17). Docker daemon **không chạy**, không có broker Kafka → phần "hành vi thật trên
broker" được hoãn sang test `@pytest.mark.live` (xem "Giới hạn của spike").

## Quyết định

**Chọn `confluent-kafka` (librdkafka), pin `confluent-kafka>=2.15,<3`** trong
`packages/mcp_kafka/pyproject.toml` (bản thực đo: 2.15.1, librdkafka 2.15.1).
ADR-0009 cần chuyển `proposed` → `accepted` (việc của SA; BE không sửa `docs/adr/`).
`kafka-python` giữ làm phương án dự phòng sau port `KafkaReader` (`mcp_kafka/ports.py`).

## Bằng chứng

| Tiêu chí | `confluent-kafka` 2.15.1 | `kafka-python` 3.0.11 |
|---|---|---|
| Cài bằng `uv pip install` | OK, **wheel nhị phân** `cp312-manylinux_2_28_x86_64`, không cần build librdkafka (12 MB `.libs`) | OK, pure Python |
| `import` | 0.018 s | 0.166 s |
| Có trong `uv sync --all-packages` của workspace | OK (`uv.lock` cập nhật) | chưa thử trong workspace (không cần) |
| AdminClient: metadata cluster-wide | `list_topics(timeout=)` | `list_topics()` |
| Topic config | `describe_configs` | `describe_configs` |
| Consumer group list/describe | `list_consumer_groups`, `describe_consumer_groups` | `list_groups`, `describe_groups` |
| Committed offset của group (chỉ đọc) | `list_consumer_group_offsets([ConsumerGroupTopicPartitions])` | `list_group_offsets` |
| Watermark | `Consumer.get_watermark_offsets(tp, cached=False)` | `end_offsets`/`beginning_offsets` |
| `assign()` không `subscribe()` | có (`assign`, `seek`, `offsets_for_times`) | có |
| Không commit | `enable.auto.commit=false` (+ không gọi `commit`) | `enable_auto_commit=False` |
| Chặn auto-create topic (ADR-0009 A1) | `allow.auto.create.topics=false` nhận được trên cả `AdminClient` và `Consumer` (đã tạo thử cả hai object với cấu hình này, không lỗi) | Consumer mặc định **`allow_auto_create_topics=True`** → phải tắt tay; dễ quên |
| `describe_acls` cho startup assert | có (`AclBindingFilter`) | có |
| Timeout điều khiển được | `socket.timeout.ms` + tham số `timeout`/`request_timeout` từng call; dead broker: `list_topics(timeout=1)` ném `KafkaException(_TRANSPORT)` sau đúng 1.0 s | `request_timeout_ms` mặc định 30 000 ms (phải hạ tay) |
| `Producer` tồn tại trong package | **có** (`confluent_kafka.Producer`) → ADR-0009 yêu cầu test chặn import/khởi tạo | có (`KafkaProducer`) |

Phát hiện quan trọng cho T-046:

1. `metadata.request.timeout.ms` trong librdkafka 2.15 bị **deprecated, "Not used"** (cảnh báo
   `CONFWARN` khi tạo AdminClient). ADR-0009 A3 yêu cầu đặt 8000 cho nó — vẫn đặt để đúng ADR
   nhưng ràng buộc thật nằm ở `socket.timeout.ms=8000` và tham số `timeout=8` truyền vào từng call
   (`list_topics`, `request_timeout` của Admin call, `get_watermark_offsets`).
2. `confluent_kafka.Producer` luôn import được; bảo đảm "không Producer" phải bằng test quét mã
   nguồn (AST) chứ không thể dựa vào việc không cài.
3. `kafka-python` cũng dùng được (đủ API), nhưng default `allow_auto_create_topics=True` và
   timeout 30 s làm R17/NFR-002 dễ hỏng → là lý do phụ chọn `confluent-kafka`.

## Lệnh đã chạy (tái lập)

```bash
uv venv --python 3.12 /tmp/s4/v1
uv pip install --python /tmp/s4/v1/bin/python confluent-kafka kafka-python
/tmp/s4/v1/bin/python - <<'PY'
import confluent_kafka as c
from confluent_kafka.admin import AdminClient
a = AdminClient({"bootstrap.servers": "localhost:1", "allow.auto.create.topics": False,
                 "socket.timeout.ms": 8000})
cons = c.Consumer({"bootstrap.servers": "localhost:1", "group.id": "x",
                   "enable.auto.commit": False, "allow.auto.create.topics": False})
a.list_topics(timeout=1)   # -> KafkaException _TRANSPORT after 1.0 s (broker down)
PY
```

## Giới hạn của spike (nêu thẳng, không che)

- **Không có broker** trong container (Docker daemon không chạy, không có distribution Kafka),
  nên các điểm sau **chưa được đo trên broker thật** và được giao cho test live của T-048
  (`@pytest.mark.live`, chạy với `infra/docker-compose.yml`, `MCP_LIVE_TESTS=1`):
  `describe_consumer_groups` / `list_consumer_group_offsets` trên broker KRaft 3.7, hành vi
  `assign()+offset` không đổi committed offset của group khác (TC-027), topic không tồn tại không
  bị tạo (TC-025, R17).
- Tương thích broker nội bộ thật của team chưa kiểm được (Open question/S1 vẫn chưa đo).
- Nếu khi chạy live `confluent-kafka` lộ vấn đề, đổi sang `kafka-python` chỉ cần viết adapter mới
  cho port `KafkaReader`; `read_api.py`/`tools.py` không đổi (có test kiến trúc chứng minh
  tool layer không import client nào).

## Việc cho người khác

- SA: đóng ADR-0009 (`accepted`, chọn `confluent-kafka`, ghi chú `metadata.request.timeout.ms`
  deprecated).
- Người vận hành: chạy các test `@pytest.mark.live` của `mcp_kafka` với compose rồi dán kết quả
  vào mục này.
