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
from urllib.parse import urlsplit

__all__ = ["scrub", "register_secret", "register_dsn_secret", "clear_registered_secrets"]

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

# JSON/dict-shaped secrets: `"password": "value"`, `'client_secret': 'value'`, `"apiKey": ".."`.
# The key must *end* with a sensitive word (so `tokenizer`/`passwordless` are left alone) and the
# value must be a quoted string of at least 4 characters. Structure and other fields are kept.
_JSON_SECRET_PATTERN = re.compile(
    r"""(?ix)
    (["'][A-Za-z0-9_\-]*(?:password|passwd|secret|token|api[_-]?key|private[_-]?key|credentials?)["']
    \s*:\s*)
    (["'])([^"']{4,})\2
    """
)

# Long, no-whitespace runs that *might* be a secret even without a recognisable
# shape or label — only flagged if they also look sufficiently random (see
# `_looks_high_entropy`), to limit false positives on plain long identifiers.
_LONG_TOKEN_PATTERN = re.compile(r"\b[A-Za-z0-9+/_=-]{32,}\b")

_ALREADY_REDACTED_MARK = "«redacted:"

# Shortest configured secret value that is worth value-based redaction. Anything
# below this is either not a real credential or so short that redacting it would
# scrub ordinary words out of logs/results — the shape/label/entropy passes still
# cover genuinely sensitive short tokens that carry a recognisable prefix.
_MIN_REGISTERED_SECRET_LEN = 8

# T-121 (CHG-003 / FR-025, ADR-0023 §6c): the set of *configured* secret values
# (e.g. the read-only Atlassian API token) that must be scrubbed wherever they
# appear in an outbound string — the error/result boundary AND the stderr log.
# Value-based redaction is the primary defence for an **opaque** token that the
# shape/label/entropy heuristics below cannot recognise (a Confluence Cloud API
# token has no guaranteed `ATATT3` prefix and may not look high-entropy): if the
# exact configured secret appears anywhere outbound, it is redacted. Registering a
# secret here is additive — the existing fixed-shape/label/JSON/entropy passes are
# untouched and still run. The registry holds only the live process's own
# credentials; it is never persisted and carries no plaintext to disk.
_REGISTERED_SECRETS: set[str] = set()


def register_secret(secret: str | None) -> None:
    """Register a configured secret value so `scrub()` redacts it wherever it appears.

    Called once at server/CLI startup after settings load (e.g. with the resolved
    Atlassian API token). Short/empty values are ignored so a blank or placeholder
    token never turns `scrub()` into a no-op that redacts ordinary text. Idempotent.
    """
    if not secret:
        return
    value = secret.strip()
    if len(value) < _MIN_REGISTERED_SECRET_LEN:
        return
    _REGISTERED_SECRETS.add(value)


def register_dsn_secret(dsn: str | None) -> None:
    """Register the credential carried by a Postgres/Redis DSN for value-based scrubbing.

    A DSN such as ``postgresql://user:p4ssw0rd@host/db`` carries its password inline. We
    register (a) the password component on its own — the part most likely to be interpolated
    into a psycopg/redis error — and (b) the full DSN string, so neither can leak verbatim on
    an error/log path. Called once at the single client-construction seam of each DSN-backed
    source (pgvector, knowledge). Short/empty values are ignored (see :func:`register_secret`);
    idempotent; never persisted.
    """
    if not dsn:
        return
    register_secret(dsn)
    parsed = urlsplit(dsn.strip())
    if parsed.password:
        register_secret(parsed.password)


def clear_registered_secrets() -> None:
    """Drop every registered secret (used by tests to keep the registry isolated)."""
    _REGISTERED_SECRETS.clear()


def _looks_high_entropy(token: str, *, min_bits_per_char: float = 3.2) -> bool:
    if len(token) < 32:
        return False
    counts = Counter(token)
    length = len(token)
    entropy = -sum((n / length) * math.log2(n / length) for n in counts.values())
    return entropy >= min_bits_per_char


def scrub(
    text: str, *, disabled: bool = False, extra_secrets: tuple[str, ...] = ()
) -> tuple[str, int]:
    """Replace secret-looking substrings with `«redacted:<kind>»`.

    Returns `(scrubbed_text, redaction_count)`. `redaction_count` feeds directly into
    `Meta.redactions` (T-008). Pass `disabled=True` to bypass entirely — that must be
    an explicit, logged operator action (`MCP_REDACT_DISABLED=true`), never a silent
    default.

    Any value registered via :func:`register_secret` (plus `extra_secrets` for this
    call) is redacted **by exact value, first**, before the shape/label/entropy passes
    — this is how an opaque configured credential such as the read-only Atlassian API
    token (T-121, FR-025) is guaranteed to be scrubbed on both the tool/result boundary
    and the error/log path even though its text carries no recognisable secret shape.
    """
    if disabled or not text:
        return text, 0

    count = 0
    result = text

    # T-121: value-based pass first — redact every registered/extra secret by its exact
    # value (longest first, so a token that contains a shorter one is handled whole).
    configured = {s.strip() for s in (*_REGISTERED_SECRETS, *extra_secrets) if s and s.strip()}
    for secret in sorted(configured, key=len, reverse=True):
        if len(secret) < _MIN_REGISTERED_SECRET_LEN:
            continue
        if secret in result:
            occurrences = result.count(secret)
            result = result.replace(secret, _REDACTED.format(kind="credential"))
            count += occurrences

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

    def _replace_json_secret(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        quote = match.group(2)
        return f"{match.group(1)}{quote}{_REDACTED.format(kind='credential')}{quote}"

    result = _JSON_SECRET_PATTERN.sub(_replace_json_secret, result)

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
