from __future__ import annotations

import re
import socket
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest

from backend.app.services.providers import (
    AIProviderBadRequestError,
    ALLOW_REDIRECTS,
    DEFAULT_OUTBOUND_TIMEOUT_SECONDS,
    DISALLOW_REDIRECTS,
    FOLLOW_REDIRECTS,
    MAX_SAFE_RESPONSE_BYTES,
    build_safe_provider_async_http_client,
    build_safe_provider_http_client,
    mask_sensitive_token,
    validate_provider_endpoint,
    verify_response_size,
)
from backend.app.services.providers.security import _enforce_no_redirect


# ---------------------------------------------------------------------------
# 1. SSRF & URL Validation Tests
# ---------------------------------------------------------------------------

def test_validate_provider_endpoint_accepts_valid_public_https():
    """Verify standard public HTTPS endpoints are accepted."""
    assert validate_provider_endpoint("https://api.openai.com/v1") == "https://api.openai.com/v1"
    assert validate_provider_endpoint("https://generativelanguage.googleapis.com") == "https://generativelanguage.googleapis.com"
    assert validate_provider_endpoint("https://api.anthropic.com/v1") == "https://api.anthropic.com/v1"


def test_validate_provider_endpoint_rejects_insecure_http():
    """Verify HTTP scheme is rejected."""
    with pytest.raises(AIProviderBadRequestError, match="insecure scheme"):
        validate_provider_endpoint("http://api.openai.com/v1")


def test_validate_provider_endpoint_rejects_credentials_in_url():
    """Verify URLs with embedded username/password are rejected."""
    with pytest.raises(AIProviderBadRequestError, match="credentials"):
        validate_provider_endpoint("https://user:password@api.openai.com/v1")


def test_validate_provider_endpoint_rejects_non_standard_ports():
    """Verify non-standard ports (other than 443) are rejected to prevent port scanning."""
    with pytest.raises(AIProviderBadRequestError, match="non-standard port"):
        validate_provider_endpoint("https://api.openai.com:8443/v1")

    with pytest.raises(AIProviderBadRequestError, match="non-standard port"):
        validate_provider_endpoint("https://api.openai.com:8080/v1")


@pytest.mark.parametrize(
    "forbidden_host",
    [
        "localhost",
        "127.0.0.1",
        "127.0.1.1",
        "0.0.0.0",
        "169.254.169.254",  # AWS/GCP/Azure link-local metadata
        "10.0.0.1",        # RFC 1918 Private class A
        "172.16.0.1",       # RFC 1918 Private class B
        "192.168.1.1",      # RFC 1918 Private class C
        "[::1]",            # IPv6 loopback
        "[fe80::1]",        # IPv6 link-local
    ],
)
def test_validate_provider_endpoint_rejects_loopback_and_private_ips(forbidden_host: str):
    """Verify direct private/loopback/link-local IP addresses and hostnames are rejected."""
    url = f"https://{forbidden_host}/v1"
    with pytest.raises(AIProviderBadRequestError, match="Forbidden host|Private or link-local IP"):
        validate_provider_endpoint(url)


def test_validate_provider_endpoint_rejects_dns_rebinding_to_internal_ip():
    """Verify DNS resolution check rejects public domains resolving to private/internal IPs."""
    with patch("backend.app.services.providers.security.socket.getaddrinfo") as mock_dns:
        # Simulate DNS resolving evil-domain.internal to 127.0.0.1
        mock_dns.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))
        ]
        with pytest.raises(AIProviderBadRequestError, match="resolves to restricted internal IP"):
            validate_provider_endpoint("https://evil-internal-domain.com/v1")


def test_validate_provider_endpoint_rejects_unresolvable_domains():
    """Verify hostnames that fail DNS resolution are rejected safely."""
    with patch("backend.app.services.providers.security.socket.getaddrinfo", side_effect=socket.gaierror("Name or service not known")):
        with pytest.raises(AIProviderBadRequestError, match="could not be resolved"):
            validate_provider_endpoint("https://non-existent-domain-xyz-404.com/v1")


# ---------------------------------------------------------------------------
# 2. HTTP Redirect Security & Hook Tests
# ---------------------------------------------------------------------------

def test_redirect_security_constants():
    """Verify redirect security constants strictly disallow following redirects."""
    assert FOLLOW_REDIRECTS is False
    assert ALLOW_REDIRECTS is False
    assert DISALLOW_REDIRECTS is False


def test_enforce_no_redirect_hook_blocks_redirect_status_codes():
    """Verify _enforce_no_redirect hook raises AIProviderBadRequestError on 3xx responses."""
    mock_request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")

    for status_code in (301, 302, 303, 307, 308):
        mock_response = httpx.Response(status_code, request=mock_request, headers={"Location": "https://attacker.com/leak"})
        with pytest.raises(AIProviderBadRequestError, match="Redirects are disabled"):
            _enforce_no_redirect(mock_response)


def test_enforce_no_redirect_hook_allows_standard_2xx_and_error_codes():
    """Verify _enforce_no_redirect hook allows 200, 400, 429, 500 without raising."""
    mock_request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    for status_code in (200, 201, 400, 401, 429, 500):
        mock_response = httpx.Response(status_code, request=mock_request)
        # Should not raise exception
        _enforce_no_redirect(mock_response)


def test_build_safe_provider_http_client_enforces_no_redirects_and_timeouts():
    """Verify built HTTP client has follow_redirects=False and correct timeouts."""
    client = build_safe_provider_http_client(timeout_seconds=15.0)
    try:
        assert client.follow_redirects is False
        assert client.timeout.read == 15.0
    finally:
        client.close()


@pytest.mark.anyio
async def test_build_safe_provider_async_http_client_enforces_no_redirects():
    """Verify built async HTTP client has follow_redirects=False."""
    client = build_safe_provider_async_http_client(timeout_seconds=20.0)
    try:
        assert client.follow_redirects is False
        assert client.timeout.read == 20.0
    finally:
        await client.aclose()


# ---------------------------------------------------------------------------
# 3. Response Size Limit Tests
# ---------------------------------------------------------------------------

def test_verify_response_size_allows_safe_content_length():
    """Verify payloads within size limit pass verification."""
    mock_request = httpx.Request("GET", "https://api.openai.com/v1/models")
    safe_response = httpx.Response(
        200,
        request=mock_request,
        headers={"Content-Length": "1048576"},  # 1 MB
        content=b"x" * 1024,
    )
    verify_response_size(safe_response)


def test_verify_response_size_rejects_oversized_content_length_header():
    """Verify Content-Length header exceeding limit is rejected before reading body."""
    mock_request = httpx.Request("GET", "https://api.openai.com/v1/models")
    oversized_response = httpx.Response(
        200,
        request=mock_request,
        headers={"Content-Length": str(MAX_SAFE_RESPONSE_BYTES + 1)},
    )
    with pytest.raises(AIProviderBadRequestError, match="exceeds safe limit"):
        verify_response_size(oversized_response)


def test_verify_response_size_rejects_oversized_body_without_content_length():
    """Verify body length exceeding limit is rejected even if Content-Length header is missing."""
    mock_request = httpx.Request("GET", "https://api.openai.com/v1/models")
    oversized_response = httpx.Response(
        200,
        request=mock_request,
        content=b"x" * (MAX_SAFE_RESPONSE_BYTES + 1024),
    )
    with pytest.raises(AIProviderBadRequestError, match="exceeds safe limit"):
        verify_response_size(oversized_response)


# ---------------------------------------------------------------------------
# 4. Token & Credential Masking Tests
# ---------------------------------------------------------------------------

def test_mask_sensitive_token_preserves_privacy():
    """Verify token masking shows only safe prefix/suffix without exposing credentials."""
    assert mask_sensitive_token("sk-proj-1234567890abcdef") == "sk-...cdef"
    assert mask_sensitive_token("test-only-fake-token-1234") == "tes...1234"
    assert mask_sensitive_token("12345") == "...2345"
    assert mask_sensitive_token("1234") == "****"
    assert mask_sensitive_token("12") == "****"
    assert mask_sensitive_token("") == "****"
    assert mask_sensitive_token(None) == "****"


# ---------------------------------------------------------------------------
# 5. Repository Test Fixture Secret Hygiene Automated Regression Test
# ---------------------------------------------------------------------------

def test_no_google_api_key_pattern_in_test_files():
    """
    Automated regression gate:
    Verify that no test files contain strings matching the Google API key pattern
    (AIza[0-9A-Za-z-_]{35}), which triggers GitHub Secret Scanning alerts.
    """
    import os
    from pathlib import Path

    repo_root = Path(__file__).resolve().parent.parent.parent
    backend_tests = repo_root / "backend" / "tests"
    frontend_tests = repo_root / "frontend" / "src" / "__tests__"

    google_key_regex = re.compile(r"AIza[0-9A-Za-z_-]{35}")
    violations: list[str] = []

    for test_dir in [backend_tests, frontend_tests]:
        if not test_dir.exists():
            continue
        for root, _, files in os.walk(test_dir):
            for file in files:
                if file.endswith((".py", ".ts", ".tsx", ".js")):
                    file_path = Path(root) / file
                    content = file_path.read_text(encoding="utf-8", errors="ignore")
                    matches = google_key_regex.findall(content)
                    if matches:
                        violations.append(f"{file_path.name}: found {len(matches)} pattern match(es)")

    assert not violations, f"Detected potential secret scanner triggers in test files: {violations}"
