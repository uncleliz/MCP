# Gate 1 brief — CHG-001 Company Knowledge (plan baseline mới)

## Summary (shown in chat, ≤ 10 lines)
- CHG-001 thêm tầng Company Knowledge lên nền 9-nguồn đã go-live: Jira + Hybrid-RAG + versioning + entities/relationships + summaries + permission server-side.
- SA ra 3 option, khuyến nghị **Option C** (gateway MỎNG in-process **giữ** stdio + Postgres hybrid + reranker local offline + CTE): điểm 445/500 (C) > A 440 > B 290.
- Vì sao C: đủ nghiệp vụ spec, **giữ bất biến NFR-005 stdio-only**, không vendor/service/datastore mới, $0/tháng, đặt sẵn đường mở HTTP+SSE v1.1.
- Chi phí: ~20–28 ngày-agent build, **$0/tháng** run (ngoài hạ tầng hiện có).
- Deviation = 100/100 → ADR-0017 (proposed) bắt buộc + CEO quyết.
- Quyết định lớn nhất (DP1/NFR-005): C giữ stdio vs B chấp nhận service HTTP (ContextForge + AGE) vs A hoãn Gateway v1.1.
- NFR-003 vẫn UNVERIFIED (HF egress 403) — đo thật trong CHG-001 (E3) trước go-live, nối tiếp DK1/D-002.
- Nếu duyệt C: baseline kế thừa Jira (nguồn 10), Hybrid-RAG, gateway in-process, 4 domain dữ liệu mới, permission server-side (đảo invariant C6).
Full brief: docs/squad/features/mcp-data-platform/1-discovery/decision-brief.md · sources: options.md, docs/adr/0017-...deviation.md

## Details
Xem decision-brief.md (TL;DR, 3 option, plan epic E1..E8, go-live criteria, risks) và options.md (bảng điểm có
trọng số, lập trường DP1..DP5 từng option) + docs/adr/0017-chg001-company-knowledge-deviation.md (ADR-deviation).

### 4 quyết định CEO cần chốt ở Gate 1
1. **Chọn option (DP1/NFR-005 — lớn nhất):** C (gateway in-process, giữ stdio — khuyến nghị) / B (deviation NFR-005, service HTTP ContextForge + AGE) / A (hoãn Gateway v1.1).
2. **DP2 reranker:** giữ reranker local offline (khuyến nghị, vendors=none) hay mở deviation +40 cho API trả tiền.
3. **NFR-003:** chấp nhận UNVERIFIED tới khi đo trong CHG-001 (nối DK1/D-002) hay chặn build tới khi gỡ egress HF trước E3.
4. **Baseline inheritance (nếu duyệt C):** chốt Jira (nguồn 10), Hybrid-RAG + reranker local, gateway-as-boundary in-process, 4 domain dữ liệu mới, permission server-side (amend invariant C6/BR-003).
