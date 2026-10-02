"""T-111 (CHG-003): the one default-deny egress choke point for the ingest-pull +
model-download path (ADR-0023 §6a, L-001).

Everything built before CHG-003 ran under a `no-egress` invariant. CHG-003 opens outbound
network for exactly two paths — the `mcp-ingest` **pull** (reading a real source such as
`tnexwm.atlassian.net`) and the one-time embedding-**model download** (`huggingface.co`).
This module is the single structural point every one of those outbound calls passes through:

* :func:`check_egress` is the only gate. Given a target host (or URL), it permits the
  connection **only** if the host matches the configured allow-list; otherwise it raises
  :class:`EgressDenied` (mapped to the contract's ``not_permitted``) **before any socket is
  opened** — default-deny, fail-closed, never "log and allow".
* The allow-list is config-driven (`MCP_EGRESS_ALLOWLIST`, CSV). It is **empty by default**,
  which means *deny all*: a fresh install makes no outbound call until the operator names the
  hosts the ingest pull / model download is allowed to reach.
* Host patterns use shell globs (:func:`fnmatch`), so the ADR's `*.atlassian.net` matches
  `tnexwm.atlassian.net` but not a look-alike like `atlassian.net.evil.example`.

Enforcement is wired at each egress *seam* (`mcp_common.http.build_client`'s event hook for the
httpx connectors, the OpenSearch SDK transport, the model-download step), each of which calls
**this one** function — so there is exactly one place egress policy is decided (L-001). The 9 MCP
servers do **not** enable it: they keep answering the client over stdio and reach only their own
upstream read API, so egress stays a property of the ingest-pull + model path, not of the servers
(ADR-0023 §6a; the servers open no new network port — NFR-012).

Any error this raises carries only the host and the allow-list (never a credential); the message
is additionally scrubbed at the result/log boundary by `mcp_common.errors.to_error_envelope`
(ADR-0015, L-001/E-003).
"""

from __future__ import annotations

import fnmatch

from mcp_common.config import CommonSettings
from mcp_common.errors import NotPermittedError

__all__ = [
    "EgressDenied",
    "parse_allowlist",
    "host_allowed",
    "resolve_host",
    "check_egress",
]

# The source identifier egress denials are attributed to in the error envelope. It is a
# property of the pull/model path, not of any one upstream source.
SOURCE = "egress"


class EgressDenied(NotPermittedError):
    """Raised when an outbound host is not on the egress allow-list (default-deny).

    A subclass of :class:`NotPermittedError` so it maps to the contract's ``not_permitted``
    error code and is always ``retryable=False`` — a denied host will not become allowed on a
    retry; the operator must add it to ``MCP_EGRESS_ALLOWLIST`` deliberately.
    """

    def __init__(
        self,
        host: str,
        allowlist: list[str],
        *,
        request_id: str | None = None,
    ) -> None:
        self.host = host
        self.allowed_patterns = list(allowlist)
        if allowlist:
            detail = "allow-list: " + ", ".join(allowlist)
        else:
            detail = "allow-list is empty (default-deny): set MCP_EGRESS_ALLOWLIST"
        super().__init__(
            f"egress to host '{host}' is refused ({detail}).",
            source=SOURCE,
            operation=f"egress {host}",
            allowlist=allowlist,
            request_id=request_id,
        )


def parse_allowlist(raw: str | None) -> list[str]:
    """Parse the ``MCP_EGRESS_ALLOWLIST`` CSV into a list of host patterns.

    Empty / unset ⇒ ``[]`` ⇒ deny all. Entries are stripped and lower-cased; blanks are
    dropped so a trailing comma or stray whitespace never silently widens the list.
    """
    if not raw:
        return []
    return [entry.strip().lower() for entry in raw.split(",") if entry.strip()]


def resolve_host(host_or_url: str) -> str:
    """Extract the bare host from either a bare host or a full URL.

    Accepts ``tnexwm.atlassian.net`` as well as ``https://tnexwm.atlassian.net/wiki/...`` so
    every seam can pass whichever it has without each re-implementing URL parsing.
    """
    candidate = host_or_url.strip()
    if "://" in candidate:
        candidate = candidate.split("://", 1)[1]
    # Strip any path, query or fragment, then any ``user:pass@`` credential and ``:port``.
    candidate = candidate.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    if "@" in candidate:
        candidate = candidate.rsplit("@", 1)[1]
    # IPv6 literals are bracketed (``[::1]:443``); keep the bracketed form, drop the port only.
    if candidate.startswith("["):
        close = candidate.find("]")
        if close != -1:
            return candidate[: close + 1].lower()
    if ":" in candidate:
        candidate = candidate.rsplit(":", 1)[0]
    return candidate.lower()


def host_allowed(host: str, allowlist: list[str]) -> bool:
    """True iff ``host`` matches at least one allow-list pattern (fnmatch glob).

    Matching is case-insensitive and anchored to the whole host, so ``*.atlassian.net`` admits
    ``tnexwm.atlassian.net`` but not ``atlassian.net.evil.example`` or a bare ``atlassian.net``.
    An empty host never matches (fail-closed).
    """
    if not host:
        return False
    target = host.lower()
    return any(fnmatch.fnmatchcase(target, pattern) for pattern in allowlist)


def check_egress(
    host_or_url: str,
    *,
    allowlist: list[str] | None = None,
    settings: CommonSettings | None = None,
    request_id: str | None = None,
) -> str:
    """The one egress gate (L-001). Permit the connection only if the host is allow-listed.

    Resolves the bare host, then permits it **only** when it matches the configured allow-list;
    otherwise raises :class:`EgressDenied` *before* the caller opens any socket (default-deny,
    fail-closed). Returns the resolved host on success so callers may log/assert on it.

    `allowlist` is taken verbatim when supplied (tests pass it directly); otherwise it is read
    from ``MCP_EGRESS_ALLOWLIST`` via :class:`CommonSettings`. Default-empty ⇒ deny all.
    """
    if allowlist is None:
        resolved_settings = settings or CommonSettings()
        allowlist = parse_allowlist(resolved_settings.egress_allowlist)

    host = resolve_host(host_or_url)
    if not host_allowed(host, allowlist):
        raise EgressDenied(host or host_or_url, allowlist, request_id=request_id)
    return host
