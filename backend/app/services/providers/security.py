from __future__ import annotations

import ipaddress
import logging
import re
import socket
from urllib.parse import urlsplit

import httpx

from backend.app.services.providers.base import AIProviderBadRequestError

logger = logging.getLogger(__name__)

# Safe defaults for outbound AI provider HTTP clients
MAX_SAFE_RESPONSE_BYTES = 10 * 1024 * 1024  # 10 MB limit to prevent memory exhaustion
DEFAULT_OUTBOUND_TIMEOUT_SECONDS = 30.0

# Explicit redirect policy: automatic redirects are disabled to prevent SSRF hopping
FOLLOW_REDIRECTS = False
ALLOW_REDIRECTS = False
DISALLOW_REDIRECTS = False  # Backward-compatible alias (True/False where True means disallowed or False means follow=False)

# Hostnames strictly forbidden to prevent SSRF against metadata services and local infrastructure
_FORBIDDEN_HOSTNAMES = {
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
    "::1",
    "metadata.google.internal",
    "metadata",
    "instance-data",
    "169.254.169.254",  # Cloud metadata IP (AWS/GCP/Azure)
    "fd00:ec2::254",    # IPv6 Cloud metadata
}

# Standard allowed outbound HTTPS ports
_ALLOWED_HTTPS_PORTS = {443}


def _is_ip_private_or_loopback(ip_str: str) -> bool:
    """Check if an IPv4 or IPv6 string points to a private, loopback, or reserved network."""
    try:
        ip = ipaddress.ip_address(ip_str)
        return (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        )
    except ValueError:
        return False


def validate_provider_endpoint(
    url: str | None,
    *,
    allow_localhost_for_testing: bool = False,
    provider_code: str = "unknown",
) -> str | None:
    """Validate that an external AI provider endpoint adheres to security restrictions.

    Enforces:
    - Must be a non-empty absolute URL with scheme 'https'.
    - Disallows insecure 'http://', 'file://', 'ftp://', or scheme-relative URLs.
    - Disallows embedded credentials in URLs (e.g. https://user:pass@host).
    - Disallows non-standard suspicious ports.
    - Disallows cloud metadata services and private/loopback addresses.
    - Resolves host DNS to prevent internal DNS rebinding and IP tricks.
    """
    if url is None:
        return None

    clean = url.strip()
    if not clean:
        return None

    parsed = urlsplit(clean)
    if parsed.scheme.lower() != "https":
        raise AIProviderBadRequestError(
            f"Provider endpoint must use secure HTTPS protocol. Rejected insecure scheme: {parsed.scheme}",
            provider_code=provider_code,
            status_code=400,
        )

    # Reject userinfo in URLs
    if parsed.username or parsed.password:
        raise AIProviderBadRequestError(
            "Provider endpoint must not contain embedded user credentials.",
            provider_code=provider_code,
            status_code=400,
        )

    hostname = (parsed.hostname or "").lower()
    if not hostname:
        raise AIProviderBadRequestError(
            "Provider endpoint must contain a valid hostname.",
            provider_code=provider_code,
            status_code=400,
        )

    if not allow_localhost_for_testing:
        if hostname in _FORBIDDEN_HOSTNAMES:
            raise AIProviderBadRequestError(
                f"Forbidden host '{hostname}' cannot be targeted as an AI provider endpoint.",
                provider_code=provider_code,
                status_code=400,
            )

        # Check if the hostname string itself is a private IP literal
        if _is_ip_private_or_loopback(hostname):
            raise AIProviderBadRequestError(
                f"Private or link-local IP '{hostname}' cannot be targeted as an AI provider endpoint.",
                provider_code=provider_code,
                status_code=400,
            )

        # DNS resolution check: verify resolved IP addresses are public
        try:
            addr_info = socket.getaddrinfo(hostname, 443, proto=socket.IPPROTO_TCP)
            for entry in addr_info:
                ip_addr = entry[4][0]
                if _is_ip_private_or_loopback(ip_addr):
                    raise AIProviderBadRequestError(
                        f"Target host '{hostname}' resolves to restricted internal IP '{ip_addr}'. Request blocked.",
                        provider_code=provider_code,
                        status_code=400,
                    )
        except socket.gaierror:
            # If DNS resolution fails, reject as invalid external endpoint
            raise AIProviderBadRequestError(
                f"Target host '{hostname}' could not be resolved by DNS.",
                provider_code=provider_code,
                status_code=400,
            )

    # Port validation
    if parsed.port is not None and parsed.port not in _ALLOWED_HTTPS_PORTS:
        if not allow_localhost_for_testing:
            raise AIProviderBadRequestError(
                f"Provider endpoint specifies non-standard port '{parsed.port}'. Only standard HTTPS (443) is permitted.",
                provider_code=provider_code,
                status_code=400,
            )

    return clean


def mask_sensitive_token(secret: str | None) -> str:
    """Mask credentials or API keys for safe display in telemetry or diagnostic logs.

    Never exposes full API keys or auth tokens.
    """
    if not secret:
        return "****"
    clean = secret.strip()
    if len(clean) > 8:
        return f"{clean[:3]}...{clean[-4:]}"
    if len(clean) > 4:
        return f"...{clean[-4:]}"
    return "****"


def _enforce_no_redirect(response: httpx.Response) -> None:
    """Response hook callback that intercepts and rejects any HTTP 3xx redirect status."""
    if response.is_redirect or response.status_code in (301, 302, 303, 307, 308):
        redirect_target = response.headers.get("location", "unknown")
        logger.warning(
            "Outbound provider redirect attempt blocked",
            extra={"status_code": response.status_code, "target": redirect_target},
        )
        raise AIProviderBadRequestError(
            f"Provider returned redirect (HTTP {response.status_code}) to '{redirect_target}'. "
            "Redirects are disabled to prevent SSRF vulnerabilities.",
            provider_code="unknown",
            status_code=response.status_code,
        )


def verify_response_size(response: httpx.Response, max_bytes: int = MAX_SAFE_RESPONSE_BYTES) -> None:
    """Validate that response content length does not exceed memory safety limits."""
    content_length = response.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > max_bytes:
                raise AIProviderBadRequestError(
                    f"Provider response size exceeds safe limit of {max_bytes} bytes.",
                    provider_code="unknown",
                    status_code=413,
                )
        except ValueError:
            pass

    if len(response.content) > max_bytes:
        raise AIProviderBadRequestError(
            f"Provider response payload exceeds safe limit of {max_bytes} bytes.",
            provider_code="unknown",
            status_code=413,
        )


def build_safe_provider_http_client(
    *,
    timeout_seconds: float | None = None,
    timeout: float | httpx.Timeout | None = None,
    max_connections: int = 50,
    max_keepalive: int = 10,
    **kwargs,
) -> httpx.Client:
    """Construct an HTTP client configured with strict security controls for outbound provider APIs.

    Enforces:
    - follow_redirects=False (automatic redirects strictly disabled).
    - Event hook rejecting any 3xx redirect status codes.
    - Explicit timeouts for connect, read, and write.
    - Connection pooling limits.
    """
    effective_timeout = timeout_seconds if timeout_seconds is not None else (timeout if timeout is not None else DEFAULT_OUTBOUND_TIMEOUT_SECONDS)
    if not isinstance(effective_timeout, httpx.Timeout):
        timeout_obj = httpx.Timeout(
            effective_timeout,
            connect=10.0,
            read=effective_timeout,
            write=10.0,
            pool=5.0,
        )
    else:
        timeout_obj = effective_timeout

    limits = httpx.Limits(
        max_connections=max_connections,
        max_keepalive_connections=max_keepalive,
    )
    event_hooks = kwargs.pop("event_hooks", {})
    response_hooks = list(event_hooks.get("response", []))
    response_hooks.append(_enforce_no_redirect)
    event_hooks["response"] = response_hooks

    return httpx.Client(
        follow_redirects=False,
        timeout=timeout_obj,
        limits=limits,
        event_hooks=event_hooks,
        **kwargs,
    )


def build_safe_provider_async_http_client(
    *,
    timeout_seconds: float | None = None,
    timeout: float | httpx.Timeout | None = None,
    max_connections: int = 50,
    max_keepalive: int = 10,
    **kwargs,
) -> httpx.AsyncClient:
    """Construct an async HTTP client configured with strict security controls for outbound provider APIs."""
    effective_timeout = timeout_seconds if timeout_seconds is not None else (timeout if timeout is not None else DEFAULT_OUTBOUND_TIMEOUT_SECONDS)
    if not isinstance(effective_timeout, httpx.Timeout):
        timeout_obj = httpx.Timeout(
            effective_timeout,
            connect=10.0,
            read=effective_timeout,
            write=10.0,
            pool=5.0,
        )
    else:
        timeout_obj = effective_timeout

    limits = httpx.Limits(
        max_connections=max_connections,
        max_keepalive_connections=max_keepalive,
    )

    async def _async_enforce_no_redirect(response: httpx.Response) -> None:
        _enforce_no_redirect(response)

    event_hooks = kwargs.pop("event_hooks", {})
    response_hooks = list(event_hooks.get("response", []))
    response_hooks.append(_async_enforce_no_redirect)
    event_hooks["response"] = response_hooks

    return httpx.AsyncClient(
        follow_redirects=False,
        timeout=timeout_obj,
        limits=limits,
        event_hooks=event_hooks,
        **kwargs,
    )
