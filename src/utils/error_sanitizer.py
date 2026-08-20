"""
Sanitize error messages to prevent leaking sensitive infrastructure details.

Strips URLs, IP addresses, hostnames with ports, API keys, and authorization
headers from error text before it reaches API clients.  Raw (unsanitized)
messages should still be written to logs for debugging.

Public docs / catalog hosts (nodedocs, apidocs, active) are allowlisted so
intentional off-ramp and MOR/hr comparison links in capacity errors survive
to the client.
"""

import re

# Public hosts that are safe (and intended) in client-facing error copy —
# e.g. P2P off-ramp docs and active.mor.org MOR/hr catalog references.
_ALLOWED_URL_HOSTS = (
    "nodedocs.mor.org",
    "nodedocs.dev",
    "apidocs.mor.org",
    "active.mor.org",
    "active.dev.mor.org",
)

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
    (_BEARER_TOKEN_RE, r"\1[redacted-token]"),
    (_AUTH_HEADER_RE,  r"\1[redacted-header]"),
    (_API_KEY_RE,      r"\1[redacted-key]"),
    (_IP_PORT_RE,      "[redacted-host]"),
    (_HOST_PORT_RE,    "[redacted-host]"),
    (_BARE_FQDN_RE,   "[redacted-host]"),
    (_BARE_IP_RE,      "[redacted-ip]"),
]


def _url_allowed(url: str) -> bool:
    """True if URL host is an intentional public docs off-ramp."""
    # Strip scheme for host check
    rest = re.sub(r"^(?:https?|ftp)://", "", url, flags=re.IGNORECASE)
    host = rest.split("/", 1)[0].split("?", 1)[0].split(":", 1)[0].lower()
    return host in _ALLOWED_URL_HOSTS or any(
        host.endswith(f".{h}") for h in _ALLOWED_URL_HOSTS
    )


def _redact_urls(text: str) -> str:
    def repl(match: re.Match) -> str:
        url = match.group(0)
        return url if _url_allowed(url) else "[redacted-url]"

    return _URL_RE.sub(repl, text)


def _redact_bare_fqdn(text: str) -> str:
    def repl(match: re.Match) -> str:
        host = match.group(0).lower()
        if host in _ALLOWED_URL_HOSTS or any(
            host.endswith(f".{h}") for h in _ALLOWED_URL_HOSTS
        ):
            return match.group(0)
        return "[redacted-host]"

    return _BARE_FQDN_RE.sub(repl, text)


def sanitize_error_message(raw: str) -> str:
    """Return *raw* with sensitive infrastructure details redacted.

    Designed to be cheap (sub-microsecond on typical error strings) and
    safe to call on every error path.  Only the *client-facing* message
    should be sanitized -- keep writing the original to logs.

    Public docs/catalog URLs (nodedocs, apidocs, active.mor.org) are kept so
    hosted-gateway off-ramp and fuse copy can point users at safe destinations.
    """
    if not raw:
        return raw

    result = _redact_urls(raw)
    for pattern, replacement in _PATTERNS:
        if pattern is _BARE_FQDN_RE:
            result = _redact_bare_fqdn(result)
        else:
            result = pattern.sub(replacement, result)
    return result
