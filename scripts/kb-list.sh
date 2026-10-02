#!/usr/bin/env bash
# kb-list — liệt kê các nguồn đã ingest + thống kê (số tài liệu, số chunk, model nhúng, độ mới).
# CHỈ ĐỌC, KHÔNG cần embedding. Dùng:  scripts/kb-list.sh
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; source credentials/.lookup.env; set +a
# Lọc một dòng cảnh báo dọn-dẹp vô hại của thư viện MCP khi tắt tiến trình con (không ảnh hưởng kết quả).
uv run python scripts/_kb_mcp.py list "$@" 2> >(grep -v 'Process group termination failed' >&2)
