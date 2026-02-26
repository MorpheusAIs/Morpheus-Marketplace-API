"""
Sanitize error messages to prevent leaking sensitive infrastructure details.

Strips URLs, IP addresses, hostnames with ports, API keys, and authorization
headers from error text before it reaches API clients.  Raw (unsanitized)
messages should still be written to logs for debugging.
"""

import re

_URL_RE = re.compile(
    r"""
    (?:https?|ftp)://          # scheme
    [^\s"'<>\]})]+             # everything until whitespace or closing delimiters
    """,
    re.VERBOSE,
)

_IP_PORT_RE = re.compile(
    r"\b\d{1,3}(?:\.\d{1,3}){3}:\d{1,5}\b"
)

_BARE_IP_RE = re.compile(
    r"\b\d{1,3}(?:\.\d{1,3}){3}\b"
)

_BEARER_TOKEN_RE = re.compile(
    r"(Bearer\s+)\S+", re.IGNORECASE
)

_API_KEY_RE = re.compile(
    r"\b(sk-|key-|api[_-]?key[=:\s]+)\S+", re.IGNORECASE
)

_AUTH_HEADER_RE = re.compile(
    r"(Authorization:\s*)\S+", re.IGNORECASE
)

_HOST_PORT_RE = re.compile(
    r"\b([a-zA-Z0-9](?:[a-zA-Z0-9\-]*[a-zA-Z0-9])?(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9\-]*[a-zA-Z0-9])?)+):\d{1,5}\b"
)

# Bare FQDNs with 3+ dot-separated segments (e.g. sub.domain.com).
# Requires at least two dots to avoid false positives on simple words.
_BARE_FQDN_RE = re.compile(
    r"\b[a-zA-Z0-9](?:[a-zA-Z0-9\-]*[a-zA-Z0-9])?"
    r"(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9\-]*[a-zA-Z0-9])?){2,}\b"
)

_PATTERNS: list[tuple[re.Pattern, str]] = [
    (_URL_RE,          "[redacted-url]"),
    (_BEARER_TOKEN_RE, r"\1[redacted-token]"),
    (_AUTH_HEADER_RE,  r"\1[redacted-header]"),
    (_API_KEY_RE,      r"\1[redacted-key]"),
    (_IP_PORT_RE,      "[redacted-host]"),
    (_HOST_PORT_RE,    "[redacted-host]"),
    (_BARE_FQDN_RE,   "[redacted-host]"),
    (_BARE_IP_RE,      "[redacted-ip]"),
]


def sanitize_error_message(raw: str) -> str:
    """Return *raw* with sensitive infrastructure details redacted.

    Designed to be cheap (sub-microsecond on typical error strings) and
    safe to call on every error path.  Only the *client-facing* message
    should be sanitized -- keep writing the original to logs.
    """
    if not raw:
        return raw

    result = raw
    for pattern, replacement in _PATTERNS:
        result = pattern.sub(replacement, result)
    return result
