"""Per-process local request token and loopback origin checks."""

import ipaddress
import secrets
from urllib.parse import urlsplit

from localforge.core.urls import is_loopback_host

SESSION_HEADER_NAME = "X-LOCALFORGE-SESSION"


def create_local_session_token() -> str:
    """Return a high-entropy token that exists only for the current API process."""

    return secrets.token_urlsafe(32)


def is_loopback_origin(origin: str) -> bool:
    """Accept only browser origins whose host resolves literally to loopback."""

    try:
        parsed = urlsplit(origin)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return False
        if parsed.username is not None or parsed.password is not None:
            return False
        _ = parsed.port
        return is_loopback_host(parsed.hostname)
    except ValueError:
        return False


def is_loopback_request_host(hostname: str | None) -> bool:
    """Validate the Host header target used for local API mutations."""

    if hostname is None:
        return False
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        return is_loopback_host(hostname)
    return is_loopback_host(hostname)
