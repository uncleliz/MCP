# Spike S2 — Embedding bake-off: `bge-m3` vs `multilingual-e5-large` (T-054)

Ngày chạy: 2026-10-01. Môi trường: container của squad (Linux x86_64, 4 CPU, 15 GiB RAM, ~30 GB
đĩa trống, Python 3.12, `uv`). Docker daemon **không chạy**.

## Trạng thái — đọc trước

| Hạng mục | Trạng thái |
|---|---|
| Script + quy trình bake-off | **Xong và có test** (`scripts/bakeoff_embedding.py`, `mcp_ingest.bakeoff`) |
| Đo thật recall@k / latency CPU / RAM / thời gian tải của 2 model | **CHƯA ĐO — pending** |
| Model chốt | **Tạm thời: `BAAI/bge-m3`** (provisional, chưa có số đo) |
| ADR-0010 | **Chưa thể đóng.** Cần số đo thật rồi SA đóng ADR (việc của SA, BE không sửa `docs/adr/`) |

Không có số nào trong tài liệu này được đo trên `bge-m3` hay `multilingual-e5-large`.

## Vì sao chưa đo được

Trọng số model phải tải từ Hugging Face Hub, mà egress của container bị chính sách tổ chức từ chối:

```
$ curl -sS -m 15 -o /dev/null -w "%{http_code}" https://huggingface.co/BAAI/bge-m3/resolve/main/config.json
000   (CONNECT tunnel failed, response 403)
$ curl -sS "$HTTPS_PROXY/__agentproxy/status"   # recentRelayFailures
{"kind":"connect_rejected","detail":"gateway answered 403 to CONNECT (policy denial or upstream failure)",
 "host":"huggingface.co:443"}     # tương tự cho cdn-lfs.huggingface.co:443
```

Đây là 403 do policy (không phải lỗi TLS/đĩa/RAM): theo hướng dẫn của proxy thì **không retry và
không đi vòng**; host bị chặn được báo lại ở đây. Phần cứng không phải điểm nghẽn: 15 GiB RAM đủ
cho một model ~2.2 GB fp32, và đĩa dư. PyPI thì tới được, nên `sentence-transformers`/`torch` cài
được; **chưa cài** vì không có trọng số để chạy.

Cách gỡ chặn (một trong hai): (a) cho phép `huggingface.co` + `cdn-lfs.huggingface.co` cho phiên
của squad; (b) tải sẵn hai thư mục model trên máy có mạng rồi đặt vào `~/.cache/huggingface`
(hoặc trỏ `HF_HOME`), chạy với `HF_HUB_OFFLINE=1`.

## Công cụ đã làm (và đã kiểm chứng bằng test)

* `packages/mcp_ingest/src/mcp_ingest/bakeoff.py` — đo **recall@k** (k = 1, 5, 10), **MRR**,
  **latency CPU** (mỗi document, mỗi query: trung bình + p95), **thời gian tải** (dựng provider +
  lần embed đầu tiên, tức lúc trọng số lazy-load thật sự được đọc) và **RAM** (RSS tăng thêm + đỉnh).
  Mỗi model chạy trong **tiến trình riêng** vì peak RSS là high-water mark của tiến trình.
* `scripts/bakeoff_embedding.py` — entry point mỏng; in bảng Markdown, `--out` ghi JSON đầy đủ.
* `eval/embedding_bakeoff.sample.yaml` — bộ mẫu **tổng hợp** (12 đoạn, 10 câu hỏi Việt/Anh) chỉ để
  chứng minh harness chạy; **không** dùng để chọn model.
* Test: `packages/mcp_ingest/tests/test_bakeoff.py` (11 test; metric, nạp dataset, đo, bảng, chạy
  cô lập từng model qua subprocess, lỗi). Dùng `DeterministicFakeProvider`; **không test nào tải
  model hay đụng mạng**.

Chạy thử harness (provider giả, dữ liệu tổng hợp — chỉ để chứng minh pipeline đo hoạt động):

```
$ uv run python scripts/bakeoff_embedding.py --models fake/hashed-bow --dataset eval/embedding_bakeoff.sample.yaml
| model | dim | recall@1 | recall@5 | recall@10 | MRR | doc ms (CPU) | query ms mean | query ms p95 | load s | RSS +MB | peak RSS MB |
| fake/hashed-bow | 1024 | 0.600 | 1.000 | 1.000 | 0.950 | 0.1 | 0.1 | 0.4 | 0.0 | 0 | 34 |
```

## Hai ứng viên (đặc tính theo model card công khai — CHƯA kiểm chứng trong container này)

| | `BAAI/bge-m3` | `intfloat/multilingual-e5-large` |
|---|---|---|
| Số chiều | 1024 | 1024 |
| Họ model / cỡ | XLM-RoBERTa-large, ~568M tham số | XLM-RoBERTa-large, ~560M tham số |
| Trọng số fp32 | ~2.2 GB | ~2.2 GB |
| Độ dài đầu vào tối đa | 8192 token | 512 token |
| Tiền tố | không cần | **bắt buộc** `query: ` / `passage: ` |
| Giấy phép | MIT | MIT |

Điều chỉnh với ADR-0010: ADR viết e5 "nhẹ hơn bge-m3". Theo số tham số ở trên hai model **cùng cỡ**,
nên đây là một giả định chưa có căn cứ; số đo RAM/latency của bake-off sẽ trả lời. Cả hai cùng 1024
chiều nên `kb.chunks.embedding vector(1024)` **không đổi** dù chọn model nào; đổi model chỉ cần
`mcp-ingest reembed` (T-080).

## Quyết định tạm thời và lý do

**Tạm chọn `BAAI/bge-m3`** (đặt làm `DEFAULT_MODEL` trong `mcp_ingest/embedding/config.py` và trong
`.env.example`). Lý do — đều là lý lẽ thiết kế, không phải số đo:

1. Giới hạn 8192 token cho phép chunk dài (tài liệu Confluence, README, MR description) mà không bị
   cắt âm thầm; e5 cắt ở 512 token — chunk lớn hơn sẽ mất đuôi mà không báo lỗi.
2. Không cần tiền tố. E5 dùng sai/thiếu tiền tố làm giảm chất lượng rõ rệt (rủi ro vận hành). Code
   vẫn **tự gắn tiền tố** cho model có `e5` trong tên (test `test_e5_models_get_query_and_passage_...`)
   nên đổi sang e5 không có bẫy này.
3. Là mặc định đã đề xuất trong ADR-0010; chọn nó khiến nhánh không bị đo xong vẫn nhất quán với
   tài liệu hiện hành.

Rủi ro nếu quyết định tạm thời sai: phải `reembed` toàn bộ kho — chi phí đã được thiết kế sẵn
(không migrate schema; `kb.chunks.embedding_model` + startup check của `mcp-pgvector` bắt lệch).
**Không embed toàn bộ corpus thật trước khi bake-off chạy xong** (R6).

## Quy trình chạy bake-off thật (người có mạng tới Hugging Face / có sẵn trọng số)

1. Môi trường: `uv sync --package mcp-ingest --extra local-embeddings` (kéo `sentence-transformers`
   + `torch`). Máy CPU thật, đóng bớt tiến trình khác; ghi lại `nproc`, RAM, phiên bản torch.
   `OMP_NUM_THREADS=<số core>` để latency so sánh được giữa hai lần chạy.
2. Dataset thật `eval/embedding_bakeoff.yaml` (schema như file sample), **lấy từ dữ liệu của team**:
   * `corpus`: xuất 200-500 đoạn từ Confluence/GitLab của team (qua `confluence_get_page` /
     `gitlab_get_file`, chia theo đúng chunker dự kiến; chỉ lấy nội dung **team-visible** — xem S5);
   * `queries`: lấy từ bộ câu hỏi NFR-003 (`eval/questions.yaml`, Q-J1-*) + câu hỏi Việt/Anh/lẫn;
     `relevant` = id đoạn mà **người** xác nhận là câu trả lời. Tối thiểu 30 câu, có cả câu tiếng
     Việt có dấu, tiếng Anh, thuật ngữ kỹ thuật/tên hàm/mã lỗi.
   * Không commit nếu chứa nội dung nội bộ nhạy cảm.
3. Chạy:
   ```
   uv run python scripts/bakeoff_embedding.py \
       --models BAAI/bge-m3,intfloat/multilingual-e5-large \
       --dataset eval/embedding_bakeoff.yaml --ks 1,5,10 \
       --out docs/spikes/S2-embedding-bakeoff.result.json
   ```
   Chạy 2 lần (lần 1 tải từ mạng, lần 2 từ cache) để tách **thời gian tải model** khỏi **thời gian
   đọc từ đĩa**; ghi cả hai.
4. Dán bảng kết quả vào mục "Kết quả" bên dưới (xoá dòng "CHƯA ĐO").
5. Quy tắc quyết định (đề xuất, vì NFR-003 chưa có ngưỡng PO): chọn model có **recall@5** cao hơn;
   nếu chênh < 0.02 thì chọn model có p95 latency/RAM thấp hơn; nếu vẫn hoà giữ `bge-m3`. Cả hai
   phải đạt recall@10 ≥ 0.80 trên bộ này, nếu không bộ chunk/cách chia mới là vấn đề, không phải model.
6. Nếu e5 thắng: đổi `DEFAULT_MODEL` (1 dòng) + `.env.example`, chạy `mcp-ingest reembed`;
   không có thay đổi schema. Báo SA đóng ADR-0010 với model + `model_id`/`dimensions` đã chốt.

## Kết quả

CHƯA ĐO. (Chỗ để dán bảng ở bước 4.)

## Việc cho SA / PO sau spike này

* SA: ADR-0010 vẫn `proposed`. Cập nhật khi có số đo; ghi nhận các điều chỉnh đã làm ở code:
  cấu hình embedding **dùng chung** qua `MCP_INGEST_EMBEDDING_*` giữa `mcp-ingest` và `mcp-pgvector`
  (kèm override `MCP_PGVECTOR_EMBEDDING_MODEL`), `mcp_ingest.embedding` không import `psycopg`
  (R19, có test), provider giả deterministic cho test, và `mcp-pgvector` từ chối serve khi model hoặc
  số chiều lệch dữ liệu (không thể bỏ qua bằng `MCP_ALLOW_UNVERIFIED_CREDENTIALS`).
* User/PO: cấp quyền mạng tới Hugging Face (hoặc cung cấp trọng số offline) để chạy bước 3.
