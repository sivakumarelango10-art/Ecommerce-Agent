from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.core.config import Settings, load_settings
from backend.app.db.base import Base
from backend.app.db.models import AuthRateLimit, User
from backend.app.main import create_app
from backend.app.security.passwords import hash_password
from backend.app.security.rate_limiter import (
    check_and_record_auth_rate_limit,
    compute_rate_limit_hash,
)


# ---------------------------------------------------------------------------
# 1. CORS Hardening Tests
# ---------------------------------------------------------------------------

def test_cors_preflight_and_allowed_origin(api_client):
    client, _ = api_client

    # 1. Preflight OPTIONS request from allowed origin
    preflight = client.options(
        "/api/v1/auth/login",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Content-Type,Authorization",
        },
    )
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert preflight.headers["access-control-allow-credentials"] == "true"
    assert "POST" in preflight.headers["access-control-allow-methods"]

    # 2. Simple GET request with allowed origin
    res = client.get("/health", headers={"Origin": "http://localhost:5173"})
    assert res.status_code == 200
    assert res.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert res.headers["access-control-allow-credentials"] == "true"


def test_cors_disallowed_origin_rejected(api_client):
    client, _ = api_client

    # Request from unauthorized origin
    res = client.get("/health", headers={"Origin": "http://malicious-site.example.com"})
    assert res.status_code == 200
    assert "access-control-allow-origin" not in res.headers


def test_cors_no_wildcard_with_credentials(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///:memory:")
    monkeypatch.setenv("AUTH_SECRET_KEY", "01234567890123456789012345678901")

    # Setting ALLOWED_ORIGINS to wildcard '*' must be rejected
    monkeypatch.setenv("ALLOWED_ORIGINS", "*")
    with pytest.raises(ValidationError, match=r"Wildcard origin '\*' is not allowed"):
        Settings()


def test_cors_comma_separated_origins_parsed(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///:memory:")
    monkeypatch.setenv("AUTH_SECRET_KEY", "01234567890123456789012345678901")
    monkeypatch.setenv(
        "ALLOWED_ORIGINS",
        "https://app.example.com, https://admin.example.com",
    )

    settings = Settings()
    assert settings.allowed_origins == [
        "https://app.example.com",
        "https://admin.example.com",
    ]


# ---------------------------------------------------------------------------
# 2. Security Headers Tests
# ---------------------------------------------------------------------------

def test_security_headers_present_on_all_responses(api_client):
    client, _ = api_client

    res = client.get("/health")
    assert res.status_code == 200
    assert res.headers["x-content-type-options"] == "nosniff"
    assert res.headers["x-frame-options"] == "DENY"
    assert res.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert res.headers["x-xss-protection"] == "1; mode=block"
    assert "Content-Security-Policy" in res.headers
    csp = res.headers["content-security-policy"]
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "object-src 'none'" in csp


def test_hsts_header_in_production_or_https(api_client):
    client, _ = api_client

    # Normal dev HTTP connection has no HSTS
    res = client.get("/health")
    assert "strict-transport-security" not in res.headers

    # HTTPS connection receives HSTS
    res_https = client.get("/health", headers={"x-forwarded-proto": "https"})
    assert "strict-transport-security" in res_https.headers
    assert "max-age=31536000" in res_https.headers["strict-transport-security"]


# ---------------------------------------------------------------------------
# 3. Authentication Rate Limiting Tests
# ---------------------------------------------------------------------------

def test_login_rate_limiting_enforcement(api_client):
    client, _ = api_client

    # Configure custom low limit to test quickly
    client.app.state.settings.auth_rate_limit_login_per_minute = 3
    client.app.state.settings.auth_rate_limit_window_seconds = 60

    payload = {"email": "user@example.com", "password": "wrong-password-123"}

    # First 3 attempts are allowed (receive 401 for wrong credentials)
    for _ in range(3):
        res = client.post("/api/v1/auth/login", json=payload)
        assert res.status_code == 401

    # 4th attempt must be throttled with 429
    throttled = client.post("/api/v1/auth/login", json=payload)
    assert throttled.status_code == 429
    assert "Retry-After" in throttled.headers
    retry_after = int(throttled.headers["Retry-After"])
    assert retry_after > 0
    assert throttled.json()["detail"] == "Too many authentication attempts. Please try again later."


def test_register_rate_limiting_enforcement(api_client):
    client, _ = api_client

    client.app.state.settings.auth_rate_limit_register_per_minute = 2
    client.app.state.settings.auth_rate_limit_window_seconds = 60

    payload = {
        "email": "newuser@example.com",
        "password": "validPassword123!",
        "display_name": "New User",
    }

    # First attempt: register succeeds (201)
    res1 = client.post("/api/v1/auth/register", json=payload)
    assert res1.status_code == 201

    # Second attempt: duplicate email conflict (409), but counts towards attempt limit
    res2 = client.post("/api/v1/auth/register", json=payload)
    assert res2.status_code == 409

    # Third attempt exceeds limit of 2: must return 429
    res3 = client.post("/api/v1/auth/register", json=payload)
    assert res3.status_code == 429
    assert "Retry-After" in res3.headers


def test_rate_limiting_prevents_account_enumeration(api_client):
    client, _ = api_client

    client.app.state.settings.auth_rate_limit_login_per_minute = 2

    # Targeting non-existent accounts repeatedly still triggers 429
    for _ in range(2):
        res = client.post(
            "/api/v1/auth/login",
            json={"email": "nonexistent@example.com", "password": "password123456"},
        )
        assert res.status_code == 401

    # 3rd request is blocked identically
    throttled = client.post(
        "/api/v1/auth/login",
        json={"email": "nonexistent@example.com", "password": "password123456"},
    )
    assert throttled.status_code == 429


def test_upstream_waf_rate_limit_header_enforced(api_client):
    client, _ = api_client

    # Upstream WAF flagged rate limit
    res = client.post(
        "/api/v1/auth/login",
        json={"email": "any@example.com", "password": "password123456"},
        headers={"X-WAF-RateLimited": "true", "Retry-After": "45"},
    )
    assert res.status_code == 429
    assert res.headers["Retry-After"] == "45"
    assert "upstream rate limiter" in res.json()["detail"].lower()


def test_multi_worker_distributed_rate_limiting(api_client):
    client, _ = api_client

    # Verify that two independent sessions on the same database share rate limit state
    engine = client.app.state.db_engine
    SessionLocal = sessionmaker(bind=engine)

    # Simulate Worker 1 and Worker 2
    session1 = SessionLocal()
    session2 = SessionLocal()

    try:
        from starlette.requests import Request

        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/auth/login",
            "headers": [(b"cf-connecting-ip", b"203.0.113.195")],
            "client": ("203.0.113.195", 12345),
        }
        req = Request(scope)

        settings = client.app.state.settings
        settings.auth_rate_limit_login_per_minute = 2

        # Worker 1 records attempt 1
        check_and_record_auth_rate_limit(
            session=session1,
            request=req,
            action="login",
            identifier="shared@example.com",
            settings=settings,
        )

        # Worker 2 records attempt 2
        check_and_record_auth_rate_limit(
            session=session2,
            request=req,
            action="login",
            identifier="shared@example.com",
            settings=settings,
        )

        # Worker 1 attempt 3 must be blocked because Worker 2's attempt was committed to the DB
        with pytest.raises(Exception) as exc_info:
            check_and_record_auth_rate_limit(
                session=session1,
                request=req,
                action="login",
                identifier="shared@example.com",
                settings=settings,
            )
        assert exc_info.value.status_code == 429
        assert "Retry-After" in exc_info.value.headers
    finally:
        session1.close()
        session2.close()
