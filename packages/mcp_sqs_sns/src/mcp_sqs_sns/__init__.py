"""mcp-sqs-sns: read-only MCP server for AWS SQS/SNS metadata (FR-010).

Deliberately has NO tool that reads message content: `sqs:ReceiveMessage` changes the
visibility timeout (a side effect), so it is excluded by design (BR-001, ADR-0003 layer 4).
"""

__version__ = "0.1.0"
