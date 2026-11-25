from __future__ import annotations

import ipaddress
import logging
import re
import socket
import typing
from urllib.parse import urlsplit

import httpcore
from httpcore._backends.anyio import AnyIOBackend
from httpcore._backends.base import AsyncNetworkStream, NetworkStream
from httpcore._backends.sync import SyncBackend
import httpx

from backend.app.services.providers.base import AIProviderBadRequestError

logger = logging.getLogger(__name__)


class AIProviderSSRFError(AIProviderBadRequestError):
    """Raised when an outbound request attempts to target internal infrastructure or fails SSRF checks."""

    pass


# Safe defaults for outbound AI provider HTTP clients
MAX_SAFE_RESPONSE_BYTES = 10 * 1024 * 1024  # 10 MB limit to prevent memory exhaustion
DEFAULT_OUTBOUND_TIMEOUT_SECONDS = 30.0

# Explicit redirect policy: automatic redirects are disabled to prevent SSRF hopping
FOLLOW_REDIRECTS = False

# Hostnames strictly forbidden to prevent SSRF against metadata services and local infrastructure
_FORBIDDEN_HOSTNAMES = {
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
    "::1",
    "metadata.google.internal",
    "metadata",
    "instance-data",
    "169.254.169.254",  # Cloud metadata IPv4 (AWS/GCP/Azure)
    "fd00:ec2::254",    # Cloud metadata IPv6
}

# Standard allowed outbound HTTPS ports
_ALLOWED_HTTPS_PORTS = {443}

# Fixed trusted provider host allowlist
TRUSTED_PROVIDER_HOSTS: set[str] = {
    "generativelanguage.googleapis.com",
    "api.openai.com",
    "api.anthropic.com",
    "api.groq.com",
    "openrouter.ai",
    "api.sarvam.ai",
    "api.cloudflare.com",
    "api.cerebras.ai",
    "api-inference.huggingface.co",
    "router.huggingface.co",
    "api.mistral.ai",
    "api.deepseek.com",
    "integrate.api.nvidia.com",
    "api.together.xyz",
    "api.fireworks.ai",
    "api.sambanova.ai",
    "api.cohere.com",
    "api.x.ai",
    "api.perplexity.ai",
    "models.inference.ai.azure.com",
}

# Specific regional Bedrock endpoints (avoids overly broad *.amazonaws.com wildcarding)
_AWS_BEDROCK_HOST_PATTERN = re.compile(r"^bedrock-runtime\.[a-z0-9\-]+\.amazonaws\.com$")


def is_trusted_provider_host(hostname: str) -> bool:
    """Verify if a normalized hostname belongs to the approved provider infrastructure allowlist."""
    clean = hostname.rstrip(". ").lower()
    if clean in TRUSTED_PROVIDER_HOSTS:
        return True
    if _AWS_BEDROCK_HOST_PATTERN.match(clean):
        return True
    return False


def _is_ip_private_or_restricted(ip_obj: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Check if an IPv4 or IPv6 address is private, loopback, link-local, reserved, multicast, or metadata."""
    # Check IPv4-mapped IPv6 addresses (e.g. ::ffff:127.0.0.1 or ::ffff:169.254.169.254)
    if isinstance(ip_obj, ipaddress.IPv6Address):
        if ip_obj.ipv4_mapped:
            return _is_ip_private_or_restricted(ip_obj.ipv4_mapped)
        if ip_obj.sixtofour:
            return _is_ip_private_or_restricted(ip_obj.sixtofour)
        if ip_obj.teredo:
            return _is_ip_private_or_restricted(ip_obj.teredo[1])
        # IPv6 NAT64 prefix (64:ff9b::/96)
        if ip_obj in ipaddress.IPv6Network("64:ff9b::/96"):
            return True

    # Standard IP address properties
    if (
        ip_obj.is_private
        or ip_obj.is_loopback
        or ip_obj.is_link_local
        or ip_obj.is_reserved
        or ip_obj.is_multicast
        or ip_obj.is_unspecified
    ):
        return True

    # Cloud metadata explicit check
    ip_str = str(ip_obj)
    if ip_str in {"169.254.169.254", "fd00:ec2::254"}:
        return True

    return False


def _is_ip_string_private_or_loopback(ip_str: str) -> bool:
    """Check if an IP address literal string points to a restricted or private network."""
    clean = ip_str.strip("[]")
    try:
        ip = ipaddress.ip_address(clean)
        return _is_ip_private_or_restricted(ip)
    except ValueError:
        return False


def _sanitize_redirect_target(location: str | None) -> str:
    """Extract a safe sanitized hostname or 'unknown' from a redirect Location header.

    Never exposes raw paths, query parameters, or embedded credentials.
    """
    if not location:
        return "unknown"
    try:
        parsed = urlsplit(location.strip())
        hostname = (parsed.hostname or "").lower()
        if hostname:
            return hostname
        if parsed.path:
            return "relative-path"
    except Exception:
        pass
    return "unparseable-location"


def _resolve_and_validate_destination_ip(
    hostname: str,
    port: int = 443,
    allow_localhost_for_testing: bool = False,
    provider_code: str = "unknown",
) -> str:
    """Resolve destination host to IP and validate all records against SSRF and private boundaries.

    Returns the first validated public IP address string for connection pinning.
    Raises AIProviderSSRFError if any resolved address violates restricted IP policy.
    """
    clean_host = hostname.strip("[]").rstrip(". ").lower()
    if not clean_host:
        raise AIProviderSSRFError(
            "Destination host must not be empty.",
            provider_code=provider_code,
            status_code=400,
        )

    if not allow_localhost_for_testing:
        if clean_host in _FORBIDDEN_HOSTNAMES:
            raise AIProviderSSRFError(
                f"Forbidden host '{clean_host}' cannot be targeted as an AI provider endpoint.",
                provider_code=provider_code,
                status_code=400,
            )

        if _is_ip_string_private_or_loopback(clean_host):
            raise AIProviderSSRFError(
                f"Private or link-local IP '{clean_host}' cannot be targeted as an AI provider endpoint.",
                provider_code=provider_code,
                status_code=400,
            )

        try:
            addr_info = socket.getaddrinfo(clean_host, port, proto=socket.IPPROTO_TCP)
            if not addr_info:
                raise AIProviderSSRFError(
                    f"Target host '{clean_host}' could not be resolved by DNS.",
                    provider_code=provider_code,
                    status_code=400,
                )

            valid_ips: list[str] = []
            for entry in addr_info:
                ip_addr = entry[4][0]
                try:
                    ip_obj = ipaddress.ip_address(ip_addr)
                    if _is_ip_private_or_restricted(ip_obj):
                        raise AIProviderSSRFError(
                            f"Target host '{clean_host}' resolves to restricted internal IP '{ip_addr}'. Request blocked.",
                            provider_code=provider_code,
                            status_code=400,
                        )
                    valid_ips.append(ip_addr)
                except ValueError:
                    raise AIProviderSSRFError(
                        f"Target host '{clean_host}' resolved to unparseable IP '{ip_addr}'.",
                        provider_code=provider_code,
                        status_code=400,
                    )

            return valid_ips[0]
        except socket.gaierror:
            raise AIProviderSSRFError(
                f"Target host '{clean_host}' could not be resolved by DNS.",
                provider_code=provider_code,
                status_code=400,
            )

    return clean_host


class HardenedSyncBackend(SyncBackend):
    """Network backend that pins resolved destination IPs at connection time to prevent DNS rebinding."""

    def __init__(self, allow_localhost_for_testing: bool = False) -> None:
        super().__init__()
        self.allow_localhost_for_testing = allow_localhost_for_testing

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: typing.Iterable[typing.Any] | None = None,
    ) -> NetworkStream:
        pinned_ip = _resolve_and_validate_destination_ip(
            host,
            port=port,
            allow_localhost_for_testing=self.allow_localhost_for_testing,
        )
        return super().connect_tcp(
            pinned_ip,
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )


class HardenedAsyncBackend(AnyIOBackend):
    """Async network backend that pins resolved destination IPs at connection time to prevent DNS rebinding."""

    def __init__(self, allow_localhost_for_testing: bool = False) -> None:
        super().__init__()
        self.allow_localhost_for_testing = allow_localhost_for_testing

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: typing.Iterable[typing.Any] | None = None,
    ) -> AsyncNetworkStream:
        pinned_ip = _resolve_and_validate_destination_ip(
            host,
            port=port,
            allow_localhost_for_testing=self.allow_localhost_for_testing,
        )
        return await super().connect_tcp(
            pinned_ip,
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )


class HardenedHTTPTransport(httpx.HTTPTransport):
    """HTTP transport that enforces connection-time IP pinning and SSRF prevention."""

    def __init__(
        self,
        *args,
        allow_localhost_for_testing: bool = False,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._pool = httpcore.ConnectionPool(
            ssl_context=self._pool._ssl_context,
            max_connections=self._pool._max_connections,
            max_keepalive_connections=self._pool._max_keepalive_connections,
            keepalive_expiry=self._pool._keepalive_expiry,
            http1=self._pool._http1,
            http2=self._pool._http2,
            retries=self._pool._retries,
            network_backend=HardenedSyncBackend(allow_localhost_for_testing=allow_localhost_for_testing),
        )


class HardenedAsyncHTTPTransport(httpx.AsyncHTTPTransport):
    """Async HTTP transport that enforces connection-time IP pinning and SSRF prevention."""

    def __init__(
        self,
        *args,
        allow_localhost_for_testing: bool = False,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=self._pool._ssl_context,
            max_connections=self._pool._max_connections,
            max_keepalive_connections=self._pool._max_keepalive_connections,
            keepalive_expiry=self._pool._keepalive_expiry,
            http1=self._pool._http1,
            http2=self._pool._http2,
            retries=self._pool._retries,
            network_backend=HardenedAsyncBackend(allow_localhost_for_testing=allow_localhost_for_testing),
        )


def validate_provider_endpoint(
    url: str | None,
    *,
    allow_localhost_for_testing: bool = False,
    enforce_trusted_hosts: bool = False,
    provider_code: str = "unknown",
) -> str | None:
    """Validate that an external AI provider endpoint adheres to security restrictions."""
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

    if parsed.username or parsed.password:
        raise AIProviderBadRequestError(
            "Provider endpoint must not contain embedded user credentials.",
            provider_code=provider_code,
            status_code=400,
        )

    raw_hostname = (parsed.hostname or "").strip()
    hostname = raw_hostname.rstrip(". ").lower()
    if not hostname:
        raise AIProviderBadRequestError(
            "Provider endpoint must contain a valid hostname.",
            provider_code=provider_code,
            status_code=400,
        )

    if not allow_localhost_for_testing:
        if enforce_trusted_hosts and not is_trusted_provider_host(hostname):
            raise AIProviderBadRequestError(
                f"Host '{hostname}' is not in the approved AI provider infrastructure allowlist.",
                provider_code=provider_code,
                status_code=400,
            )

        # Validate destination IP resolution
        _resolve_and_validate_destination_ip(
            hostname,
            port=parsed.port or 443,
            allow_localhost_for_testing=allow_localhost_for_testing,
            provider_code=provider_code,
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
    """Mask credentials or API keys for safe display in telemetry or diagnostic logs."""
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
    if response.is_redirect or (300 <= response.status_code < 400 and response.status_code != 304):
        raw_location = response.headers.get("location")
        safe_target_host = _sanitize_redirect_target(raw_location)
        request_host = (response.request.url.host if response.request else "unknown").lower()
        logger.warning(
            "Outbound provider redirect attempt blocked",
            extra={
                "status_code": response.status_code,
                "request_host": request_host,
                "target_host": safe_target_host,
            },
        )
        raise AIProviderBadRequestError(
            f"Provider returned redirect (HTTP {response.status_code}) to host '{safe_target_host}'. "
            "Redirects are disabled to prevent SSRF vulnerabilities.",
            provider_code="unknown",
            status_code=response.status_code,
        )


def read_safe_response_body(
    response: httpx.Response,
    max_bytes: int = MAX_SAFE_RESPONSE_BYTES,
    chunk_size: int = 65536,
) -> bytes:
    """Read a synchronous HTTP response body in bounded streamed chunks to prevent memory exhaustion DoS."""
    content_length_header = response.headers.get("content-length")
    if content_length_header is not None:
        try:
            declared_len = int(content_length_header)
            if declared_len > max_bytes:
                response.close()
                raise AIProviderBadRequestError(
                    f"Provider response Content-Length ({declared_len} bytes) exceeds safe limit of {max_bytes} bytes.",
                    provider_code="unknown",
                    status_code=413,
                )
        except ValueError:
            pass

    total_bytes = 0
    chunks: list[bytes] = []

    try:
        for chunk in response.iter_bytes(chunk_size=chunk_size):
            total_bytes += len(chunk)
            if total_bytes > max_bytes:
                response.close()
                del chunks
                raise AIProviderBadRequestError(
                    f"Provider response body exceeded safe limit of {max_bytes} bytes during streaming.",
                    provider_code="unknown",
                    status_code=413,
                )
            chunks.append(chunk)
    except Exception:
        response.close()
        raise

    return b"".join(chunks)


async def aread_safe_response_body(
    response: httpx.Response,
    max_bytes: int = MAX_SAFE_RESPONSE_BYTES,
    chunk_size: int = 65536,
) -> bytes:
    """Read an asynchronous HTTP response body in bounded streamed chunks to prevent memory exhaustion DoS."""
    content_length_header = response.headers.get("content-length")
    if content_length_header is not None:
        try:
            declared_len = int(content_length_header)
            if declared_len > max_bytes:
                await response.aclose()
                raise AIProviderBadRequestError(
                    f"Provider response Content-Length ({declared_len} bytes) exceeds safe limit of {max_bytes} bytes.",
                    provider_code="unknown",
                    status_code=413,
                )
        except ValueError:
            pass

    total_bytes = 0
    chunks: list[bytes] = []

    try:
        async for chunk in response.aiter_bytes(chunk_size=chunk_size):
            total_bytes += len(chunk)
            if total_bytes > max_bytes:
                await response.aclose()
                del chunks
                raise AIProviderBadRequestError(
                    f"Provider response body exceeded safe limit of {max_bytes} bytes during streaming.",
                    provider_code="unknown",
                    status_code=413,
                )
            chunks.append(chunk)
    except Exception:
        await response.aclose()
        raise

    return b"".join(chunks)


def verify_response_size(response: httpx.Response, max_bytes: int = MAX_SAFE_RESPONSE_BYTES) -> None:
    """Validate that response content length does not exceed memory safety limits."""
    content_length = response.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > max_bytes:
                response.close()
                raise AIProviderBadRequestError(
                    f"Provider response size exceeds safe limit of {max_bytes} bytes.",
                    provider_code="unknown",
                    status_code=413,
                )
        except ValueError:
            pass

    if hasattr(response, "_content") and response._content is not None:
        if len(response._content) > max_bytes:
            response.close()
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
    transport: httpx.BaseTransport | None = None,
    allow_localhost_for_testing: bool = False,
    **kwargs,
) -> httpx.Client:
    """Construct an HTTP client configured with strict security controls for outbound provider APIs."""
    effective_timeout = (
        timeout_seconds
        if timeout_seconds is not None
        else (timeout if timeout is not None else DEFAULT_OUTBOUND_TIMEOUT_SECONDS)
    )
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

    if transport is None:
        transport = HardenedHTTPTransport(
            limits=limits,
            allow_localhost_for_testing=allow_localhost_for_testing,
        )

    return httpx.Client(
        transport=transport,
        follow_redirects=False,
        timeout=timeout_obj,
        event_hooks=event_hooks,
        **kwargs,
    )


def build_safe_provider_async_http_client(
    *,
    timeout_seconds: float | None = None,
    timeout: float | httpx.Timeout | None = None,
    max_connections: int = 50,
    max_keepalive: int = 10,
    transport: httpx.AsyncBaseTransport | None = None,
    allow_localhost_for_testing: bool = False,
    **kwargs,
) -> httpx.AsyncClient:
    """Construct an async HTTP client configured with strict security controls for outbound provider APIs."""
    effective_timeout = (
        timeout_seconds
        if timeout_seconds is not None
        else (timeout if timeout is not None else DEFAULT_OUTBOUND_TIMEOUT_SECONDS)
    )
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

    if transport is None:
        transport = HardenedAsyncHTTPTransport(
            limits=limits,
            allow_localhost_for_testing=allow_localhost_for_testing,
        )

    return httpx.AsyncClient(
        transport=transport,
        follow_redirects=False,
        timeout=timeout_obj,
        event_hooks=event_hooks,
        **kwargs,
    )
