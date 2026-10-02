#!/usr/bin/env bash
# kb-get — lấy nguyên văn một tài liệu đã ingest theo source_uri hoặc document_id.
# CHỈ ĐỌC, KHÔNG cần embedding (không phụ thuộc model nhúng).
#
# Dùng:
#   scripts/kb-get.sh --source-uri  "https://tnexwm.atlassian.net/wiki/spaces/EA/pages/1165426720/..."
#   scripts/kb-get.sh --document-id "1c274560-ba52-45b7-a084-5aa35ee17da4"
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ $# -eq 0 ]]; then
  echo "Dùng: $0 --source-uri <URI> | --document-id <UUID>  [--json]" >&2
  exit 1
fi
set -a; source credentials/.lookup.env; set +a
# Lọc một dòng cảnh báo dọn-dẹp vô hại của thư viện MCP khi tắt tiến trình con.
uv run python scripts/_kb_mcp.py get "$@" 2> >(grep -v 'Process group termination failed' >&2)
