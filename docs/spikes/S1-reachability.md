# Spike S1 — Reachability/VPN của 5 nguồn remote (T-001)

Trạng thái: **script đã xong, kết quả đo thật CHƯA có** (cần chạy trên máy dev có VPN).
Trả lời Open question 4 (architecture.md R1) sau khi bảng bên dưới được điền.

## Cách chạy

```bash
cp .env.example .env            # điền URL + credential read-only thật; không commit .env
set -a; . ./.env; set +a
uv run python scripts/probe_reachability.py --timeout 5
```

Script `scripts/probe_reachability.py` độc lập với `mcp_common` (chỉ httpx, boto3 nếu có).
Mỗi nguồn kiểm DNS, TCP, TLS, một GET rẻ nhất và thời gian phản hồi; exit code 0 khi mọi nguồn
là `reachable` hoặc `not-configured`. Secret không bao giờ được in ra.

| Nguồn | Endpoint GET rẻ nhất | Biến cấu hình |
|---|---|---|
| Confluence | `/rest/api/space?limit=1` | `MCP_CONFLUENCE_BASE_URL`, `_EMAIL`, `_API_TOKEN` |
| GitLab | `/api/v4/version` | `MCP_GITLAB_BASE_URL`, `_PRIVATE_TOKEN` |
| OpenSearch | `/` | `MCP_OPENSEARCH_HOSTS`, `_USERNAME`, `_PASSWORD` |
| Kibana | `/api/status` | `MCP_KIBANA_BASE_URL`, `_USERNAME`, `_PASSWORD` |
| CloudWatch | `logs:DescribeLogGroups(limit=1)` | `MCP_CLOUDWATCH_REGION`, `_AWS_ACCESS_KEY_ID`, `_AWS_SECRET_ACCESS_KEY` |

Phân loại: `reachable` (2xx/3xx/4xx không phải 401/403), `auth-fail` (401/403 hoặc lỗi
credential AWS, mạng vẫn thông), `unreachable` (DNS/TCP/TLS fail, timeout, 5xx),
`not-configured` (thiếu biến).

## Bảng reachability (điền sau khi chạy trên máy dev)

Lần chạy trong container của squad (không có VPN, không có URL nguồn thật) cho kết quả
`not-configured` cho cả 5 nguồn, nên chưa thể kết luận.

| Nguồn | DNS | TCP | TLS | HTTP | Latency (ms) | Status | Ghi chú |
|---|---|---|---|---|---|---|---|
| Confluence | - | - | - | - | - | chưa đo | cần chạy trên máy dev |
| GitLab | - | - | - | - | - | chưa đo | cần chạy trên máy dev |
| OpenSearch | - | - | - | - | - | chưa đo | cần chạy trên máy dev |
| Kibana | - | - | - | - | - | chưa đo | cần chạy trên máy dev |
| CloudWatch | - | - | - | - | - | chưa đo | cần chạy trên máy dev |

## Kết luận (chờ số liệu)

- Nguồn `unreachable` sau khi chạy: unit test vẫn làm được bằng `respx` + fixture payload thật,
  nhưng **integration test (`@pytest.mark.live`) bị hoãn** và phải nêu trong
  `regression-report.md`.
- Nguồn `auth-fail`: mạng thông, cần cấp lại credential read-only trước khi chạy live test.
- Sau này chuyển logic probe vào lệnh `doctor` của từng server rồi xoá script này (theo T-001).

## Việc cần người thực hiện

Chạy script trên máy dev có VPN, dán bảng thật vào mục trên và cập nhật phần Kết luận.
