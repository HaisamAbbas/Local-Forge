"""Validation helpers for user-supplied inference engine endpoint URLs."""

import ipaddress
from urllib.parse import SplitResult, urlsplit


def parse_engine_endpoint(endpoint_url: str) -> SplitResult:
    """Validate the shape and transport security of an endpoint without contacting it."""

    try:
        parsed = urlsplit(endpoint_url)
        hostname = parsed.hostname
        _ = parsed.port  # Accessing port also validates malformed/out-of-range port values.
    except ValueError as error:
        raise ValueError("endpoint URL has an invalid host or port") from error

    if parsed.scheme not in {"http", "https"}:
        raise ValueError("endpoint URL must use http or https")
    if not hostname:
        raise ValueError("endpoint URL must include a host")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("endpoint URL must not include embedded credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("endpoint URL must not include query parameters or fragments")
    if not is_loopback_host(hostname) and parsed.scheme != "https":
        raise ValueError("remote endpoints must use HTTPS")
    return parsed


def endpoint_is_remote(endpoint_url: str | None) -> bool:
    """Classify an endpoint without DNS lookups or network traffic."""

    if endpoint_url is None:
        return False
    parsed = parse_engine_endpoint(endpoint_url)
    return not is_loopback_host(parsed.hostname or "")


def is_loopback_host(hostname: str) -> bool:
    """Return true for IP loopback literals and localhost names."""

    normalized = hostname.rstrip(".").casefold()
    if normalized == "localhost" or normalized.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False
