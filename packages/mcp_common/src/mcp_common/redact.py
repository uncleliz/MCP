"""T-013: `mcp_common.redact` — secret scrubbing for logs and tool output (ADR-0015).

`scrub()` is applied two ways:
(a) to every log record (wiring done where each server sets up logging), and
(b) to every free-text field before it is returned in a tool result.

Turning redaction off (`MCP_REDACT_DISABLED=true`) must be an explicit operator
action — `scrub(text, disabled=True)` makes that an argument the caller has to pass
deliberately, it is never the default.
"""

from __future__ import annotations

import math
import re
from collections import Counter

__all__ = ["scrub"]

_REDACTED = "«redacted:{kind}»"

# Recognisable secret shapes with a fixed prefix/structure (ADR-0015): AWS keys, JWTs,
# `Bearer ...` headers, GitLab/GitHub/Atlassian token prefixes, PEM private key blocks.
_FIXED_SHAPE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("aws_access_key_id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\b")),
    ("bearer_token", re.compile(r"\bBearer\s+[A-Za-z0-9\-_.=]{8,}", re.IGNORECASE)),
    ("gitlab_token", re.compile(r"\bglpat-[A-Za-z0-9_-]{16,}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
    ("atlassian_token", re.compile(r"\bATATT3[A-Za-z0-9_=\-]{10,}\b")),
    (
        "private_key_block",
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]+?-----END [A-Z ]*PRIVATE KEY-----"),
    ),
)

# `.env`-style lines: KEY (containing PASSWORD/SECRET/TOKEN/API_KEY/PRIVATE_KEY) = value.
_ENV_STYLE_PATTERN = re.compile(
    r"(?im)^([A-Za-z][A-Za-z0-9_]*"
    r"(?:PASSWORD|SECRET|TOKEN|API_KEY|PRIVATE_KEY)[A-Za-z0-9_]*)\s*=\s*(\S+)\s*$"
)

# Inline `password: xxx` / `token=xxx` / `api_key: xxx` (ADR-0015's
# `password|passwd|secret|token|api[_-]?key\s*[:=]\s*...` pattern).
_LABELED_SECRET_PATTERN = re.compile(
    r"""(?ix)
    \b(password|passwd|secret|token|api[_-]?key)
    \s*[:=]\s*
    ["']?([^\s"'&,;]{4,})["']?
    """
)

# Long, no-whitespace runs that *might* be a secret even without a recognisable
# shape or label — only flagged if they also look sufficiently random (see
# `_looks_high_entropy`), to limit false positives on plain long identifiers.
_LONG_TOKEN_PATTERN = re.compile(r"\b[A-Za-z0-9+/_=-]{32,}\b")

_ALREADY_REDACTED_MARK = "«redacted:"


def _looks_high_entropy(token: str, *, min_bits_per_char: float = 3.2) -> bool:
    if len(token) < 32:
        return False
    counts = Counter(token)
    length = len(token)
    entropy = -sum((n / length) * math.log2(n / length) for n in counts.values())
    return entropy >= min_bits_per_char


def scrub(text: str, *, disabled: bool = False) -> tuple[str, int]:
    """Replace secret-looking substrings with `«redacted:<kind>»`.

    Returns `(scrubbed_text, redaction_count)`. `redaction_count` feeds directly into
    `Meta.redactions` (T-008). Pass `disabled=True` to bypass entirely — that must be
    an explicit, logged operator action (`MCP_REDACT_DISABLED=true`), never a silent
    default.
    """
    if disabled or not text:
        return text, 0

    count = 0
    result = text

    for kind, pattern in _FIXED_SHAPE_PATTERNS:
        result, n = pattern.subn(_REDACTED.format(kind=kind), result)
        count += n

    def _replace_env_style(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return f"{match.group(1)}={_REDACTED.format(kind='credential')}"

    result = _ENV_STYLE_PATTERN.sub(_replace_env_style, result)

    def _replace_labeled(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return f"{match.group(1)}={_REDACTED.format(kind='credential')}"

    result = _LABELED_SECRET_PATTERN.sub(_replace_labeled, result)

    def _replace_high_entropy(match: re.Match[str]) -> str:
        nonlocal count
        token = match.group(0)
        if _ALREADY_REDACTED_MARK in token:
            return token
        if _looks_high_entropy(token):
            count += 1
            return _REDACTED.format(kind="high-entropy")
        return token

    result = _LONG_TOKEN_PATTERN.sub(_replace_high_entropy, result)

    return result, count
