# Backlog — Company Knowledge enhancement (CHG-002 deferred blocks)

> CEO quyết 2026-10-01T22:30: triển khai ngay B1 + B3 + **B4 Grounding contract**. Các block dưới đây hoãn vào
> backlog; CEO sẽ hỏi lại để đưa vào phát triển sau. Nguồn phân tích khả thi: `1-discovery/market-research-chg002.md`.
> Thứ tự gợi ý (researcher): sau B4 → B8 Governance → B2 Knowledge Model → B7 Provenance → B9+B10 → P4 (Memory/State/Orchestrator).

| ID | Block | Giá trị | Độ khó | Phụ thuộc | Cảnh báo bất biến | Cần SA |
|----|-------|---------|--------|-----------|-------------------|--------|
| B2 | Knowledge Model đầy đủ (ontology + entity extraction) | Cao (CEO coi đáng giá nhất) | Cao nhất | — | extraction có thể cần LLM → egress/vendor | options + ADR |
| B5 | Memory (quyết định học được; TTL/version/status) | Cao | Trung | store ghi nội bộ | ghi `kb.agent_*` + credential riêng, không chạm nguồn; choke point + adversarial test | options + ADR |
| B6 | State (task progress) | Trung | Thấp | gần B5 | như B5 (store ghi nội bộ) | ADR nhỏ (chung B5) |
| B7 | Provenance đầy đủ (chain + obsolete propagation v2→v3) | Cao | Trung | **bắt buộc sau B2** | — | ADR |
| B8 | Governance lifecycle (Official/Draft/Deprecated/Experimental/Unknown) | Cao | Thấp | — | — | ADR nhỏ |
| B9 | Evaluation (100 Company Questions + deploy-gate) | Cao | Trung | HF egress (NFR-003) | egress HF 403 chặn đo thật; LLM-judge = egress nếu API | ADR |
| B10 | Observability (trace end-to-end + grounding-failure + cost) | Trung | Thấp | — | — | incremental |
| ORCH | Orchestrator + Harness | Cao (nhưng cuối) | Cao nhất | B9+B10 trước | **in-process giữ stdio**; LLM-ở-đâu (local offline / không-gọi-LLM-server / API-deviation) → ESCALATE CEO | options + ADR LỚN |

## Câu hỏi mở lớn nhất khi mở backlog
- **LLM cho Orchestrator ở đâu?** local offline / không gọi LLM phía server (Claude-client tổng hợp) / API trả tiền (deviation vendors=none). Quyết này định hình toàn bộ P4 + baseline.
- NFR-003 đo thật + B9 Evaluation phụ thuộc gỡ egress HF + chốt model ADR-0010 (nối DK1/D-002).

## Cách CEO đưa một block ra khỏi backlog
Nói với squad: "đưa <ID> vào phát triển" → DM ghi change mới (CHG-00x) → researcher/SA (nếu cần) → Gate 1 → build.
