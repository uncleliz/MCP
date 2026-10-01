#!/usr/bin/env bash
# Scheduler entry point for `mcp-ingest run` (T-082, FR-012/AC-002, NFR-004).
#
#   run-ingest.sh incremental|full [extra `mcp-ingest run` arguments...]
#
# * Loads the INGEST environment from $MCP_INGEST_ENV_FILE (default ~/.config/mcp-ingest/env).
#   That file holds MCP_INGEST_PGVECTOR_DSN (role mcp_ingest_rw) and the source credentials. It is
#   a different file from the environment of every MCP server: mcp_ingest_rw must never appear in
#   an MCP server's env (ADR-0003 A1). MCP_INGEST_ADMIN_DSN does not belong here either: only
#   `mcp-ingest db upgrade` reads it and that is run by hand.
# * Writes the JSON report, the JSON logs and one history line per run to $MCP_INGEST_LOG_DIR
#   (default ~/.local/state/mcp-ingest), then exits with mcp-ingest's own exit code:
#       0 success   1 partial   2 failed   3 another run still holds a source lock
#   See infra/scheduler/runbook-ingest.md for what to do for each.
set -u -o pipefail

mode="${1:-}"
case "$mode" in
  incremental | full) shift ;;
  *)
    echo "usage: $0 incremental|full [mcp-ingest run args...]" >&2
    exit 64
    ;;
esac

env_file="${MCP_INGEST_ENV_FILE:-$HOME/.config/mcp-ingest/env}"
if [ -f "$env_file" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$env_file"
  set +a
fi

log_dir="${MCP_INGEST_LOG_DIR:-$HOME/.local/state/mcp-ingest}"
mkdir -p "$log_dir" || exit 66
repo="${MCP_INGEST_REPO:-$(cd "$(dirname "$0")/../.." && pwd)}"
# MCP_INGEST_BIN lets tests (and unusual installs) replace the launcher.
bin="${MCP_INGEST_BIN:-uv run --project $repo mcp-ingest}"

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
report="$log_dir/run-$mode-$stamp.json"
logfile="$log_dir/run-$mode-$stamp.log"

# shellcheck disable=SC2086
$bin run --source all --mode "$mode" --json "$@" >"$report" 2>"$logfile"
code=$?

printf '%s\n' "$code" >"$log_dir/last-exit-code"
printf '{"ts":"%s","mode":"%s","exit_code":%s,"report":"%s"}\n' \
  "$stamp" "$mode" "$code" "$report" >>"$log_dir/history.jsonl"

case "$code" in
  0) ;;
  1) echo "mcp-ingest $mode: PARTIAL (some source or document failed) - see $report; run 'run --retry-failed'" >&2 ;;
  2) echo "mcp-ingest $mode: FAILED - see $logfile" >&2 ;;
  3) echo "mcp-ingest $mode: SKIPPED, a previous run still holds the source lock" >&2 ;;
  *) echo "mcp-ingest $mode: unexpected exit code $code - see $logfile" >&2 ;;
esac
exit "$code"
