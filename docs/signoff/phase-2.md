# Sign-off Phase 2 — OpenSearch, Kibana, CloudWatch, Kafka, Redis (T-053)

Trạng thái: **phần tự động xong và xanh; phần thủ công cần credential thật/VPN/Docker/Claude
Desktop CHƯA làm được trong container của squad** (không có VPN, không có tài khoản
OpenSearch/Kibana/AWS, Docker daemon không chạy, không có Claude Desktop). Các mục thủ công để
trống có chủ đích, không được đánh dấu giả. Test `@pytest.mark.live` đã viết nhưng **chưa chạy**.

## A. Đã kiểm chứng tự động (`make ci` xanh)

| Mục | Bằng chứng |
|---|---|
| 25 tool Phase 2 (6+3+7+5+4), tổng 40 sau Phase 1+2, mỗi server trả lời `tools/list` | `packages/mcp_cloudwatch/tests/test_phase2_surface.py`; `test_settings_cli.py` của mỗi package chạy `serve` qua stdio thật |
| `opensearch_search_dsl` mặc định TẮT: mặc định Claude thấy **24** tool Phase 2 / 39 tổng; đủ 25/40 khi `MCP_OPENSEARCH_ALLOW_DSL=true` | `test_default_surface_hides_the_dsl_escape_hatch`, `test_dsl_tool_is_not_registered_unless_the_feature_flag_is_on` |
| 0 tool ghi; mọi tool là operation `x-readonly: true`, `x-side-effects: none` | `test_tools_readonly.py` của cả 5 package (`assert_readonly_tool_surface`) + `test_NFR_001_every_phase2_tool_is_a_readonly_contract_operation_and_none_is_a_write` |
| Gọi tool ghi không tồn tại bị SDK từ chối ở tầng JSON-RPC (FR-014 AC-002) | `test_FR_014_AC_002_*` (5-7 tên mỗi package) |
| OpenSearch: `script`/`scripted_metric`/`runtime_mappings`/`scroll`/PIT/terms-lookup bị chặn ở mọi độ sâu và ở tầng transport | `mcp_opensearch/tests/test_client.py` (17 body tấn công, 18 endpoint), `test_TC_018_*` |
| Kibana: chỉ 3 endpoint GET, không bao giờ gửi header xsrf | `mcp_kibana/tests/test_client.py`, `test_every_tool_call_only_issues_get_requests` |
| CloudWatch: API ngoài allowlist `not_permitted` cả ở code lẫn hook botocore; `SimulatePrincipalPolicy` trả `allowed` cho action ghi thì từ chối serve | `mcp_cloudwatch/tests/test_client.py` |
| CloudWatch: huỷ giữa Logs Insights vẫn gọi `StopQuery` đúng một lần (`try/finally` + `asyncio.shield`) | `test_TC_023_*` (huỷ trực tiếp và qua `asyncio.timeout`) |
| Kafka: không `Producer`, không `subscribe`, không commit, không `topic=` trong metadata (kiểm bằng AST); `allow.auto.create.topics=false` trên cả Admin và Consumer | `mcp_kafka/tests/test_client.py` (`test_TC_026_*`, `test_R17_*`) |
| Kafka R17: mô tả/peek topic không tồn tại trên cluster giả lập **auto-create bật** không tạo topic, danh sách topic trước/sau giống nhau | `test_FR_007_AC_002_missing_topic_*`, `test_FR_007_AC_003_a_full_tool_sweep_*` |
| Kafka: đổi adapter không sửa tầng tool (cùng bộ test chạy với adapter confluent và adapter in-memory không có thư viện Kafka) | fixture `api` hai tham số trong `mcp_kafka/tests/test_read_api.py` (TC-028) |
| Redis: mọi lệnh ghi (`SET/DEL/EXPIRE/...`) `not_permitted` ở code **và** bị ACL server từ chối; `KEYS` không xuất hiện trong mã; user ghi được thì từ chối serve | `mcp_redis/tests/test_client.py`, `test_local_redis.py` (redis-server 7 thật + `infra/redis/users.acl`) |
| Snapshot khớp contract; 4 nhánh `ok/empty/not_found/error` validate theo `api-contract.yaml` | `test_contract.py` của cả 5 package |
| NFR-002: mọi nguồn thất bại < 25s, `hint` nhắc VPN, deadline riêng theo tool, `timeout_s` kiểm theo deadline riêng | `test_timeout_budget.py` của cả 5 package; `test_NFR_002_every_source_fails_fast_below_the_25s_tool_deadline` |
| R16: N call treo → call N+1 trả `upstream_unavailable` ngay (CloudWatch, Kafka, và `BoundedExecutor` chung) | `test_R16_*` trong `mcp_cloudwatch`, `mcp_kafka`, `mcp_common/tests/test_runtime_executor.py` |
| Prompt `incident_investigation` (3 argument, chỉ thị nêu nguồn rỗng thành một dòng, citation từng mệnh đề, chỉ gọi tool có trong contract) | `mcp_cloudwatch/tests/test_prompts.py` |
| Bộ câu hỏi eval Phase 2 hợp lệ (12 câu; mỗi nguồn rỗng một case; Kafka lag; Redis cache) | `mcp_cloudwatch/tests/test_eval_phase2.py` |

Timeout theo nguồn (kiểm bằng hằng số trong code, không phải đo thật):

| Nguồn | Trường hợp xấu nhất khi endpoint chết | Cấu hình |
|---|---|---|
| Kibana | 2 x (3 + 7) + 1 = **21s** | `mcp_common.http` (ADR-0006 A2) |
| CloudWatch | 2 x (3 + 7) = **20s** | botocore `total_max_attempts=2`, connect 3 / read 7 (ADR-0008 A4) |
| OpenSearch | search/count `timeout_s + 1` = **21s** (mặc định); call rẻ 7s | không retry khi timeout, 1 retry khi lỗi kết nối |
| Kafka | **8s** mỗi call (`socket.timeout.ms=8000`) | ADR-0009 A3 |
| Redis | 2 + 5 = **7s** | connect 2 / read 5 (ADR-0008 A4) |

Tất cả < 25s (`MCP_TOOL_DEADLINE`); ngưỡng NFR-002 vẫn chờ PO chốt (`# THRESHOLD TBD (Open
question 1)` trong các test).

Kết quả gần nhất: 1317 test pass, 9 skip (`@pytest.mark.live`, lý do: cần credential/VPN/Docker);
coverage `mcp_opensearch` 95%, `mcp_kibana` 97%, `mcp_cloudwatch` 97%, `mcp_kafka` 97%,
`mcp_redis` 95%, `mcp_common` 96%; ruff, mypy, validate contract và suite read-only (132 test) xanh.

## B. Checklist thủ công (người có VPN + tài khoản thật điền)

- [ ] `uv run mcp-opensearch doctor` báo `read-only check: ok` (user không map role
      `all_access`/`security_manager`/`admin`; có quyền đọc `/_plugins/_security/authinfo`).
      Kết quả: ______
- [ ] `uv run mcp-kibana doctor` báo `ok`. Lưu ý: Kibana không có endpoint chứng minh quyền chỉ-đọc,
      kiểm tay role viewer. Kết quả: ______
- [ ] `uv run mcp-cloudwatch doctor` báo `ok` (`SimulatePrincipalPolicy` cho action ghi là
      `implicitDeny`). Nếu chỉ thấy cảnh báo "SimulatePrincipalPolicy not available" thì cấp thêm
      `iam:SimulatePrincipalPolicy` hoặc kiểm IAM policy bằng tay. Kết quả: ______
- [ ] `uv run mcp-kafka doctor` báo `ok` (ACL của principal chỉ `Describe` + `Read`, DENY `Create`).
      Kết quả: ______
- [ ] `uv run mcp-redis doctor` báo `ok` với user `mcp_ro` (có `+acl|getuser`). Kết quả: ______
- [ ] Chạy live test với `infra/docker-compose.yml` + `infra/docker-compose.kafka-autocreate.yml`:
      `MCP_LIVE_TESTS=1 uv run pytest -m live packages/mcp_kafka packages/mcp_redis` — gồm test âm R17
      trên broker auto-create bật (topic list trước/sau giống nhau) và TC-027 (offset group khác không
      đổi). Kết quả: ______
- [ ] Dán JSON từ `uv run mcp-common config-emit --server <tên>` cho cả 5 server vào
      `claude_desktop_config.json`; khởi động lại Claude Desktop/Code.
- [ ] 5 server hiện trong danh sách MCP của Claude: 25 tool Phase 2 (6+3+7+5+4; **24** nếu không bật
      `MCP_OPENSEARCH_ALLOW_DSL`), tổng 40 (39) sau Phase 1+2, 0 tool ghi (NFR-005).
- [ ] Prompt `incident_investigation` hiện trong danh sách prompt của `mcp-cloudwatch` (3 argument).
- [ ] Chạy bộ câu hỏi `eval/questions.yaml` mục `phase2_questions` theo
      `docs/claude-usage/journey-2.md`; lưu kết quả vào `eval/results/`; hoàn tất review tay (NFR-003).
- [ ] Với từng nguồn, đo thời gian thật khi tắt VPN: lỗi trả về < 25s và `hint` nhắc VPN (NFR-002).
- [ ] Chạy lại `make ci` trên máy dev: xanh.

Người ký: ______  Ngày: ______
