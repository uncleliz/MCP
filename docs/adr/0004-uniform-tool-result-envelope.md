# ADR-0004: Envelope kết quả tool thống nhất, citation bắt buộc, empty là status

**Date**: 2026-10-01
**Status**: accepted
**Deciders**: SA (squad-sa)

## Context

BR-002, FR-015 và mọi AC-002 dạng "trả về empty/not found tường minh thay vì bịa" đòi hỏi:
(a) mỗi kết quả tool phải mang theo dữ liệu đủ để Claude trích dẫn nguồn; (b) "không có dữ
liệu" phải phân biệt được rõ với "lỗi" và không bao giờ bị trả về như exception mơ hồ.
Nếu 9 server mỗi server một hình dạng output, Claude phải suy đoán và QA không thể viết
kiểm tra chung.

## Decision

Mọi tool của mọi server trả về một envelope duy nhất (`ToolResult` trong `mcp_common.envelope`),
khai báo bằng `outputSchema` của MCP và chốt trong `api-contract.yaml`:

```json
{
  "status": "ok | empty | not_found | partial",
  "items": [ ... ],
  "citations": [ { "source_type": "confluence", "label": "...", "uri": "...", "locator": {...} } ],
  "meta": { "source": "confluence", "returned": 12, "has_more": true, "next_cursor": "…",
            "truncated": false, "elapsed_ms": 412, "as_of": "2026-10-01T03:00:00Z",
            "query_echo": {...}, "warnings": [] }
}
```

- `status=empty`: truy vấn hợp lệ, không có kết quả → **không** phải lỗi.
- `status=not_found`: định danh cụ thể được yêu cầu (page_id, topic, key, queue) không tồn tại.
- `status=partial`: có kết quả nhưng bị cắt bớt (`meta.truncated=true`) hoặc một phần nguồn lỗi.
- Lỗi thật trả về MCP tool error kèm payload `{"status":"error","error":{code,message,source,retryable,details}}`
  với `code` thuộc enum cố định (xem `ErrorCode` trong contract).
- `citations` là **bắt buộc không rỗng khi `status` ∈ {ok, partial}**; mỗi item trong `items`
  có `citation_ref` trỏ tới index của citation tương ứng.

## Alternatives Considered

### Alternative 1: Trả về payload thô của từng nguồn
- **Pros**: Ít code nhất, giữ đủ thông tin.
- **Cons**: Claude phải tự học 9 hình dạng; token phình; không chỗ nào bắt buộc có citation;
  "empty" lẫn với lỗi.
- **Why not**: Trực tiếp mâu thuẫn FR-015 và NFR-003.

### Alternative 2: Chỉ trả về text đã render sẵn cho Claude đọc
- **Pros**: Tiết kiệm token, dễ đọc.
- **Cons**: Không machine-checkable → QA không validate được theo contract; mất khả năng phân trang.
- **Why not**: Vi phạm nguyên tắc contract-first. (Giải pháp: trả **cả hai** — `structuredContent`
  theo envelope + một text rendering ngắn do `mcp_common.render` sinh ra.)

### Alternative 3: Dùng exception cho trường hợp không có kết quả
- **Pros**: Code gọn.
- **Cons**: Client MCP nhận `isError` → Claude dễ diễn giải thành "hệ thống lỗi" và tự suy đoán.
- **Why not**: AC-002 của FR-001/004/005/007/008/010/011 yêu cầu empty tường minh.

## Consequences

### Positive
- Một schema chung `ToolResult` cho phép test hợp đồng cho cả 9 server bằng cùng một bộ kiểm tra.
- `meta.as_of` + `meta.warnings` cho Claude biết dữ liệu cũ tới đâu (phục vụ NFR-004).
- `next_cursor` chuẩn hoá phân trang cho mọi nguồn.

### Negative
- Envelope thêm ~120 byte overhead mỗi response.
- Mỗi adapter nguồn phải có một mapper sang envelope → thêm code và test.

### Risks
- Envelope quá tổng quát có thể khiến item mỗi tool vẫn khác nhau nhiều. Giảm thiểu: mỗi tool
  khai báo `items` bằng một schema item cụ thể (`ConfluencePage`, `LogEvent`, …) trong contract,
  không dùng `additionalProperties: true`.
