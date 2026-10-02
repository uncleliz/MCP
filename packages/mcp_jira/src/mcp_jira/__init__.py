"""mcp-jira: read-only MCP server for Jira (CHG-001 source #10, ADR-0019).

Thin REST client over httpx (no `jira`/`atlassian-python-api` SDK — those carry the full write
surface, ADR-0019 Alternative 1). Two API flavors are handled behind one tool surface:
Cloud (`/rest/api/3`, cursor `nextPageToken`) and Server/Data Center (`/rest/api/2`, offset
`startAt`/`maxResults`); the split is closed inside `client.py` so the opaque `CursorField`
(ADR-0004) never leaks which flavor is in use.
"""

__version__ = "0.1.0"
