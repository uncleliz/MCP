# CHG-003 — Đóng cửa sổ theo dõi sau go-live (watch close)

Cửa sổ theo dõi 30 phút mở lúc 2026-10-02 15:48:35 (+07), hạn đóng 16:18:35. Giờ là
~22:25 — đã quá hạn từ lâu và DM đã kiểm tra nhanh thấy mọi bất biến còn nguyên. Đây là
lần đọc xác nhận cuối trước khi đóng cửa sổ ở trạng thái XANH.

## Năm trigger rollback (D-007 DK2) — kiểm lần cuối, tất cả XANH

1. Liveness: container mcp-dev-postgres Up 28h (healthy); db mcp_kb, pgvector 0.8.6, role
   read-only mcp_query_ro. Không sự cố.
2. Số dòng (SLI): 3 tài liệu / 27 chunk, ổn định. Cả 27 chunk mang model BAAI/bge-m3 sau
   lần re-embed NFR-003 lúc 22:03 — đây là trạng thái ĐÚNG sau re-embed, không phải trôi
   dữ liệu. Số dòng không đổi so với trước re-embed.
3. Egress chặn-mặc-định: allowlist rỗng chặn mọi host; với allowlist '*.atlassian.net' thì
   tnexwm.atlassian.net được phép, còn huggingface.co và evil.example.com bị từ chối. Một
   choke point duy nhất.
4. Token không rò: .token-key đã gitignore, không được track; quét toàn bộ file commit +
   evidence không thấy giá trị token (leak_flag=0).
5. Trạng thái ingest: lần chạy confluence gần nhất = success, 0 lỗi.

## NFR-003 — đo lần đầu (cỡ mẫu nhỏ)

Sau khi tải xong model bge-m3 (nạp offline), đã re-embed thật 27/27 chunk sang bge-m3 và đo
NFR-003 lần đầu trên 1 tài liệu EA thật (25 chunk): hit@5 = 8/8, MRR = 1.0, calibration_status
= uncalibrated. Đây là tín hiệu tích cực NHƯNG cỡ mẫu nhỏ, chưa hiệu chỉnh — KHÔNG suy rộng ra
chất lượng trên toàn corpus (giữ đúng L-002: không thổi phồng một phép đo chưa đủ dữ liệu). Theo
quyết định của CEO (Option 1), NFR-003 giữ ở mức 'đã đo lần đầu (cỡ mẫu nhỏ)', chưa phải
'verified trên dữ liệu công ty'.

## Kết luận

Verdict: XANH. Không có trigger nào bị vi phạm. KHÔNG cần rollback. Cửa sổ theo dõi đóng ở
trạng thái xanh. Go-live CHG-003 (first real EA pull) hoàn tất.

Bằng chứng: 20261002-222544-chg003-watch-close-sample.txt (số liệu đọc trực tiếp).
