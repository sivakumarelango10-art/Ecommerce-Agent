from __future__ import annotations

import io
import ipaddress
import logging
from pathlib import Path
import re
import socket
from typing import Any, AsyncIterator, Iterator
from unittest.mock import MagicMock, patch

import httpx
import pytest

from backend.app.services.providers import (
    AIProviderBadRequestError,
    AIProviderSSRFError,
    DEFAULT_OUTBOUND_TIMEOUT_SECONDS,
    FOLLOW_REDIRECTS,
    HardenedAsyncHTTPTransport,
    HardenedHTTPTransport,
    MAX_SAFE_RESPONSE_BYTES,
    TRUSTED_PROVIDER_HOSTS,
    aread_safe_response_body,
    build_safe_provider_async_http_client,
    build_safe_provider_http_client,
    is_trusted_provider_host,
    mask_sensitive_token,
    read_safe_response_body,
    validate_provider_endpoint,
    verify_response_size,
)
from backend.app.services.providers.security import _enforce_no_redirect, _sanitize_redirect_target


# ---------------------------------------------------------------------------
# 1. Scanner & Gitleaks Configuration Verification Tests (Section 1)
# ---------------------------------------------------------------------------

def test_gitleaks_config_has_no_blanket_test_directory_exclusions():
    """Verify that .gitleaks.toml does not exempt test directories or .env.example."""
    repo_root = Path(__file__).resolve().parent.parent.parent
    gitleaks_config_path = repo_root / ".gitleaks.toml"
    assert gitleaks_config_path.exists(), ".gitleaks.toml configuration must exist"

    content = gitleaks_config_path.read_text(encoding="utf-8")

    # Blanket directory suppression must be absent
    assert "backend/tests" not in content, "Blanket exclusion of backend/tests/ is strictly forbidden"
    assert "frontend/src/__tests__" not in content, "Blanket exclusion of frontend/src/__tests__/ is strictly forbidden"
    assert "__tests__" not in content

    # .env.example must NOT be exempted from scanning
    assert ".env.example" not in content, ".env.example must not be exempted from secret scanning"

    # No broad or path-level allowlist blocks in .gitleaks.toml
    assert "[allowlist]" not in content
    assert "backend/tests/test_admin_auth" not in content


def test_env_example_contains_only_blank_safe_placeholders():
    """Verify .env.example does not contain real-looking fake keys, only blank placeholders."""
    repo_root = Path(__file__).resolve().parent.parent.parent
    env_example = (repo_root / ".env.example").read_text(encoding="utf-8")

    for line in env_example.splitlines():
        trimmed = line.strip()
        if not trimmed or trimmed.startswith("#"):
            continue
        if "=" in trimmed:
            key, val = trimmed.split("=", 1)
            # Safe defaults like GEMINI_MODEL or numbers are fine; secrets must be blank
            if "KEY" in key or "SECRET" in key:
                assert val == "", f"Secret setting {key} must have blank placeholder in .env.example"


# ---------------------------------------------------------------------------
# 2. Pinned GitHub Actions Immutable References Tests (Section 2)
# ---------------------------------------------------------------------------

def test_security_workflow_pins_actions_to_immutable_shas():
    """Verify that all GitHub Actions are pinned to verified 40-character commit SHAs."""
    repo_root = Path(__file__).resolve().parent.parent.parent
    workflow_path = repo_root / ".github" / "workflows" / "security-scan.yml"
    assert workflow_path.exists(), "security-scan.yml must exist"

    content = workflow_path.read_text(encoding="utf-8")

    # Verified commit SHA for actions/checkout v4.2.2
    checkout_pin = "actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683"
    assert checkout_pin in content, f"actions/checkout must be pinned to {checkout_pin}"

    # Verified commit SHA for gitleaks/gitleaks-action v2.3.9
    gitleaks_pin = "gitleaks/gitleaks-action@ff98106e4c7b2bc287b24eaf42907196329070c7"
    assert gitleaks_pin in content, f"gitleaks/gitleaks-action must be pinned to {gitleaks_pin}"

    # Verify 40-character hexadecimal SHA format
    uses_lines = [line.strip() for line in content.splitlines() if line.strip().startswith("uses:")]
    assert len(uses_lines) >= 2
    for line in uses_lines:
        action_ref = line.split("uses:")[1].strip().split("#")[0].strip()
        name, sha = action_ref.split("@")
        assert len(sha) == 40, f"Action '{name}' is not pinned to a 40-character commit SHA: '{sha}'"
        assert re.match(r"^[0-9a-f]{40}$", sha), f"SHA for '{name}' is not lowercase hex"


def test_security_workflow_permissions_and_depth():
    """Verify least privilege permissions and full git history availability for scanning."""
    repo_root = Path(__file__).resolve().parent.parent.parent
    content = (repo_root / ".github" / "workflows" / "security-scan.yml").read_text(encoding="utf-8")

    assert "permissions:" in content
    assert "contents: read" in content
    assert "fetch-depth: 0" in content


# ---------------------------------------------------------------------------
# 3. HTTP Redirect Policy & Sanitized Logging Tests (Section 3)
# ---------------------------------------------------------------------------

def test_redirect_single_policy_constant():
    """Verify that a single clearly named policy constant is exported."""
    assert FOLLOW_REDIRECTS is False
    # Ensure confusing/contradictory aliases were removed
    import backend.app.services.providers.security as sec
    assert not hasattr(sec, "ALLOW_REDIRECTS")
    assert not hasattr(sec, "DISALLOW_REDIRECTS")


def test_http_clients_retain_follow_redirects_false():
    """Verify both sync and async HTTP clients strictly retain follow_redirects=False."""
    sync_client = build_safe_provider_http_client(timeout_seconds=10.0)
    try:
        assert sync_client.follow_redirects is False
    finally:
        sync_client.close()

    async_client = build_safe_provider_async_http_client(timeout_seconds=10.0)
    try:
        assert async_client.follow_redirects is False
    finally:
        pass


@pytest.mark.parametrize("status_code", [300, 301, 302, 303, 305, 307, 308])
def test_all_3xx_redirect_codes_are_rejected(status_code: int):
    """Verify all 3xx redirect status codes are caught and rejected with AIProviderBadRequestError."""
    mock_request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    mock_response = httpx.Response(
        status_code,
        request=mock_request,
        headers={"Location": "https://attacker.com/leak"},
    )
    with pytest.raises(AIProviderBadRequestError, match="Redirects are disabled"):
        _enforce_no_redirect(mock_response)


def test_redirect_logging_never_exposes_raw_location_or_query_parameters():
    """Verify redirect logging never exposes raw URLs, query strings, or embedded credentials."""
    mock_request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    leaky_location = "https://admin:supersecret@attacker.com/leak/path?secret_token=PRIVATE_API_TOKEN_999&user_id=123#fragment"
    mock_response = httpx.Response(
        302,
        request=mock_request,
        headers={"Location": leaky_location},
    )

    with patch("backend.app.services.providers.security.logger.warning") as mock_warn:
        with pytest.raises(AIProviderBadRequestError) as exc_info:
            _enforce_no_redirect(mock_response)

        # 1. Error message verification: only sanitized target host, no query string, no credentials
        err_msg = str(exc_info.value)
        assert "attacker.com" in err_msg
        assert "PRIVATE_API_TOKEN" not in err_msg
        assert "supersecret" not in err_msg
        assert "secret_token=" not in err_msg

        # 2. Structured log arguments verification: structured extra dict only, no leaks
        assert mock_warn.called
        call_args = mock_warn.call_args
        log_msg = call_args[0][0]
        extra = call_args[1].get("extra", {})

        assert extra.get("target_host") == "attacker.com"
        assert extra.get("status_code") == 302
        assert extra.get("request_host") == "api.openai.com"

        assert "PRIVATE_API_TOKEN" not in log_msg
        assert "supersecret" not in log_msg
        assert "secret_token=" not in log_msg


# ---------------------------------------------------------------------------
# 4. Streaming Response Limits Tests (Section 4)
# ---------------------------------------------------------------------------

class FakeStreamResponse:
    """Mock HTTP response supporting streamed chunk iteration and close tracking."""

    def __init__(
        self,
        chunks: list[bytes],
        headers: dict[str, str] | None = None,
        status_code: int = 200,
    ) -> None:
        self.chunks = chunks
        self.headers = httpx.Headers(headers or {})
        self.status_code = status_code
        self.closed = False
        self.async_closed = False

    def iter_bytes(self, chunk_size: int = 65536) -> Iterator[bytes]:
        for c in self.chunks:
            if self.closed:
                break
            yield c

    async def aiter_bytes(self, chunk_size: int = 65536) -> AsyncIterator[bytes]:
        for c in self.chunks:
            if self.async_closed:
                break
            yield c

    def close(self) -> None:
        self.closed = True

    async def aclose(self) -> None:
        self.async_closed = True


def test_read_safe_response_body_oversized_content_length():
    """Verify oversized Content-Length header is rejected up-front without reading body and closes stream."""
    resp = FakeStreamResponse(
        chunks=[b"small chunk"],
        headers={"Content-Length": str(MAX_SAFE_RESPONSE_BYTES + 1000)},
    )
    with pytest.raises(AIProviderBadRequestError, match="exceeds safe limit"):
        read_safe_response_body(resp)
    assert resp.closed is True


def test_read_safe_response_body_missing_content_length_stops_immediately():
    """Verify streaming response without Content-Length stops immediately once limit is exceeded."""
    chunk_1mb = b"A" * (1024 * 1024)
    # 12 MB total in 1 MB chunks
    resp = FakeStreamResponse(
        chunks=[chunk_1mb for _ in range(12)],
        headers={},  # Missing Content-Length
    )
    with pytest.raises(AIProviderBadRequestError, match="exceeded safe limit .* during streaming"):
        read_safe_response_body(resp, max_bytes=5 * 1024 * 1024)  # 5 MB limit
    assert resp.closed is True


def test_read_safe_response_body_incorrect_content_length():
    """Verify deceptive Content-Length (header claims 50 bytes, body streams 2 MB) is caught and closed."""
    chunk_500k = b"X" * (500 * 1024)
    resp = FakeStreamResponse(
        chunks=[chunk_500k for _ in range(4)],  # 2 MB total
        headers={"Content-Length": "50"},  # Deceptive header
    )
    with pytest.raises(AIProviderBadRequestError, match="exceeded safe limit .* during streaming"):
        read_safe_response_body(resp, max_bytes=1024 * 1024)  # 1 MB limit
    assert resp.closed is True


def test_read_safe_response_body_exactly_at_limit():
    """Verify response payload exactly at configured limit passes cleanly."""
    exact_payload = b"Z" * 1024
    resp = FakeStreamResponse(
        chunks=[b"Z" * 512, b"Z" * 512],
        headers={"Content-Length": "1024"},
    )
    data = read_safe_response_body(resp, max_bytes=1024)
    assert data == exact_payload
    assert len(data) == 1024


@pytest.mark.anyio
async def test_aread_safe_response_body_async_streaming_limit():
    """Verify async streaming response reader enforces byte limits and closes async stream."""
    chunk_2mb = b"Q" * (2 * 1024 * 1024)
    resp = FakeStreamResponse(
        chunks=[chunk_2mb for _ in range(3)],  # 6 MB total
        headers={},
    )
    with pytest.raises(AIProviderBadRequestError, match="exceeded safe limit .* during streaming"):
        await aread_safe_response_body(resp, max_bytes=3 * 1024 * 1024)  # 3 MB limit
    assert resp.async_closed is True


# ---------------------------------------------------------------------------
# 5. SSRF & Hardened DNS Boundaries Tests (Section 5)
# ---------------------------------------------------------------------------

def test_validate_provider_endpoint_accepts_valid_public_https():
    """Verify standard public HTTPS endpoints are accepted."""
    assert validate_provider_endpoint("https://api.openai.com/v1") == "https://api.openai.com/v1"
    assert validate_provider_endpoint("https://generativelanguage.googleapis.com") == "https://generativelanguage.googleapis.com"
    assert validate_provider_endpoint("https://api.anthropic.com/v1") == "https://api.anthropic.com/v1"


def test_validate_provider_endpoint_hostname_normalization_trailing_dots():
    """Verify hostname normalization strips trailing dots safely (e.g. FQDN trailing dot)."""
    normalized = validate_provider_endpoint("https://api.openai.com./v1")
    assert normalized == "https://api.openai.com./v1"


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
        "[fd00:ec2::254]",  # IPv6 cloud metadata
        "[::ffff:127.0.0.1]",       # IPv4-mapped IPv6 loopback
        "[::ffff:169.254.169.254]", # IPv4-mapped IPv6 metadata
        "[::ffff:10.0.0.1]",        # IPv4-mapped IPv6 private
        "[64:ff9b::127.0.0.1]",     # IPv6 NAT64 prefix
    ],
)
def test_validate_provider_endpoint_rejects_loopback_and_private_ips(forbidden_host: str):
    """Verify direct private/loopback/link-local/mapped IP addresses and hostnames are rejected."""
    url = f"https://{forbidden_host}/v1"
    with pytest.raises(AIProviderBadRequestError, match="Forbidden host|Private or link-local IP"):
        validate_provider_endpoint(url)


def test_validate_provider_endpoint_rejects_dns_multi_answer_rebinding():
    """Verify DNS resolution check rejects domains resolving to mixed public and private IPs."""
    with patch("backend.app.services.providers.security.socket.getaddrinfo") as mock_dns:
        # Simulate DNS returning a public IP followed by a private 127.0.0.1 IP
        mock_dns.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
        ]
        with pytest.raises(AIProviderBadRequestError, match="resolves to restricted internal IP"):
            validate_provider_endpoint("https://split-dns-target.com/v1")


def test_validate_provider_endpoint_rejects_unresolvable_domains():
    """Verify hostnames that fail DNS resolution are rejected safely."""
    with patch("backend.app.services.providers.security.socket.getaddrinfo", side_effect=socket.gaierror("Name or service not known")):
        with pytest.raises(AIProviderBadRequestError, match="could not be resolved"):
            validate_provider_endpoint("https://non-existent-domain-xyz-404.com/v1")


def test_trusted_provider_hosts_allowlist():
    """Verify trusted provider host allowlist checks approved and rejected hosts."""
    assert is_trusted_provider_host("api.openai.com") is True
    assert is_trusted_provider_host("generativelanguage.googleapis.com") is True
    assert is_trusted_provider_host("api.anthropic.com") is True
    assert is_trusted_provider_host("api.groq.com") is True
    assert is_trusted_provider_host("bedrock-runtime.us-east-1.amazonaws.com") is True
    assert is_trusted_provider_host("models.inference.ai.azure.com") is True

    # Rejection of lookalike and arbitrary untrusted domains
    assert is_trusted_provider_host("api.openai.com.attacker.com") is False
    assert is_trusted_provider_host("evil-openai.com") is False
    assert is_trusted_provider_host("attacker.internal") is False


def test_validate_provider_endpoint_enforce_trusted_hosts():
    """Verify enforce_trusted_hosts option rejects untrusted hostnames."""
    # Trusted host passes
    assert validate_provider_endpoint(
        "https://api.openai.com/v1",
        enforce_trusted_hosts=True,
    ) == "https://api.openai.com/v1"

    # Untrusted external host fails when enforce_trusted_hosts=True
    with pytest.raises(AIProviderBadRequestError, match="not in the approved AI provider infrastructure allowlist"):
        validate_provider_endpoint(
            "https://arbitrary-untrusted-api.com/v1",
            enforce_trusted_hosts=True,
        )


def test_hardened_transport_pins_destination_ip_and_blocks_rebinding():
    """Verify HardenedSyncBackend intercepts host connection and blocks restricted destination IPs."""
    from backend.app.services.providers.security import HardenedSyncBackend

    backend = HardenedSyncBackend()
    with pytest.raises(AIProviderSSRFError, match="Forbidden host|restricted internal IP"):
        backend.connect_tcp("127.0.0.1", 443)

    with pytest.raises(AIProviderSSRFError, match="Forbidden host|restricted internal IP"):
        backend.connect_tcp("169.254.169.254", 443)


@pytest.mark.anyio
async def test_hardened_async_transport_pins_destination_ip_and_blocks_rebinding():
    """Verify HardenedAsyncBackend intercepts host connection and blocks restricted destination IPs."""
    from backend.app.services.providers.security import HardenedAsyncBackend

    backend = HardenedAsyncBackend()
    with pytest.raises(AIProviderSSRFError, match="Forbidden host|restricted internal IP"):
        await backend.connect_tcp("127.0.0.1", 443)

    with pytest.raises(AIProviderSSRFError, match="Forbidden host|restricted internal IP"):
        await backend.connect_tcp("169.254.169.254", 443)


def test_build_safe_provider_http_clients_use_hardened_transports():
    """Verify client builder functions default to Hardened transports with connection-time IP pinning."""
    client = build_safe_provider_http_client()
    assert isinstance(client._transport, HardenedHTTPTransport)
    client.close()

    async_client = build_safe_provider_async_http_client()
    assert isinstance(async_client._transport, HardenedAsyncHTTPTransport)


# ---------------------------------------------------------------------------
# 6. Token & Credential Masking Tests
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
# 7. Repository Test Fixture Secret Hygiene Automated Regression Test
# ---------------------------------------------------------------------------

def test_no_google_api_key_pattern_in_test_files():
    """
    Automated regression gate:
    Verify that no test files contain strings matching the Google API key pattern
    (AIza[0-9A-Za-z-_]{35}), which triggers GitHub Secret Scanning alerts.
    """
    import os

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
