# Runbook vận hành pipeline ingest (T-082)

> Lệch so với `implementation-plan.md`: plan ghi file này là `docs/runbook-ingest.md`. Phạm vi ghi
> của `squad-backend` không gồm các đường dẫn trực tiếp dưới `docs/` ngoài `spikes/` và
> `signoff/`, nên file nằm ở `infra/scheduler/runbook-ingest.md`. Nội dung không đổi; Lead/SA có
> thể chuyển nếu hạn chế này là ngoài ý muốn.

Phạm vi: `mcp-ingest` (FR-012) — thành phần **duy nhất** ghi vào kho `kb`. 9 MCP server chỉ đọc.

## 1. Lịch chạy

| Việc | Mẫu | Lệnh |
|---|---|---|
| Incremental mỗi giờ | `crontab.example`, `com.mcp.ingest.plist` | `run-ingest.sh incremental` |
| Full reconcile 03:00 mỗi ngày | `crontab.example`, `com.mcp.ingest.full.plist` | `run-ingest.sh full` |

Đây là đề xuất của SA. **Cadence cuối cùng chờ PO xác nhận** (`THRESHOLD TBD`, Open question 5, ngưỡng độ mới
NFR-004); cho tới lúc đó dùng mặc định trên và đọc độ mới bằng `mcp-ingest status`.

Cài đặt (cron): `crontab -e`, dán 2 dòng của `crontab.example` sau khi thay `/path/to/MCP`.
Cài đặt (launchd, macOS): thay `/path/to/MCP` trong cả hai plist, chép vào
`~/Library/LaunchAgents/`, `launchctl load ~/Library/LaunchAgents/com.mcp.ingest.plist` (và
`.full.plist`). Máy ngủ thì lần chạy bị bỏ lỡ — incremental theo watermark nên lần sau tự bù.

`run-ingest.sh` đọc môi trường từ `~/.config/mcp-ingest/env` (đổi bằng `MCP_INGEST_ENV_FILE`) và ghi
báo cáo JSON, log JSON và `history.jsonl` vào `~/.local/state/mcp-ingest/` (đổi bằng
`MCP_INGEST_LOG_DIR`).

## 2. Quy tắc credential (không thương lượng)

* File env của ingest chứa `MCP_INGEST_PGVECTOR_DSN` (role **`mcp_ingest_rw`**), credential
  **chỉ đọc** của các nguồn (`MCP_CONFLUENCE_*`, `MCP_GITLAB_*`, ...), và `MCP_INGEST_EMBEDDING_*`.
* **Không bao giờ** đưa `mcp_ingest_rw` (hay `MCP_INGEST_PGVECTOR_DSN` / `MCP_INGEST_ADMIN_DSN`) vào
  `env` của bất kỳ MCP server nào trong cấu hình Claude Desktop/Code. `mcp-pgvector` dùng
  `mcp_query_ro` qua `MCP_PGVECTOR_DSN`, và **từ chối khởi động** nếu được dán DSN có quyền ghi
  (ADR-0003 A1). `scripts/verify_tool_surface.py` kiểm điều này trên mọi cấu hình mẫu.
* `MCP_INGEST_ADMIN_DSN` chỉ dùng cho `mcp-ingest db upgrade`, chạy tay khi nâng schema; không đặt
  nó trong file env của scheduler.
* File env quyền `600`. Không commit.

## 3. Đọc exit code

`run-ingest.sh` trả đúng exit code của `mcp-ingest run`; báo cáo ở `run-<mode>-<ts>.json`
(schema `IngestRunReport`), log ở `run-<mode>-<ts>.log`.

| Exit | Nghĩa | Việc cần làm |
|---|---|---|
| **0** success | Mọi nguồn bật đều chạy hết, không document nào lỗi | Không cần làm gì. |
| **1** partial | Có nguồn lỗi hoặc có document lỗi (nguồn khác vẫn xong) | Xem mục 4. |
| **2** failed | Không nguồn nào chạy được (config, DB, model lệch dữ liệu...) | Xem mục 5. |
| **3** lock | Lần chạy trước còn giữ lock của nguồn (`pg_try_advisory_lock`) | Xem mục 6. |

## 4. Xử lý `partial` (exit 1)

1. Mở báo cáo: `jq '.sources[] | {source_type,status,documents_failed,errors}' run-*.json`.
2. **Một nguồn `failed`/`partial` với lỗi `crawl`/`connect`**: thường là VPN/credential/hết quota.
   Sửa nguyên nhân; checkpoint của nguồn đó **không** bị nâng nên lần chạy kế tiếp đi lại đúng chỗ,
   không cần làm gì thêm.
3. **`documents_failed > 0`**: document lỗi được ghi vào `kb.ingest_failures` (stage, code,
   `attempts`). Lấy lại bằng:
   `uv run mcp-ingest run --source <nguồn> --retry-failed`
   Hàng được xoá khi ingest thành công. Document vẫn lỗi sau nhiều lần → đọc `last_error`:
   `SELECT source_id, stage, code, attempts, last_error FROM kb.ingest_failures;`
4. **`code = blocked_by_policy`** (stage `redact`) **không phải lỗi**: document bị chặn có chủ ý
   (deny-glob, hoặc `visibility != 'team'` — corpus chỉ chứa nội dung cả team đọc được, ADR-0016).
   Không "sửa" bằng cách nới deny-glob; nếu một document cần vào corpus, nhãn ở nguồn phải đúng
   (space/project khai báo là team).
5. **Lỗi `reconcile` ("safety valve")**: lần full vừa rồi thấy ít hơn 80% số document đang có nên
   *không* tombstone gì. Kiểm tra nguồn có đang trả thiếu không (quyền token đổi? space bị gỡ?).
   Nếu việc giảm là thật (xoá hàng loạt có chủ ý), xoá tay các document đó rồi chạy lại full.
6. `mcp-ingest status` cho biết nguồn nào đã quá cũ (`staleness_hours`).

## 5. Xử lý `failed` (exit 2)

* `error: config error ...` → thiếu biến env; thông báo nêu đúng tên biến. `mcp-ingest sources`
  liệt kê connector nào còn thiếu gì.
* `configured embedding model ... differs from the model(s) stored` → đã đổi model mà chưa re-embed:
  `uv run mcp-ingest reembed --model <model> [--source ...]` (an toàn khi bị ngắt, chạy lại tiếp
  tục). Trong lúc re-embed dở, `mcp-pgvector` từ chối phục vụ (kho trộn hai không gian vector).
* `cannot connect to the database` → kiểm tra Postgres và `MCP_INGEST_PGVECTOR_DSN` (không in DSN).
* Chưa migrate: chạy tay `MCP_INGEST_ADMIN_DSN=... uv run mcp-ingest db upgrade`.

## 6. Xử lý `lock` (exit 3)

Lần chạy trước (hoặc một lần chạy tay) còn chạy. Đây thường vô hại: lần này không làm gì cả.
Nếu lặp lại nhiều giờ liền: `SELECT pid, granted FROM pg_locks WHERE locktype = 'advisory';` (một session giữ lock mỗi
nguồn đang chạy) hoặc xem tiến trình `mcp-ingest` còn sống không. Lock gắn với session Postgres nên tự nhả khi process chết; không cần xoá tay.

## 7. HNSW và `maintenance_work_mem` (R12)

`0003_indexes.sql` tạo index HNSW trên bảng rỗng (tức thì). Nạp lần đầu hàng trăm nghìn chunk
bằng cách chèn dần vào index thì chậm và tốn RAM; khi nạp lô đầu rất lớn, dùng quy trình sau (bằng
role sở hữu bảng/admin, **không** phải `mcp_ingest_rw`):

```sql
DROP INDEX kb.chunks_embedding_hnsw;
-- ... chạy mcp-ingest run (nạp lô đầu) ...
SET maintenance_work_mem = '2GB';      -- riêng cho session build; chỉnh theo RAM máy
CREATE INDEX chunks_embedding_hnsw ON kb.chunks
  USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);
ANALYZE kb.chunks;
```

Trong lúc không có index, `kb_semantic_search` vẫn đúng (quét tuần tự) nhưng chậm. Sau khi xoá
hàng loạt (`mcp-ingest prune`, báo `reindex_recommended`):
`SET maintenance_work_mem = '2GB'; REINDEX INDEX CONCURRENTLY kb.chunks_embedding_hnsw;`

## 8. Dọn dẹp (`prune`)

Không có `prune` thì `kb` chỉ tăng. Lệnh **không có mặc định ngầm**: bắt buộc `--older-than`, và
mặc định là dry-run.

```bash
uv run mcp-ingest prune --tombstoned --older-than 30d             # dry-run: chỉ báo số hàng
uv run mcp-ingest prune --tombstoned --older-than 30d --no-dry-run
```

`--tombstoned` chỉ xoá bia mộ (`deleted_at`) đủ tuổi; thiếu cờ này, lệnh áp dụng cho document còn
sống mà **không crawl nào thấy** trong khoảng đó (mồ côi), không bao giờ cho document chỉ đơn giản
là không đổi ở nguồn. Giá trị retention mặc định chờ PO (`# THRESHOLD TBD`).

## 9. Kiểm tra sau khi cài lịch

1. `uv run mcp-ingest sources` — connector cần dùng phải `configured=True`.
2. `infra/scheduler/run-ingest.sh incremental; echo $?` — chạy tay một lần; xem `history.jsonl`.
3. `uv run mcp-ingest status` — `last_success_at` mới, `failures=0` hoặc giải thích được.
4. `SELECT source_type, status, documents_seen, finished_at FROM kb.ingest_runs ORDER BY started_at
   DESC LIMIT 5;` — mỗi nguồn mỗi lần chạy có đúng một hàng.
