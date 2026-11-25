from __future__ import annotations

import logging
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from google.genai import errors
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.config import Settings
from backend.app.db.base import Base
from backend.app.db.models import User, UserGeminiSettings
from backend.app.main import create_app
from backend.app.schemas.gemini_settings import (
    PersonalGeminiKeyResponse,
    SavePersonalGeminiKeyRequest,
    TogglePersonalGeminiKeyRequest,
    ValidatePersonalGeminiKeyRequest,
    ValidatePersonalGeminiKeyResponse,
)
from backend.app.security.key_encryption import (
    KeyEncryptionError,
    encrypt_user_key,
    mask_user_key,
)
from backend.app.security.tokens import create_access_token
from backend.app.services.gemini_key_service import (
    GeminiKeyServiceError,
    decrypt_personal_key,
    delete_personal_key,
    get_personal_key_status,
    save_personal_key,
    toggle_personal_key,
    validate_gemini_api_key,
)

VALID_SECRET = SecretStr("test-encryption-secret-32-bytes-long-123456789")


class FakeGenaiModels:
    def __init__(self, outcome: object = "pong") -> None:
        self.outcome = outcome
        self.calls: list[dict[str, object]] = []

    def generate_content(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        if hasattr(self.outcome, "text"):
            return self.outcome
        return SimpleNamespace(text=str(self.outcome), function_calls=[])


class FakeGenaiClient:
    def __init__(self, outcome: object = "pong") -> None:
        self.models = FakeGenaiModels(outcome)
        self.closed = False

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def test_setup(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-platform-key-not-sent")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.8-flash")
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///:memory:")
    monkeypatch.setenv("AUTH_SECRET_KEY", "test-auth-secret-key-0123456789abcdef")
    monkeypatch.setenv("USER_KEY_ENCRYPTION_SECRET", VALID_SECRET.get_secret_value())
    monkeypatch.setenv("OUTBOX_WORKER_ENABLED", "false")

    fake_client = FakeGenaiClient("pong")

    def client_factory(*args, **kwargs):
        return fake_client

    app = create_app()
    app.state.gemini_client_factory = client_factory

    with TestClient(app) as client:
        Base.metadata.create_all(client.app.state.db_engine)
        yield client, fake_client


def _create_user(client: TestClient, email: str = "customer@example.com") -> tuple[User, str]:
    session_factory = client.app.state.db_session_factory
    settings: Settings = client.app.state.settings
    with session_factory() as session:
        user = User(
            id=uuid4(),
            email=email,
            password_hash="mock-argon2-hash",
            is_active=True,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        token = create_access_token(user.id, settings)
        return user, token


# ---------------------------------------------------------------------------
# 1. Authenticated user can save a valid key
# ---------------------------------------------------------------------------
def test_authenticated_user_can_save_valid_key(test_setup):
    client, fake_client = test_setup
    user, token = _create_user(client)
    headers = {"Authorization": f"Bearer {token}"}

    payload = {"api_key": "test-only-fake-gemini-key-material-cdef"}
    response = client.post("/api/v1/customers/me/gemini-key", json=payload, headers=headers)

    assert response.status_code == 200
    data = response.json()
    assert data["has_key"] is True
    assert data["is_enabled"] is True
    assert data["key_hint"] == "...cdef"
    assert data["status"] == "ACTIVE"
    assert data["last_validated_at"] is not None

    # Verify database persistence
    with client.app.state.db_session_factory() as session:
        stored = session.scalar(select(UserGeminiSettings).where(UserGeminiSettings.user_id == user.id))
        assert stored is not None
        assert stored.encrypted_api_key.startswith("v1:")
        assert stored.key_hint == "...cdef"
        assert stored.is_enabled is True
        assert stored.status == "ACTIVE"


# ---------------------------------------------------------------------------
# 2. Invalid key is not saved
# ---------------------------------------------------------------------------
def test_invalid_key_is_not_saved(test_setup):
    client, fake_client = test_setup
    user, token = _create_user(client)
    headers = {"Authorization": f"Bearer {token}"}

    # Simulate Google 400 API_KEY_INVALID error
    fake_client.models.outcome = errors.APIError(
        400,
        "API_KEY_INVALID: The provided API key is expired or invalid",
    )

    payload = {"api_key": "test-only-fake-gemini-key-material-invalid"}
    response = client.post("/api/v1/customers/me/gemini-key", json=payload, headers=headers)

    assert response.status_code == 400
    assert "invalid or revoked" in response.json()["detail"].lower()

    # Verify no row was inserted into user_gemini_settings
    with client.app.state.db_session_factory() as session:
        stored = session.scalar(select(UserGeminiSettings).where(UserGeminiSettings.user_id == user.id))
        assert stored is None


# ---------------------------------------------------------------------------
# 3. Quota error does not mark the key invalid
# ---------------------------------------------------------------------------
def test_quota_error_does_not_mark_the_key_invalid(test_setup):
    client, fake_client = test_setup
    user, token = _create_user(client)
    headers = {"Authorization": f"Bearer {token}"}

    # Simulate 429 Quota exhaustion
    fake_client.models.outcome = errors.APIError(
        429,
        "RESOURCE_EXHAUSTED: Daily quota exceeded for project",
    )

    # 1. Validation-only endpoint
    val_res = client.post(
        "/api/v1/customers/me/gemini-key/validate",
        json={"api_key": "test-only-fake-gemini-key-material-quota"},
        headers=headers,
    )
    assert val_res.status_code == 200
    val_data = val_res.json()
    assert val_data["is_valid"] is False
    assert val_data["status"] == "QUOTA_EXHAUSTED"
    assert "quota" in val_data["message"].lower()
    assert val_data["status"] != "INVALID_KEY"

    # 2. Save endpoint returns 429 and does NOT save
    save_res = client.post(
        "/api/v1/customers/me/gemini-key",
        json={"api_key": "test-only-fake-gemini-key-material-quota"},
        headers=headers,
    )
    assert save_res.status_code == 429
    assert "quota" in save_res.json()["detail"].lower()

    with client.app.state.db_session_factory() as session:
        stored = session.scalar(select(UserGeminiSettings).where(UserGeminiSettings.user_id == user.id))
        assert stored is None


# ---------------------------------------------------------------------------
# 4. Temporary provider outage is handled safely
# ---------------------------------------------------------------------------
def test_temporary_provider_outage_is_handled_safely(test_setup):
    client, fake_client = test_setup
    user, token = _create_user(client)
    headers = {"Authorization": f"Bearer {token}"}

    # Simulate 503 Service Unavailable
    fake_client.models.outcome = errors.APIError(
        503,
        "The model is currently overloaded. Please retry later.",
    )

    payload = {"api_key": "test-only-fake-gemini-key-material-overload"}
    response = client.post("/api/v1/customers/me/gemini-key", json=payload, headers=headers)

    assert response.status_code == 503
    assert "temporarily unavailable" in response.json()["detail"].lower()

    with client.app.state.db_session_factory() as session:
        stored = session.scalar(select(UserGeminiSettings).where(UserGeminiSettings.user_id == user.id))
        assert stored is None


# ---------------------------------------------------------------------------
# 5. Existing key remains unchanged if replacement validation fails
# ---------------------------------------------------------------------------
def test_existing_key_remains_unchanged_if_replacement_validation_fails(test_setup):
    client, fake_client = test_setup
    user, token = _create_user(client)
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Successfully save original key
    fake_client.models.outcome = "pong"
    client.post(
        "/api/v1/customers/me/gemini-key",
        json={"api_key": "test-only-fake-gemini-key-material-1111"},
        headers=headers,
    )

    with client.app.state.db_session_factory() as session:
        original = session.scalar(select(UserGeminiSettings).where(UserGeminiSettings.user_id == user.id))
        assert original is not None
        assert original.key_hint == "...1111"
        orig_ct = original.encrypted_api_key

    # 2. Attempt to replace with a broken key
    fake_client.models.outcome = errors.APIError(
        400,
        "API_KEY_INVALID",
    )
    replace_res = client.post(
        "/api/v1/customers/me/gemini-key",
        json={"api_key": "test-only-fake-gemini-key-material-broken"},
        headers=headers,
    )
    assert replace_res.status_code == 400

    # 3. Verify original key in database is 100% preserved
    with client.app.state.db_session_factory() as session:
        preserved = session.scalar(select(UserGeminiSettings).where(UserGeminiSettings.user_id == user.id))
        assert preserved is not None
        assert preserved.key_hint == "...1111"
        assert preserved.encrypted_api_key == orig_ct
        assert preserved.status == "ACTIVE"


# ---------------------------------------------------------------------------
# 6. GET returns masked key only
# ---------------------------------------------------------------------------
def test_get_returns_masked_key_only(test_setup):
    client, fake_client = test_setup
    user, token = _create_user(client)
    headers = {"Authorization": f"Bearer {token}"}

    full_secret_key = "test-only-fake-gemini-key-material-3210"
    client.post(
        "/api/v1/customers/me/gemini-key",
        json={"api_key": full_secret_key},
        headers=headers,
    )

    get_res = client.get("/api/v1/customers/me/gemini-key", headers=headers)
    assert get_res.status_code == 200
    data = get_res.json()
    assert data["has_key"] is True
    assert data["key_hint"] == "...3210"
    assert full_secret_key not in get_res.text


# ---------------------------------------------------------------------------
# 7. API never returns ciphertext
# ---------------------------------------------------------------------------
def test_api_never_returns_ciphertext(test_setup):
    client, fake_client = test_setup
    user, token = _create_user(client)
    headers = {"Authorization": f"Bearer {token}"}

    post_res = client.post(
        "/api/v1/customers/me/gemini-key",
        json={"api_key": "test-only-fake-gemini-key-material-ciphercheck"},
        headers=headers,
    )
    assert post_res.status_code == 200
    assert "v1:" not in post_res.text
    assert "encrypted_api_key" not in post_res.json()

    get_res = client.get("/api/v1/customers/me/gemini-key", headers=headers)
    assert "v1:" not in get_res.text
    assert "encrypted_api_key" not in get_res.json()

    patch_res = client.patch(
        "/api/v1/customers/me/gemini-key",
        json={"is_enabled": False},
        headers=headers,
    )
    assert "v1:" not in patch_res.text
    assert "encrypted_api_key" not in patch_res.json()


# ---------------------------------------------------------------------------
# 8. Enable and disable endpoints work
# ---------------------------------------------------------------------------
def test_enable_and_disable_endpoints_work(test_setup):
    client, fake_client = test_setup
    user, token = _create_user(client)
    headers = {"Authorization": f"Bearer {token}"}

    # Save initial key (defaults to enabled)
    client.post(
        "/api/v1/customers/me/gemini-key",
        json={"api_key": "test-only-fake-gemini-key-material-toggle"},
        headers=headers,
    )

    # Disable
    res_disable = client.patch(
        "/api/v1/customers/me/gemini-key",
        json={"is_enabled": False},
        headers=headers,
    )
    assert res_disable.status_code == 200
    assert res_disable.json()["is_enabled"] is False

    # Confirm via GET
    res_get1 = client.get("/api/v1/customers/me/gemini-key", headers=headers)
    assert res_get1.json()["is_enabled"] is False

    # Re-enable
    res_enable = client.patch(
        "/api/v1/customers/me/gemini-key",
        json={"is_enabled": True},
        headers=headers,
    )
    assert res_enable.status_code == 200
    assert res_enable.json()["is_enabled"] is True

    # Confirm via GET
    res_get2 = client.get("/api/v1/customers/me/gemini-key", headers=headers)
    assert res_get2.json()["is_enabled"] is True


# ---------------------------------------------------------------------------
# 9. Delete removes the key
# ---------------------------------------------------------------------------
def test_delete_removes_the_key(test_setup):
    client, fake_client = test_setup
    user, token = _create_user(client)
    headers = {"Authorization": f"Bearer {token}"}

    client.post(
        "/api/v1/customers/me/gemini-key",
        json={"api_key": "test-only-fake-gemini-key-material-delete"},
        headers=headers,
    )

    # Delete
    del_res = client.delete("/api/v1/customers/me/gemini-key", headers=headers)
    assert del_res.status_code == 204

    # Subsequent GET returns has_key=False
    get_res = client.get("/api/v1/customers/me/gemini-key", headers=headers)
    assert get_res.status_code == 200
    assert get_res.json()["has_key"] is False
    assert get_res.json()["key_hint"] is None

    # Subsequent DELETE returns 404
    del_again = client.delete("/api/v1/customers/me/gemini-key", headers=headers)
    assert del_again.status_code == 404


# ---------------------------------------------------------------------------
# 10. User A cannot access User B's key
# ---------------------------------------------------------------------------
def test_user_a_cannot_access_user_b_key(test_setup):
    client, fake_client = test_setup
    user_a, token_a = _create_user(client, email="usera@example.com")
    user_b, token_b = _create_user(client, email="userb@example.com")

    # User B saves a key
    client.post(
        "/api/v1/customers/me/gemini-key",
        json={"api_key": "test-only-fake-gemini-key-material-BBBB"},
        headers={"Authorization": f"Bearer {token_b}"},
    )

    # User A requests their own key status -> must see has_key=False
    res_a = client.get(
        "/api/v1/customers/me/gemini-key",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert res_a.status_code == 200
    assert res_a.json()["has_key"] is False
    assert res_a.json()["key_hint"] is None

    # User B requests their key status -> sees their own key
    res_b = client.get(
        "/api/v1/customers/me/gemini-key",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert res_b.status_code == 200
    assert res_b.json()["has_key"] is True
    assert res_b.json()["key_hint"] == "...BBBB"


# ---------------------------------------------------------------------------
# 11. User A cannot modify User B's key
# ---------------------------------------------------------------------------
def test_user_a_cannot_modify_user_b_key(test_setup):
    client, fake_client = test_setup
    user_a, token_a = _create_user(client, email="attacker@example.com")
    user_b, token_b = _create_user(client, email="victim@example.com")

    # User B saves a key
    client.post(
        "/api/v1/customers/me/gemini-key",
        json={"api_key": "test-only-fake-gemini-key-material-7890"},
        headers={"Authorization": f"Bearer {token_b}"},
    )

    # User A tries to delete -> 404 because User A has no key
    del_res = client.delete(
        "/api/v1/customers/me/gemini-key",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert del_res.status_code == 404

    # User A tries to toggle -> 404
    patch_res = client.patch(
        "/api/v1/customers/me/gemini-key",
        json={"is_enabled": False},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert patch_res.status_code == 404

    # Verify User B's key remains completely untouched
    with client.app.state.db_session_factory() as session:
        stored_b = session.scalar(select(UserGeminiSettings).where(UserGeminiSettings.user_id == user_b.id))
        assert stored_b is not None
        assert stored_b.is_enabled is True
        assert stored_b.key_hint == "...7890"


# ---------------------------------------------------------------------------
# 12. Unauthenticated access is rejected
# ---------------------------------------------------------------------------
def test_unauthenticated_access_is_rejected(test_setup):
    client, fake_client = test_setup

    assert client.get("/api/v1/customers/me/gemini-key").status_code == 401
    assert client.post("/api/v1/customers/me/gemini-key", json={"api_key": "test-only-fake-gemini-key-material-unauth1"}).status_code == 401
    assert client.patch("/api/v1/customers/me/gemini-key", json={"is_enabled": False}).status_code == 401
    assert client.delete("/api/v1/customers/me/gemini-key").status_code == 401
    assert client.post("/api/v1/customers/me/gemini-key/validate", json={"api_key": "test-only-fake-gemini-key-material-unauth2"}).status_code == 401


# ---------------------------------------------------------------------------
# 13. Corrupted stored ciphertext is handled safely
# ---------------------------------------------------------------------------
def test_corrupted_stored_ciphertext_is_handled_safely(test_setup):
    client, fake_client = test_setup
    user, token = _create_user(client)
    headers = {"Authorization": f"Bearer {token}"}

    # Insert a record with intentionally corrupted ciphertext
    with client.app.state.db_session_factory() as session:
        corrupted_row = UserGeminiSettings(
            user_id=user.id,
            encrypted_api_key="v1:totally-corrupted-ciphertext-token",
            key_hint="...9999",
            key_version=1,
            is_enabled=True,
            status="ACTIVE",
        )
        session.add(corrupted_row)
        session.commit()

    # 1. GET still succeeds because it never decrypts
    get_res = client.get("/api/v1/customers/me/gemini-key", headers=headers)
    assert get_res.status_code == 200
    assert get_res.json()["has_key"] is True
    assert get_res.json()["key_hint"] == "...9999"

    # 2. Internal decrypt helper marks status as CORRUPTED and safely raises
    with client.app.state.db_session_factory() as session:
        stored = session.scalar(select(UserGeminiSettings).where(UserGeminiSettings.user_id == user.id))
        with pytest.raises(GeminiKeyServiceError, match="corrupted or unreadable"):
            decrypt_personal_key(session, stored, client.app.state.settings)

        # Confirm status updated to CORRUPTED
        assert stored.status == "CORRUPTED"


# ---------------------------------------------------------------------------
# 14. Rate limiting protects validation requests
# ---------------------------------------------------------------------------
def test_rate_limiting_protects_validation_requests(test_setup):
    client, fake_client = test_setup
    user, token = _create_user(client)
    headers = {"Authorization": f"Bearer {token}"}

    # Set strict rate limit: 2 validation attempts per minute
    client.app.state.settings.gemini_key_validation_rate_limit_per_minute = 2
    client.app.state.settings.auth_rate_limit_window_seconds = 60

    payload = {"api_key": "test-only-fake-gemini-key-material-ratelimit"}

    # Attempt 1 -> allowed
    res1 = client.post("/api/v1/customers/me/gemini-key/validate", json=payload, headers=headers)
    assert res1.status_code == 200

    # Attempt 2 -> allowed
    res2 = client.post("/api/v1/customers/me/gemini-key/validate", json=payload, headers=headers)
    assert res2.status_code == 200

    # Attempt 3 -> Rate limit exceeded (429)
    res3 = client.post("/api/v1/customers/me/gemini-key/validate", json=payload, headers=headers)
    assert res3.status_code == 429
    assert "too many" in res3.json()["detail"].lower()
    assert "Retry-After" in res3.headers

    # Upstream WAF header enforcement
    waf_headers = {**headers, "X-WAF-RateLimited": "true"}
    res_waf = client.post("/api/v1/customers/me/gemini-key/validate", json=payload, headers=waf_headers)
    assert res_waf.status_code == 429
    assert "upstream" in res_waf.json()["detail"].lower()


# ---------------------------------------------------------------------------
# 15. Exception messages and logs never disclose plaintext keys
# ---------------------------------------------------------------------------
def test_exception_messages_and_logs_never_disclose_plaintext_keys(test_setup, caplog):
    client, fake_client = test_setup
    user, token = _create_user(client)
    headers = {"Authorization": f"Bearer {token}"}

    secret_key_material = "TOP_SECRET_PLAINTEXT_API_KEY_NEVER_LOGGED_99999"
    fake_client.models.outcome = errors.APIError(
        400,
        f"Detailed error mentioning raw request with {secret_key_material}",
    )

    with caplog.at_level(logging.DEBUG):
        response = client.post(
            "/api/v1/customers/me/gemini-key",
            json={"api_key": secret_key_material},
            headers=headers,
        )

    assert response.status_code == 400
    assert secret_key_material not in response.text
    assert secret_key_material not in caplog.text
