#!/usr/bin/env bash
# scripts/squad/cost-report.sh — bảng thống kê chi phí AI của một project, độc lập (không gọi AI).
#
# Gộp hai nguồn dữ liệu chi phí mà các nền AI tự ghi ra máy:
#   1) Claude Code  — log ~/.claude/metrics/costs.jsonl (ECC cost-tracker), đơn vị USD.
#   2) Kiro CLI     — session ~/.kiro/sessions/cli/*.json, field metering_usage, đơn vị credit.
#
# Dùng:
#   cost-report.sh                 # tự nhận project = thư mục chứa script (gốc repo), bảng tổng + chi tiết
#   cost-report.sh <path|substr>   # lọc theo đường dẫn project (khớp chuỗi con trong cwd)
#   cost-report.sh --all           # không lọc theo project: xem toàn bộ máy
#   cost-report.sh --json          # xuất JSON máy đọc (tôn trọng bộ lọc project ở trên)
#
# Giới hạn đã biết (dữ liệu nguồn, không phải lỗi script):
#   - Claude Code: log ghi theo SESSION, không gắn project; cột "project" chỉ áp dụng cho Kiro.
#     Vì vậy phần USD luôn hiển thị TỔNG TOÀN MÁY và được ghi chú rõ.
#   - Kiro: mọi turn gắn agent "kiro_default" và token thô = 0; chỉ metering_usage (credit) dùng được.
#     => Không nền nào tách được chi phí theo VAI squad; thống kê dừng ở mức project/session/model.
#
# Yêu cầu: node (đã dùng bởi các script khác trong kit). Không cần sqlite3, jq.
set -uo pipefail

SELF_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SELF_DIR/../.." && pwd)"   # scripts/squad -> gốc repo

MODE="table"
FILTER="$PROJECT_ROOT"
case "${1:-}" in
  --all)   FILTER="" ;;
  --json)  MODE="json" ;;
  "")      ;;                 # mặc định: lọc theo PROJECT_ROOT
  *)       FILTER="$1" ;;
esac
[[ "${2:-}" == "--json" ]] && MODE="json"

command -v node >/dev/null 2>&1 || { echo "cost-report cần 'node' trên PATH" >&2; exit 2; }

CLAUDE_LOG="$HOME/.claude/metrics/costs.jsonl"
KIRO_DIR="$HOME/.kiro/sessions/cli"

MODE="$MODE" FILTER="$FILTER" CLAUDE_LOG="$CLAUDE_LOG" KIRO_DIR="$KIRO_DIR" node <<'NODE'
const fs = require("fs"), path = require("path");
const MODE = process.env.MODE, FILTER = process.env.FILTER || "";
const CLAUDE_LOG = process.env.CLAUDE_LOG, KIRO_DIR = process.env.KIRO_DIR;

// ---------- Claude Code (USD) ----------
function claude() {
  const out = { found: false, sessions: 0, total: 0, today: 0, yesterday: 0,
                byModel: new Map(), byDay: new Map(), tok: { in:0,out:0,cw:0,cr:0 } };
  if (!fs.existsSync(CLAUDE_LOG)) return out;
  out.found = true;
  const rows = fs.readFileSync(CLAUDE_LOG, "utf8").split(/\r?\n/).filter(Boolean)
    .map(l => { try { return JSON.parse(l); } catch { return null; } }).filter(Boolean);
  // cumulative-per-session: giữ bản ghi mới nhất mỗi session_id
  const bySession = new Map();
  for (const r of rows) {
    const k = r.session_id || r.transcript_path || r.timestamp;
    const p = bySession.get(k);
    if (!p || String(r.timestamp) > String(p.timestamp)) bySession.set(k, r);
  }
  const latest = [...bySession.values()];
  out.sessions = latest.length;
  const cost = r => Number(r.estimated_cost_usd) || 0;
  const day = r => String(r.timestamp || "").slice(0, 10);
  const today = new Date().toISOString().slice(0, 10);
  const yest = new Date(Date.now() - 864e5).toISOString().slice(0, 10);
  for (const r of latest) {
    const c = cost(r);
    out.total += c;
    if (day(r) === today) out.today += c;
    if (day(r) === yest) out.yesterday += c;
    out.byModel.set(r.model || "(unknown)", (out.byModel.get(r.model || "(unknown)") || 0) + c);
    out.byDay.set(day(r), (out.byDay.get(day(r)) || 0) + c);
    out.tok.in += Number(r.input_tokens) || 0;
    out.tok.out += Number(r.output_tokens) || 0;
    out.tok.cw += Number(r.cache_write_tokens) || 0;
    out.tok.cr += Number(r.cache_read_tokens) || 0;
  }
  return out;
}

// ---------- Kiro (credits) ----------
function kiro(filter) {
  const out = { found: false, sessionsScanned: 0, sessionsMatched: 0,
                credits: 0, turns: 0, byProject: new Map(), byAgent: new Map(),
                sessions: [] };
  if (!fs.existsSync(KIRO_DIR)) return out;
  out.found = true;
  for (const f of fs.readdirSync(KIRO_DIR).filter(f => f.endsWith(".json"))) {
    let j; try { j = JSON.parse(fs.readFileSync(path.join(KIRO_DIR, f), "utf8")); } catch { continue; }
    out.sessionsScanned++;
    const cwd = j.cwd || "(unknown)";
    if (filter && !cwd.includes(filter)) continue;
    out.sessionsMatched++;
    const turns = j.session_state?.conversation_metadata?.user_turn_metadatas || [];
    let sCred = 0;
    for (const t of turns) {
      const agent = t.loop_id?.agent_id?.name || "(unknown)";
      let cred = 0;
      if (Array.isArray(t.metering_usage))
        cred = t.metering_usage.filter(m => m.unit === "credit").reduce((s, m) => s + (Number(m.value) || 0), 0);
      sCred += cred; out.credits += cred; out.turns++;
      out.byAgent.set(agent, (out.byAgent.get(agent) || 0) + cred);
    }
    out.byProject.set(cwd, (out.byProject.get(cwd) || 0) + sCred);
    out.sessions.push({ id: (j.session_id || f).slice(0, 8), cwd,
                        reason: j.session_created_reason || "?", credits: sCred,
                        title: (j.title || "").slice(0, 60) });
  }
  return out;
}

const C = claude();
const K = kiro(FILTER);

// ---------- JSON ----------
if (MODE === "json") {
  const mapObj = m => Object.fromEntries(m);
  console.log(JSON.stringify({
    filter: FILTER || "(all)",
    claude: C.found ? {
      note: "USD theo session, KHÔNG lọc được theo project (log không gắn project)",
      sessions: C.sessions, total_usd: +C.total.toFixed(4),
      today_usd: +C.today.toFixed(4), yesterday_usd: +C.yesterday.toFixed(4),
      by_model: mapObj(new Map([...C.byModel].map(([k,v]) => [k, +v.toFixed(4)]))),
      by_day: mapObj(new Map([...C.byDay].map(([k,v]) => [k, +v.toFixed(4)]))),
      tokens: C.tok,
    } : null,
    kiro: K.found ? {
      unit: "credit", scanned: K.sessionsScanned, matched: K.sessionsMatched,
      credits: +K.credits.toFixed(3), turns: K.turns,
      by_project: mapObj(new Map([...K.byProject].map(([k,v]) => [k, +v.toFixed(3)]))),
      by_agent: mapObj(new Map([...K.byAgent].map(([k,v]) => [k, +v.toFixed(3)]))),
    } : null,
  }, null, 2));
  process.exit(0);
}

// ---------- Bảng ----------
const nf = n => Number(n).toLocaleString("en-US");
const usd = n => "$" + (n < 1 ? n.toFixed(4) : n.toFixed(2));
const cr  = n => n.toFixed(3);
const line = () => console.log("-".repeat(64));

console.log("=".repeat(64));
console.log("BÁO CÁO CHI PHÍ AI  —  lọc project: " + (FILTER || "(toàn máy)"));
console.log("=".repeat(64));

// Claude Code
console.log("\n[1] CLAUDE CODE  (USD, nguồn: ~/.claude/metrics/costs.jsonl)");
line();
if (!C.found) {
  console.log("  Không tìm thấy log. Cost-tracker chạy sau phiên Claude Code đầu tiên.");
} else {
  console.log("  Ghi chú: log theo SESSION, không gắn project => đây là TỔNG TOÀN MÁY.");
  console.log("  Tổng:     " + usd(C.total) + "   (" + C.sessions + " sessions)");
  console.log("  Hôm nay:  " + usd(C.today) + "    |  Hôm qua: " + usd(C.yesterday));
  console.log("  Tokens:   input " + nf(C.tok.in) + " | output " + nf(C.tok.out) +
              " | cache_write " + nf(C.tok.cw) + " | cache_read " + nf(C.tok.cr));
  if (C.byModel.size) {
    console.log("  Theo model:");
    [...C.byModel].sort((a,b)=>b[1]-a[1]).forEach(([k,v]) => console.log("    " + usd(v).padEnd(12) + k));
  }
  if (C.byDay.size) {
    console.log("  Theo ngày:");
    [...C.byDay].sort().forEach(([k,v]) => console.log("    " + k + "   " + usd(v)));
  }
}

// Kiro
console.log("\n[2] KIRO CLI  (credit, nguồn: ~/.kiro/sessions/cli/*.json)");
line();
if (!K.found) {
  console.log("  Không tìm thấy session Kiro.");
} else {
  console.log("  Quét " + K.sessionsScanned + " session, khớp bộ lọc: " + K.sessionsMatched);
  console.log("  Credits:  " + cr(K.credits) + "   (" + K.turns + " lượt)");
  console.log("  Ghi chú: Kiro gắn mọi lượt vào 'kiro_default' => KHÔNG tách được theo vai squad.");
  if (!FILTER && K.byProject.size) {
    console.log("  Theo project (cwd):");
    [...K.byProject].sort((a,b)=>b[1]-a[1]).slice(0,15)
      .forEach(([k,v]) => console.log("    " + cr(v).padStart(10) + " cr   " + k));
  }
  if (K.byAgent.size) {
    console.log("  Theo agent:");
    [...K.byAgent].sort((a,b)=>b[1]-a[1]).forEach(([k,v]) => console.log("    " + cr(v).padStart(10) + " cr   " + k));
  }
  if (FILTER && K.sessions.length) {
    console.log("  Session khớp:");
    K.sessions.sort((a,b)=>b.credits-a.credits).forEach(s =>
      console.log("    " + cr(s.credits).padStart(8) + " cr  [" + s.reason + "] " + s.id + "  " + s.title));
  }
}

console.log("\n" + "=".repeat(64));
console.log("Lưu ý: hai nền dùng đơn vị khác nhau (USD vs credit) nên KHÔNG cộng gộp.");
console.log("Chi phí theo VAI trong flow: không nền nào ghi nhãn vai => không tính được.");
console.log("=".repeat(64));
NODE
