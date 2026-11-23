from types import SimpleNamespace
from unittest.mock import MagicMock
import urllib.parse
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.app.core.config import Settings
from backend.app.db.base import Base
from backend.app.db.models import User
from backend.app.main import create_app
from backend.app.security.passwords import hash_password
from backend.app.security.tokens import create_access_token
from backend.app.services.neon_service import NeonService

ALL_NEON_ENDPOINTS = [
    ("GET", "/api/v1/neon/status", None),
    ("GET", "/api/v1/neon/function/hello", None),
    ("POST", "/api/v1/neon/storage/upload", {"key": "test.txt", "content": "hello"}),
    ("GET", "/api/v1/neon/storage/download?key=test.txt", None),
    ("GET", "/api/v1/neon/ai-gateway/models", None),
]


def create_test_user(
    session: Session,
    email: str,
    is_active: bool = True,
) -> User:
    user = User(
        id=uuid4(),
        email=email,
        password_hash=hash_password("Password123!"),
        is_active=is_active,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def mock_neon_service():
    """Mock NeonService to avoid any external network calls to Neon or AWS."""
    service = MagicMock(spec=NeonService)
    service.get_all_status.return_value = {
        "branch": "test-branch",
        "branch_id": "br-test-123",
        "services": {
            "postgres": {"status": "connected"},
            "functions": {"status": "available"},
            "object_storage": {"status": "available"},
            "ai-gateway": {"status": "available"},
            "auth": {"status": "available"},
            "realtime": {"status": "available"},
        },
    }
    service.check_functions.return_value = {
        "status": "success",
        "response": "Hello from mock function",
    }
    mock_s3 = MagicMock()
    mock_s3.put_object.return_value = SimpleNamespace(status_code=200, text="OK")
    mock_s3.get_object.return_value = SimpleNamespace(
        status_code=200,
        text="mock-file-content",
        headers={"content-type": "text/plain"},
    )
    service._storage_client = mock_s3
    service.settings = SimpleNamespace(
        storage_max_upload_bytes=2 * 1024 * 1024,
        storage_allowed_content_types={
            "text/plain",
            "text/markdown",
            "text/csv",
            "application/json",
            "application/octet-stream",
        },
    )
    service.check_ai_gateway.return_value = {
        "status": "success",
        "models_count": 5,
        "models": [{"id": "meta/llama-3.1-8b"}],
    }
    return service


@pytest.mark.parametrize("method,path,payload", ALL_NEON_ENDPOINTS)
def test_neon_endpoints_reject_unauthenticated_requests(
    api_client,
    method,
    path,
    payload,
):
    """Verify that unauthenticated requests to any Neon endpoint return 401."""
    client, _ = api_client
    if method == "GET":
        res = client.get(path)
    else:
        res = client.post(path, json=payload)

    assert res.status_code == 401
    assert "WWW-Authenticate" in res.headers
    assert res.json()["detail"] == "Authentication is required."


@pytest.mark.parametrize("method,path,payload", ALL_NEON_ENDPOINTS)
def test_neon_endpoints_reject_invalid_and_expired_tokens(
    api_client,
    method,
    path,
    payload,
):
    """Verify that invalid or malformed bearer tokens return 401."""
    client, _ = api_client
    headers = {"Authorization": "Bearer invalid.token.value"}
    if method == "GET":
        res = client.get(path, headers=headers)
    else:
        res = client.post(path, json=payload, headers=headers)

    assert res.status_code == 401
    assert res.json()["detail"] == "Authentication is required."


@pytest.mark.parametrize("method,path,payload", ALL_NEON_ENDPOINTS)
def test_neon_endpoints_reject_inactive_users(
    api_client,
    method,
    path,
    payload,
):
    """Verify that inactive users return 401 even if allowlisted as admin."""
    client, _ = api_client
    client.app.state.settings.admin_emails = {"inactive-admin@storeflow.com"}

    session = client.app.state.db_session_factory()
    try:
        user = create_test_user(
            session,
            email="inactive-admin@storeflow.com",
            is_active=False,
        )
        token = create_access_token(user.id, client.app.state.settings)
    finally:
        session.close()

    headers = auth_header(token)
    if method == "GET":
        res = client.get(path, headers=headers)
    else:
        res = client.post(path, json=payload, headers=headers)

    assert res.status_code == 401
    assert res.json()["detail"] == "Authentication is required."


@pytest.mark.parametrize("method,path,payload", ALL_NEON_ENDPOINTS)
def test_neon_endpoints_reject_ordinary_customers_with_403(
    api_client,
    method,
    path,
    payload,
):
    """Verify that authenticated active customers NOT in ADMIN_EMAILS receive 403 Forbidden."""
    client, _ = api_client
    client.app.state.settings.admin_emails = {"authorized-admin@storeflow.com"}

    session = client.app.state.db_session_factory()
    try:
        user = create_test_user(
            session,
            email="ordinary-customer@example.com",
            is_active=True,
        )
        token = create_access_token(user.id, client.app.state.settings)
    finally:
        session.close()

    headers = auth_header(token)
    if method == "GET":
        res = client.get(path, headers=headers)
    else:
        res = client.post(path, json=payload, headers=headers)

    assert res.status_code == 403
    assert res.json()["detail"] == "Admin privileges are required to access this resource."


@pytest.mark.parametrize("method,path,payload", ALL_NEON_ENDPOINTS)
def test_neon_endpoints_empty_admin_allowlist_fails_closed(
    api_client,
    method,
    path,
    payload,
):
    """Verify that an empty ADMIN_EMAILS allowlist fails closed: all users receive 403."""
    client, _ = api_client
    client.app.state.settings.admin_emails = set()

    session = client.app.state.db_session_factory()
    try:
        user = create_test_user(
            session,
            email="wouldbeadmin@storeflow.com",
            is_active=True,
        )
        token = create_access_token(user.id, client.app.state.settings)
    finally:
        session.close()

    headers = auth_header(token)
    if method == "GET":
        res = client.get(path, headers=headers)
    else:
        res = client.post(path, json=payload, headers=headers)

    assert res.status_code == 403
    assert res.json()["detail"] == "Admin privileges are required to access this resource."


@pytest.mark.parametrize("method,path,payload", ALL_NEON_ENDPOINTS)
def test_neon_endpoints_allow_allowlisted_admin(
    api_client,
    mock_neon_service,
    monkeypatch,
    method,
    path,
    payload,
):
    """Verify that an authenticated user in ADMIN_EMAILS can access all Neon endpoints."""
    client, _ = api_client
    client.app.state.settings.admin_emails = {"lead-admin@storeflow.com"}

    # Mock the get_neon_service dependency
    from backend.app.api.routes import neon as neon_routes

    client.app.dependency_overrides[neon_routes.get_neon_service] = (
        lambda: mock_neon_service
    )

    try:
        session = client.app.state.db_session_factory()
        try:
            admin_user = create_test_user(
                session,
                email="lead-admin@storeflow.com",
                is_active=True,
            )
            token = create_access_token(admin_user.id, client.app.state.settings)
        finally:
            session.close()

        headers = auth_header(token)
        if method == "GET":
            res = client.get(path, headers=headers)
        else:
            res = client.post(path, json=payload, headers=headers)

        assert res.status_code == 200, f"Expected 200 for {method} {path}, got {res.status_code}: {res.text}"
    finally:
        client.app.dependency_overrides.clear()


def test_neon_admin_email_normalization_and_case_insensitivity(
    api_client,
    mock_neon_service,
):
    """Verify case-insensitivity and whitespace trimming for admin allowlist."""
    client, _ = api_client
    from backend.app.api.routes import neon as neon_routes

    client.app.dependency_overrides[neon_routes.get_neon_service] = (
        lambda: mock_neon_service
    )

    # Allowlist set with mixed casing
    client.app.state.settings.admin_emails = {"sysadmin@storeflow.com"}

    try:
        session = client.app.state.db_session_factory()
        try:
            # User email in DB has mixed casing
            user = create_test_user(
                session,
                email="SysAdmin@StoreFlow.com",
                is_active=True,
            )
            token = create_access_token(user.id, client.app.state.settings)
        finally:
            session.close()

        res = client.get("/api/v1/neon/status", headers=auth_header(token))
        assert res.status_code == 200
    finally:
        client.app.dependency_overrides.clear()


def test_admin_emails_config_parsing():
    """Verify Settings parses comma-separated, lists, whitespace, and handles empty sets."""
    # 1. Comma-separated string with mixed case and whitespace
    s1 = Settings(
        GEMINI_API_KEY="test",
        DATABASE_URL="sqlite+pysqlite:///:memory:",
        AUTH_SECRET_KEY="01234567890123456789012345678901",
        ADMIN_EMAILS="  Admin1@StoreFlow.com , Admin2@StoreFlow.com ,   ",
    )
    assert s1.admin_emails == {"admin1@storeflow.com", "admin2@storeflow.com"}

    # 2. Empty string
    s2 = Settings(
        GEMINI_API_KEY="test",
        DATABASE_URL="sqlite+pysqlite:///:memory:",
        AUTH_SECRET_KEY="01234567890123456789012345678901",
        ADMIN_EMAILS="   ",
    )
    assert s2.admin_emails == set()

    # 3. JSON list string
    s3 = Settings(
        GEMINI_API_KEY="test",
        DATABASE_URL="sqlite+pysqlite:///:memory:",
        AUTH_SECRET_KEY="01234567890123456789012345678901",
        ADMIN_EMAILS='["UserA@SF.com", "UserB@SF.com"]',
    )
    assert s3.admin_emails == {"usera@sf.com", "userb@sf.com"}

    # 4. Unset default
    s4 = Settings(
        GEMINI_API_KEY="test",
        DATABASE_URL="sqlite+pysqlite:///:memory:",
        AUTH_SECRET_KEY="01234567890123456789012345678901",
    )
    assert s4.admin_emails == set()
    assert s4.storage_max_upload_bytes == 2 * 1024 * 1024
    assert "text/plain" in s4.storage_allowed_content_types


def test_storage_upload_oversized_payload_returns_413(
    api_client,
    mock_neon_service,
):
    """Verify that uploads exceeding storage_max_upload_bytes return HTTP 413."""
    client, _ = api_client
    from backend.app.api.routes import neon as neon_routes

    client.app.dependency_overrides[neon_routes.get_neon_service] = (
        lambda: mock_neon_service
    )
    try:
        token = create_admin_token_helper(client, "admin-upload-limits@storeflow.com")
        # 2 MB + 100 bytes
        oversized_content = "a" * (2 * 1024 * 1024 + 100)
        res = client.post(
            "/api/v1/neon/storage/upload",
            json={
                "key": "oversized.txt",
                "content": oversized_content,
                "content_type": "text/plain",
            },
            headers=auth_header(token),
        )
        assert res.status_code == 413
        assert "exceeds maximum permitted size" in res.json()["detail"]
    finally:
        client.app.dependency_overrides.clear()


def test_storage_upload_oversized_content_length_header_returns_413(
    api_client,
    mock_neon_service,
):
    """Verify that requests with oversized Content-Length headers are rejected with 413."""
    client, _ = api_client
    from backend.app.api.routes import neon as neon_routes

    client.app.dependency_overrides[neon_routes.get_neon_service] = (
        lambda: mock_neon_service
    )
    try:
        token = create_admin_token_helper(client, "admin-cl@storeflow.com")
        headers = {
            **auth_header(token),
            "Content-Length": "3145728",  # 3 MB
        }
        res = client.post(
            "/api/v1/neon/storage/upload",
            json={
                "key": "test.txt",
                "content": "small content",
                "content_type": "text/plain",
            },
            headers=headers,
        )
        assert res.status_code == 413
        assert "exceeds maximum permitted size" in res.json()["detail"]
    finally:
        client.app.dependency_overrides.clear()


@pytest.mark.parametrize(
    "bad_content_type",
    [
        "text/html",
        "image/svg+xml",
        "application/javascript",
        "application/x-sh",
        "text/x-python",
    ],
)
def test_storage_upload_disallowed_content_types_return_415(
    api_client,
    mock_neon_service,
    bad_content_type,
):
    """Verify that disallowed content types return HTTP 415."""
    client, _ = api_client
    from backend.app.api.routes import neon as neon_routes

    client.app.dependency_overrides[neon_routes.get_neon_service] = (
        lambda: mock_neon_service
    )
    try:
        token = create_admin_token_helper(client, "admin-ct@storeflow.com")
        res = client.post(
            "/api/v1/neon/storage/upload",
            json={
                "key": "malicious.html",
                "content": "<h1>Hello</h1>",
                "content_type": bad_content_type,
            },
            headers=auth_header(token),
        )
        assert res.status_code == 415
        assert "not supported" in res.json()["detail"]
    finally:
        client.app.dependency_overrides.clear()


@pytest.mark.parametrize(
    "invalid_key",
    [
        "../../etc/passwd",
        "../secrets.env",
        "uploads/../../root.txt",
        "/leading/slash.txt",
        "trailing/slash/",
        "consecutive//slashes.txt",
        "control\x00character.txt",
        "control\nnewline.txt",
        "control\rreturn.txt",
        "space in key.txt",
        "special$char.txt",
        "a" * 257,
    ],
)
def test_storage_upload_and_download_invalid_keys_return_400(
    api_client,
    mock_neon_service,
    invalid_key,
):
    """Verify that traversal-like segments, control characters, and malformed keys return HTTP 400 on upload and download."""
    client, _ = api_client
    from backend.app.api.routes import neon as neon_routes

    client.app.dependency_overrides[neon_routes.get_neon_service] = (
        lambda: mock_neon_service
    )
    try:
        token = create_admin_token_helper(client, "admin-keys@storeflow.com")

        # 1. Test upload rejection
        upload_res = client.post(
            "/api/v1/neon/storage/upload",
            json={
                "key": invalid_key,
                "content": "safe content",
                "content_type": "text/plain",
            },
            headers=auth_header(token),
        )
        # Note: Pydantic max_length=256 causes 422 if > 256, our validator returns 400
        assert upload_res.status_code in {400, 422}

        # 2. Test download rejection
        encoded_key = urllib.parse.quote(invalid_key)
        download_res = client.get(
            f"/api/v1/neon/storage/download?key={encoded_key}",
            headers=auth_header(token),
        )
        assert download_res.status_code == 400
    finally:
        client.app.dependency_overrides.clear()


def test_storage_download_not_found_returns_clean_404(
    api_client,
    mock_neon_service,
):
    """Verify that 404 from upstream S3 returns clean 404 without leaking raw XML."""
    client, _ = api_client
    from backend.app.api.routes import neon as neon_routes

    mock_neon_service._storage_client.get_object.return_value = SimpleNamespace(
        status_code=404,
        text="<Error><Code>NoSuchKey</Code><StringToSign>secret_sig_string</StringToSign></Error>",
        headers={},
    )
    client.app.dependency_overrides[neon_routes.get_neon_service] = (
        lambda: mock_neon_service
    )
    try:
        token = create_admin_token_helper(client, "admin-s3-404@storeflow.com")
        res = client.get(
            "/api/v1/neon/storage/download?key=missing.txt",
            headers=auth_header(token),
        )
        assert res.status_code == 404
        assert "<Error>" not in res.text
        assert "secret_sig_string" not in res.text
        assert res.json()["detail"] == "Object 'missing.txt' was not found."
    finally:
        client.app.dependency_overrides.clear()


def test_storage_upload_upstream_error_sanitized_to_502(
    api_client,
    mock_neon_service,
):
    """Verify that upstream S3 errors return generic 502 without leaking credentials or XML."""
    client, _ = api_client
    from backend.app.api.routes import neon as neon_routes

    mock_neon_service._storage_client.put_object.return_value = SimpleNamespace(
        status_code=500,
        text="<Error><Code>SignatureDoesNotMatch</Code><StringToSign>SensitiveInternalSig</StringToSign></Error>",
    )
    client.app.dependency_overrides[neon_routes.get_neon_service] = (
        lambda: mock_neon_service
    )
    try:
        token = create_admin_token_helper(client, "admin-s3-500@storeflow.com")
        res = client.post(
            "/api/v1/neon/storage/upload",
            json={
                "key": "test.txt",
                "content": "valid content",
                "content_type": "text/plain",
            },
            headers=auth_header(token),
        )
        assert res.status_code == 502
        assert "SensitiveInternalSig" not in res.text
        assert "<Error>" not in res.text
        assert res.json()["detail"] == "Upstream object storage service error."
    finally:
        client.app.dependency_overrides.clear()


def test_storage_download_upstream_error_sanitized_to_502(
    api_client,
    mock_neon_service,
):
    """Verify that upstream 500 errors on download return generic 502 (not 404)."""
    client, _ = api_client
    from backend.app.api.routes import neon as neon_routes

    mock_neon_service._storage_client.get_object.return_value = SimpleNamespace(
        status_code=500,
        text="<Error><Code>InternalError</Code><HostId>internal-host</HostId></Error>",
        headers={},
    )
    client.app.dependency_overrides[neon_routes.get_neon_service] = (
        lambda: mock_neon_service
    )
    try:
        token = create_admin_token_helper(client, "admin-s3-dl-err@storeflow.com")
        res = client.get(
            "/api/v1/neon/storage/download?key=test.txt",
            headers=auth_header(token),
        )
        assert res.status_code == 502
        assert "internal-host" not in res.text
        assert "<Error>" not in res.text
        assert res.json()["detail"] == "Upstream object storage service error."
    finally:
        client.app.dependency_overrides.clear()


def test_function_hello_upstream_error_sanitized(
    api_client,
    mock_neon_service,
):
    """Verify that Neon Function error detail never leaks internal socket or IP info."""
    client, _ = api_client
    from backend.app.api.routes import neon as neon_routes

    mock_neon_service.check_functions.return_value = {
        "status": "error",
        "error": "ConnectionRefusedError: Connect to 192.168.1.100:443 failed",
    }
    client.app.dependency_overrides[neon_routes.get_neon_service] = (
        lambda: mock_neon_service
    )
    try:
        token = create_admin_token_helper(client, "admin-fn-err@storeflow.com")
        res = client.get(
            "/api/v1/neon/function/hello",
            headers=auth_header(token),
        )
        assert res.status_code == 502
        assert "192.168.1.100" not in res.text
        assert res.json()["detail"] == "Upstream function service error."
    finally:
        client.app.dependency_overrides.clear()


def test_ai_gateway_upstream_error_sanitized(
    api_client,
    mock_neon_service,
):
    """Verify that Neon AI Gateway error detail never leaks internal endpoint details."""
    client, _ = api_client
    from backend.app.api.routes import neon as neon_routes

    mock_neon_service.check_ai_gateway.return_value = {
        "status": "error",
        "error": "HTTP 503 from https://internal.databricks.gateway:8080/v1/models",
    }
    client.app.dependency_overrides[neon_routes.get_neon_service] = (
        lambda: mock_neon_service
    )
    try:
        token = create_admin_token_helper(client, "admin-ai-err@storeflow.com")
        res = client.get(
            "/api/v1/neon/ai-gateway/models",
            headers=auth_header(token),
        )
        assert res.status_code == 502
        assert "internal.databricks.gateway" not in res.text
        assert res.json()["detail"] == "Upstream AI Gateway service error."
    finally:
        client.app.dependency_overrides.clear()


def test_neon_sigv4_client_rfc3986_canonical_uri_and_lifecycle():
    """Verify NeonSigV4Client canonical URI encoding (RFC 3986), persistent client reuse, and repr safety."""
    from backend.app.services.neon_service import NeonSigV4Client

    client = NeonSigV4Client(
        endpoint_url="https://s3.neon.tech",
        access_key_id="test_key_id",
        secret_access_key="super_secret_access_key_value",
        region="us-east-2",
    )

    # 1. Credential concealment in repr
    client_repr = repr(client)
    assert "super_secret_access_key_value" not in client_repr
    assert "test_key_id" not in client_repr
    assert "s3.neon.tech" in client_repr

    # 2. RFC 3986 path encoding
    canonical_path = client._canonical_uri("assets", "uploads/my document (2026).txt")
    assert canonical_path == "/assets/uploads/my%20document%20%282026%29.txt"

    # 3. Persistent HTTP client reuse
    http_c1 = client._get_client()
    http_c2 = client._get_client()
    assert http_c1 is http_c2

    # 4. Cleanup
    client.close()
    assert client._client is None


def create_admin_token_helper(client, email: str) -> str:
    """Helper to register admin email and mint token for testing."""
    client.app.state.settings.admin_emails = {email}
    session = client.app.state.db_session_factory()
    try:
        user = create_test_user(session, email=email, is_active=True)
        return create_access_token(user.id, client.app.state.settings)
    finally:
        session.close()


def test_check_realtime_probe_error_sanitized(monkeypatch):
    """Verify that NeonService.check_realtime never leaks raw exception strings or socket details."""
    import httpx
    from pydantic import SecretStr

    mock_settings = SimpleNamespace(
        neon_realtime_url="wss://realtime.neon.tech/v1",
        neon_realtime_secret=SecretStr("super-secret-token-12345"),
        aws_endpoint_url_s3=None,
        aws_access_key_id=None,
        aws_secret_access_key=None,
    )

    sensitive_error_msg = "ConnectTimeout: failed to connect to 10.240.0.1:443 [sensitive_internal_socket]"

    def mock_get(*args, **kwargs):
        raise httpx.ConnectTimeout(sensitive_error_msg)

    monkeypatch.setattr(httpx.Client, "get", mock_get)

    service = NeonService(mock_settings)
    result = service.check_realtime()

    assert result["status"] == "configured"
    assert result["websocket_url"] == "wss://realtime.neon.tech/v1"
    assert result["secret_configured"] is True
    assert result["probe_note"] == "Realtime connectivity probe failed."
    assert sensitive_error_msg not in str(result)
    assert "10.240.0.1" not in str(result)
    assert "sensitive_internal_socket" not in str(result)
    assert "super-secret-token-12345" not in str(result)

