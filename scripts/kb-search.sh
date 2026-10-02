#!/usr/bin/env bash
# kb-search — tra cứu ngữ nghĩa knowledge base (Confluence EA đã ingest) từ terminal. CHỈ ĐỌC.
#
# Dùng:   scripts/kb-search.sh "câu truy vấn" [số-kết-quả]
# Ví dụ:  scripts/kb-search.sh "ZNS config" 5
#
# Cách hoạt động (trung thực): script tự khởi động một endpoint nhúng (embedding) loopback giống
# HỆT lúc ingest CHG-003 — DeterministicFakeProvider, model "fake/hashed-bow", 1024 chiều — rồi
# chạy mcp-pgvector (role CHỈ ĐỌC mcp_query_ro) qua stdio để nhúng câu hỏi cùng không gian vector
# với dữ liệu đã lưu. Nhờ vậy vector truy vấn khớp vector đã lưu và trả về đúng chunk EA thật.
#
# NFR-003 (trung thực): "fake/hashed-bow" chỉ là chồng-lặp-từ, KHÔNG có ngữ nghĩa học máy. Thứ hạng
# là THẬT-NHƯNG-CHƯA-ĐO cho tới khi tải model BAAI/bge-m3 thật và re-embed. Nội dung + trích dẫn trả
# về là dữ liệu EA ingest thật trên tnexwm.atlassian.net.
set -euo pipefail
cd "$(dirname "$0")/.."

QUERY="${1:-}"
TOPK="${2:-5}"
if [[ -z "$QUERY" ]]; then
  echo "Dùng: $0 \"câu truy vấn\" [số-kết-quả]" >&2
  exit 1
fi

# Nạp DSN read-only (file ẩn, git-ignored). KHÔNG in secret.
set -a; source credentials/.lookup.env; set +a

# Lọc một dòng cảnh báo dọn-dẹp vô hại của thư viện MCP khi tắt tiến trình con.
uv run python scripts/_kb_mcp.py search "$QUERY" "$TOPK" 2> >(grep -v 'Process group termination failed' >&2)
