# ADR-0002: MCP Python SDK chính thức, stdio cho v1 với bootstrap transport-agnostic

**Date**: 2026-10-01
**Status**: accepted
**Deciders**: SA (squad-sa)

## Context

NFR-005 yêu cầu mỗi server chạy local qua stdio và đăng ký được với Claude Desktop/Code
mà không cần service web. BR-004 yêu cầu kiến trúc không chặn đường chuyển sang
multi-user/remote hosting (HTTP + SSE / streamable HTTP) về sau. Nếu logic tool bị buộc
trực tiếp vào vòng lặp stdio thì việc thêm transport HTTP sau này phải viết lại từng server.

## Decision

Dùng MCP Python SDK chính thức (`mcp`, lớp `FastMCP`) để khai báo tool bằng decorator với
`outputSchema` tường minh. Mỗi package server chỉ export một hàm `build_server() -> FastMCP`
thuần (không I/O transport), còn `mcp_common.runtime.serve(build_server, transport=...)`
chịu trách nhiệm chọn transport. v1 chỉ bật `stdio`; `streamable-http` được bọc sẵn nhưng
tắt bằng cấu hình (`MCP_TRANSPORT=stdio` là mặc định và là giá trị duy nhất được hỗ trợ ở v1).

## Alternatives Considered

### Alternative 1: Tự implement JSON-RPC over stdio
- **Pros**: Không phụ thuộc SDK, kiểm soát hoàn toàn.
- **Cons**: Phải tự bám theo thay đổi của spec MCP (tool outputSchema, prompts, resources,
  cancellation, progress); 9 server nhân rủi ro.
- **Why not**: Không có lợi ích nào bù được chi phí bảo trì spec.

### Alternative 2: Dùng framework bên thứ ba (ví dụ wrapper cộng đồng)
- **Pros**: Có thể có sugar nhiều hơn.
- **Cons**: Trễ so với spec, ít bảo đảm dài hạn.
- **Why not**: SDK chính thức đã đủ và bám spec sớm nhất.

### Alternative 3: Viết luôn HTTP+SSE ở v1
- **Pros**: Đi thẳng tới đích multi-user.
- **Cons**: Kéo theo authN/Z multi-user, quản lý session, hosting — đều nằm ngoài scope v1
  (Out of scope trong requirements.md).
- **Why not**: Scope v1 rõ ràng là stdio; chỉ cần *không chặn* đường mở rộng.

## Consequences

### Positive
- Logic tool có thể test bằng in-memory client của SDK, không cần dựng process.
- Thêm transport HTTP sau này là thay đổi tại một điểm (`mcp_common.runtime`), không sửa 9 server.
- `outputSchema` của SDK cho phép validate output theo `api-contract.yaml` tự động (xem ADR-0013).

### Negative
- Bị ràng buộc phiên bản SDK; breaking change của SDK ảnh hưởng cả 10 package.
- Phải pin phiên bản `mcp` trong `mcp-common` và nâng cấp có kiểm soát.

### Risks
- Khi bật HTTP trong tương lai, mô hình "mỗi user chạy process với credential của chính mình"
  (xem BR-003 trong architecture.md) sẽ mất → cần thiết kế authN/Z riêng. Ghi nhận là spike,
  không giải quyết ở v1.
