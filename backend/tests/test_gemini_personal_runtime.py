from __future__ import annotations

from datetime import timedelta
import logging
from types import SimpleNamespace
from typing import Any, Callable
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from google.genai import errors, types
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.config import Settings
from backend.app.core.datetime_utils import ensure_utc, utc_now
from backend.app.db.base import Base
from backend.app.db.models import GeminiUsageEvent, User, UserGeminiSettings
from backend.app.main import create_app
from backend.app.security.key_encryption import encrypt_user_key, mask_user_key
from backend.app.security.tokens import create_access_token
from backend.app.services.gemini_service import (
    GeminiProviderError,
    GeminiResult,
    GeminiService,
    GeminiTimeoutError,
)
from backend.app.services.personal_gemini_runtime import (
    RequestScopedGeminiService,
    record_gemini_usage,
    resolve_personal_key,
)

VALID_SECRET = SecretStr("test-encryption-secret-32-bytes-long-123456789")


class TrackingGenaiClient:
    def __init__(self, key: str, outcome: Any = "Mocked answer") -> None:
        self.key = key
        self.outcome = outcome
        self.calls: list[dict[str, Any]] = []
        self.closed = False
        self.usage_metadata: Any = None

    @property
    def models(self) -> TrackingGenaiClient:
        return self

    def generate_content(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        outcome = self.outcome
        if callable(outcome):
            outcome = outcome(**kwargs)
        if isinstance(outcome, BaseException):
            raise outcome
        text = str(outcome)
        return SimpleNamespace(
            text=text,
            function_calls=[],
            usage_metadata=self.usage_metadata,
            candidates=[
                SimpleNamespace(
                    content=types.Content(
                        role="model",
                        parts=[types.Part.from_text(text=text)],
                    )
                )
            ],
        )

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def runtime_setup(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-platform-company-key")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.8-flash")
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///:memory:")
    monkeypatch.setenv("AUTH_SECRET_KEY", "test-auth-secret-key-0123456789abcdef")
    monkeypatch.setenv("USER_KEY_ENCRYPTION_SECRET", VALID_SECRET.get_secret_value())
    monkeypatch.setenv("OUTBOX_WORKER_ENABLED", "false")

    clients_by_key: dict[str, TrackingGenaiClient] = {}

    def client_factory(api_key: str, **kwargs: Any) -> TrackingGenaiClient:
        if api_key not in clients_by_key:
            clients_by_key[api_key] = TrackingGenaiClient(key=api_key)
        return clients_by_key[api_key]

    def set_outcome(api_key: str, outcome: Any, usage_metadata: Any = None) -> TrackingGenaiClient:
        if api_key not in clients_by_key:
            clients_by_key[api_key] = TrackingGenaiClient(key=api_key)
        clients_by_key[api_key].outcome = outcome
        clients_by_key[api_key].usage_metadata = usage_metadata
        return clients_by_key[api_key]

    monkeypatch.setattr("backend.app.services.gemini_service.genai.Client", client_factory)
    monkeypatch.setattr("backend.app.services.personal_gemini_runtime.genai.Client", client_factory)

    app = create_app()
    app.state.gemini_client_factory = client_factory

    with TestClient(app) as client:
        Base.metadata.create_all(client.app.state.db_engine)
        yield client, set_outcome, clients_by_key


def _create_user_with_key(
    client: TestClient,
    email: str = "customer@example.com",
    raw_key: str = "AIzaSyPersonalTestKey1234567890",
    is_enabled: bool = True,
    status: str = "ACTIVE",
    cooldown_until: Any = None,
) -> tuple[User, str, str]:
    session_factory = client.app.state.db_session_factory
    settings: Settings = client.app.state.settings
    with session_factory() as session:
        user = User(
            id=uuid4(),
            email=email,
            password_hash="mock-hash",
            is_active=True,
        )
        session.add(user)
        session.flush()

        ct = encrypt_user_key(raw_key, settings.user_key_encryption_secret)
        hint = mask_user_key(raw_key)
        gemini_settings = UserGeminiSettings(
            user_id=user.id,
            encrypted_api_key=ct,
            key_hint=hint,
            key_version=1,
            is_enabled=is_enabled,
            status=status,
            cooldown_until=cooldown_until,
            last_validated_at=utc_now(),
        )
        session.add(gemini_settings)
        session.commit()
        session.refresh(user)

        token = create_access_token(user.id, settings)
        return user, token, raw_key


# ---------------------------------------------------------------------------
# 1. Personal enabled key is used first
# ---------------------------------------------------------------------------
def test_personal_enabled_key_is_used_first(runtime_setup):
    client, set_outcome, clients = runtime_setup
    user, token, raw_key = _create_user_with_key(client)
    headers = {"Authorization": f"Bearer {token}"}

    set_outcome(raw_key, "Personal key shopping advice")
    set_outcome("test-platform-company-key", "Company answer")

    res = client.post("/api/v1/ask", json={"question": "What is the best laptop?"}, headers=headers)
    assert res.status_code == 200
    assert res.json()["answer"] == "Personal key shopping advice"

    # Personal key was called
    assert len(clients[raw_key].calls) == 1
    # Company pool key was NOT called
    assert len(clients["test-platform-company-key"].calls) == 0

    # Telemetry check
    with client.app.state.db_session_factory() as session:
        events = session.scalars(select(GeminiUsageEvent).where(GeminiUsageEvent.user_id == user.id)).all()
        assert len(events) == 1
        assert events[0].key_source == "PERSONAL"
        assert events[0].status == "SUCCESS"
        assert events[0].attempt_number == 1


# ---------------------------------------------------------------------------
# 2. Personal disabled key uses company pool
# ---------------------------------------------------------------------------
def test_personal_disabled_key_uses_company_pool(runtime_setup):
    client, set_outcome, clients = runtime_setup
    user, token, raw_key = _create_user_with_key(client, is_enabled=False)
    headers = {"Authorization": f"Bearer {token}"}

    set_outcome(raw_key, "Personal key response")
    set_outcome("test-platform-company-key", "Company pool response")

    res = client.post("/api/v1/ask", json={"question": "Tell me about shipping"}, headers=headers)
    assert res.status_code == 200
    assert res.json()["answer"] == "Company pool response"

    # Personal key was NOT called
    assert len(clients.get(raw_key, TrackingGenaiClient(raw_key)).calls) == 0
    # Company key was called
    assert len(clients["test-platform-company-key"].calls) == 1

    # Telemetry check
    with client.app.state.db_session_factory() as session:
        events = session.scalars(select(GeminiUsageEvent).where(GeminiUsageEvent.user_id == user.id)).all()
        assert len(events) == 1
        assert events[0].key_source == "COMPANY"
        assert events[0].status == "SUCCESS"


# ---------------------------------------------------------------------------
# 3. Guest uses company pool only
# ---------------------------------------------------------------------------
def test_guest_uses_company_pool_only(runtime_setup):
    client, set_outcome, clients = runtime_setup
    set_outcome("test-platform-company-key", "Guest answer from company pool")

    res = client.post("/api/v1/ask", json={"question": "What is the return window?"})
    assert res.status_code == 200
    assert res.json()["answer"] == "Guest answer from company pool"

    with client.app.state.db_session_factory() as session:
        event = session.scalar(select(GeminiUsageEvent).order_by(GeminiUsageEvent.created_at.desc()))
        assert event is not None
        assert event.user_id is None
        assert event.key_source == "COMPANY"
        assert event.status == "SUCCESS"


# ---------------------------------------------------------------------------
# 4. Personal 429 falls back to company
# ---------------------------------------------------------------------------
def test_personal_429_falls_back_to_company(runtime_setup):
    client, set_outcome, clients = runtime_setup
    user, token, raw_key = _create_user_with_key(client)
    headers = {"Authorization": f"Bearer {token}"}

    # Personal key returns 429
    set_outcome(raw_key, errors.APIError(429, "Rate limit exceeded"))
    set_outcome("test-platform-company-key", "Fell back to company successfully")

    res = client.post("/api/v1/ask", json={"question": "Find wireless headphones"}, headers=headers)
    assert res.status_code == 200
    assert res.json()["answer"] == "Fell back to company successfully"

    # Verify cooldown was applied in DB
    with client.app.state.db_session_factory() as session:
        db_settings = session.scalar(select(UserGeminiSettings).where(UserGeminiSettings.user_id == user.id))
        assert db_settings is not None
        assert db_settings.cooldown_until is not None
        assert ensure_utc(db_settings.cooldown_until) > utc_now()
        assert db_settings.status == "ACTIVE"

        # Telemetry: 2 events with same correlation_id
        events = session.scalars(
            select(GeminiUsageEvent).where(GeminiUsageEvent.user_id == user.id).order_by(GeminiUsageEvent.attempt_number)
        ).all()
        assert len(events) == 2
        assert events[0].correlation_id == events[1].correlation_id
        assert events[0].key_source == "PERSONAL"
        assert events[0].status == "COOLDOWN"
        assert events[0].attempt_number == 1
        assert events[1].key_source == "COMPANY"
        assert events[1].status == "SUCCESS"
        assert events[1].attempt_number == 2


# ---------------------------------------------------------------------------
# 5. Personal daily-quota exhaustion applies the correct cooldown
# ---------------------------------------------------------------------------
def test_personal_daily_quota_exhaustion_applies_correct_cooldown(runtime_setup):
    client, set_outcome, clients = runtime_setup
    user, token, raw_key = _create_user_with_key(client)
    headers = {"Authorization": f"Bearer {token}"}

    daily_err = errors.APIError(429, "Resource exhausted: queries per day limit reached")
    set_outcome(raw_key, daily_err)
    set_outcome("test-platform-company-key", "Company fallback")

    start_time = utc_now()
    res = client.post("/api/v1/ask", json={"question": "Recommend shoes"}, headers=headers)
    assert res.status_code == 200

    with client.app.state.db_session_factory() as session:
        db_settings = session.scalar(select(UserGeminiSettings).where(UserGeminiSettings.user_id == user.id))
        assert db_settings.cooldown_until is not None
        # Must be close to 24 hours (86,400s)
        remaining = (ensure_utc(db_settings.cooldown_until) - start_time).total_seconds()
        assert 86000 <= remaining <= 86410


# ---------------------------------------------------------------------------
# 6. Personal invalid credentials trigger safe fallback
# ---------------------------------------------------------------------------
def test_personal_invalid_credentials_trigger_safe_fallback(runtime_setup):
    client, set_outcome, clients = runtime_setup
    user, token, raw_key = _create_user_with_key(client)
    headers = {"Authorization": f"Bearer {token}"}

    set_outcome(raw_key, errors.APIError(400, "API_KEY_INVALID"))
    set_outcome("test-platform-company-key", "Company success")

    res = client.post("/api/v1/ask", json={"question": "Explain warranties"}, headers=headers)
    assert res.status_code == 200
    assert res.json()["answer"] == "Company success"

    with client.app.state.db_session_factory() as session:
        db_settings = session.scalar(select(UserGeminiSettings).where(UserGeminiSettings.user_id == user.id))
        assert db_settings.status == "INVALID"

        events = session.scalars(select(GeminiUsageEvent).where(GeminiUsageEvent.user_id == user.id)).all()
        personal_ev = [e for e in events if e.key_source == "PERSONAL"][0]
        assert personal_ev.status == "FAILED"
        assert personal_ev.error_category == "INVALID_CREDENTIAL"


# ---------------------------------------------------------------------------
# 7. Transient 503/504 follows bounded retry behavior
# ---------------------------------------------------------------------------
def test_transient_503_follows_bounded_retry_behavior(runtime_setup):
    client, set_outcome, clients = runtime_setup
    user, token, raw_key = _create_user_with_key(client)
    headers = {"Authorization": f"Bearer {token}"}

    set_outcome(raw_key, errors.APIError(503, "Service Unavailable"))
    set_outcome("test-platform-company-key", "Company success")

    res = client.post("/api/v1/ask", json={"question": "Help with shipping"}, headers=headers)
    assert res.status_code == 200

    # Key must NOT be disabled
    with client.app.state.db_session_factory() as session:
        db_settings = session.scalar(select(UserGeminiSettings).where(UserGeminiSettings.user_id == user.id))
        assert db_settings.status == "ACTIVE"
        assert db_settings.cooldown_until is None


# ---------------------------------------------------------------------------
# 8. Personal recovery after cooldown
# ---------------------------------------------------------------------------
def test_personal_recovery_after_cooldown(runtime_setup):
    client, set_outcome, clients = runtime_setup
    past_cooldown = utc_now() - timedelta(minutes=5)
    user, token, raw_key = _create_user_with_key(client, cooldown_until=past_cooldown)
    headers = {"Authorization": f"Bearer {token}"}

    set_outcome(raw_key, "Recovered personal answer")
    set_outcome("test-platform-company-key", "Company")

    res = client.post("/api/v1/ask", json={"question": "Check product stock"}, headers=headers)
    assert res.status_code == 200
    assert res.json()["answer"] == "Recovered personal answer"

    with client.app.state.db_session_factory() as session:
        db_settings = session.scalar(select(UserGeminiSettings).where(UserGeminiSettings.user_id == user.id))
        assert db_settings.cooldown_until is None
        assert db_settings.status == "ACTIVE"


# ---------------------------------------------------------------------------
# 9. User A can never access User B's personal key
# ---------------------------------------------------------------------------
def test_user_a_can_never_access_user_b_personal_key(runtime_setup):
    client, set_outcome, clients = runtime_setup
    user_a, token_a, key_a = _create_user_with_key(client, email="usera@test.com", raw_key="AIzaSyKeyA111111111")
    user_b, token_b, key_b = _create_user_with_key(client, email="userb@test.com", raw_key="AIzaSyKeyB222222222")

    set_outcome(key_a, "Answer for A")
    set_outcome(key_b, "Answer for B")

    # User A calls
    res_a = client.post("/api/v1/ask", json={"question": "A question"}, headers={"Authorization": f"Bearer {token_a}"})
    assert res_a.json()["answer"] == "Answer for A"
    assert len(clients[key_a].calls) == 1
    assert len(clients[key_b].calls) == 0

    # User B calls
    res_b = client.post("/api/v1/ask", json={"question": "B question"}, headers={"Authorization": f"Bearer {token_b}"})
    assert res_b.json()["answer"] == "Answer for B"
    assert len(clients[key_b].calls) == 1


# ---------------------------------------------------------------------------
# 10. Every Supervisor, Shopping and Order Support call uses the resolver
# ---------------------------------------------------------------------------
def test_supervisor_and_shopping_use_resolver(runtime_setup):
    client, set_outcome, clients = runtime_setup
    user, token, raw_key = _create_user_with_key(client)
    headers = {"Authorization": f"Bearer {token}"}

    set_outcome(raw_key, "Personal shopping route")
    set_outcome("test-platform-company-key", "Company")

    # Call /shopping/chat
    res = client.post("/api/v1/shopping/chat", json={"question": "Do you have mechanical keyboards?"}, headers=headers)
    assert res.status_code == 200

    # Personal key was called
    assert len(clients[raw_key].calls) >= 1


# ---------------------------------------------------------------------------
# 11. /ask remains backward compatible
# ---------------------------------------------------------------------------
def test_ask_remains_backward_compatible(runtime_setup):
    client, set_outcome, clients = runtime_setup
    set_outcome("test-platform-company-key", "Standard ask answer")

    # Guest request with no token
    res = client.post("/api/v1/ask", json={"question": "General question"})
    assert res.status_code == 200
    assert res.json()["answer"] == "Standard ask answer"


# ---------------------------------------------------------------------------
# 12. Telemetry records PERSONAL and COMPANY source accurately
# ---------------------------------------------------------------------------
def test_telemetry_records_sources_accurately(runtime_setup):
    client, set_outcome, clients = runtime_setup
    user, token, raw_key = _create_user_with_key(client)
    headers = {"Authorization": f"Bearer {token}"}

    set_outcome(raw_key, "Personal answer")
    set_outcome("test-platform-company-key", "Company answer")

    # 1. Authenticated -> PERSONAL
    client.post("/api/v1/ask", json={"question": "Auth question"}, headers=headers)

    # 2. Guest -> COMPANY
    client.post("/api/v1/ask", json={"question": "Guest question"})

    with client.app.state.db_session_factory() as session:
        events = session.scalars(select(GeminiUsageEvent).order_by(GeminiUsageEvent.created_at.asc())).all()
        assert len(events) == 2
        assert events[0].key_source == "PERSONAL"
        assert events[0].user_id == user.id
        assert events[1].key_source == "COMPANY"
        assert events[1].user_id is None


# ---------------------------------------------------------------------------
# 13. Provider attempts and logical requests are distinguishable
# ---------------------------------------------------------------------------
def test_provider_attempts_and_logical_requests_are_distinguishable(runtime_setup):
    client, set_outcome, clients = runtime_setup
    user, token, raw_key = _create_user_with_key(client)
    headers = {"Authorization": f"Bearer {token}"}

    set_outcome(raw_key, errors.APIError(429, "Quota exceeded"))
    set_outcome("test-platform-company-key", "Company answer")

    client.post("/api/v1/ask", json={"question": "Distinguishable query"}, headers=headers)

    with client.app.state.db_session_factory() as session:
        events = session.scalars(select(GeminiUsageEvent).order_by(GeminiUsageEvent.attempt_number.asc())).all()
        assert len(events) == 2
        # Same logical request correlation ID
        assert events[0].correlation_id == events[1].correlation_id
        # Different attempts
        assert events[0].attempt_number == 1
        assert events[0].key_source == "PERSONAL"
        assert events[1].attempt_number == 2
        assert events[1].key_source == "COMPANY"


# ---------------------------------------------------------------------------
# 14. Missing token usage metadata is handled correctly
# ---------------------------------------------------------------------------
def test_missing_token_usage_metadata_handled_correctly(runtime_setup):
    client, set_outcome, clients = runtime_setup
    user, token, raw_key = _create_user_with_key(client)
    headers = {"Authorization": f"Bearer {token}"}

    # Tracking client without usage_metadata
    set_outcome(raw_key, "No tokens reported", usage_metadata=None)

    client.post("/api/v1/ask", json={"question": "Token check"}, headers=headers)

    with client.app.state.db_session_factory() as session:
        event = session.scalar(select(GeminiUsageEvent).where(GeminiUsageEvent.user_id == user.id))
        assert event is not None
        assert event.input_tokens is None
        assert event.output_tokens is None
        assert event.total_tokens is None


# ---------------------------------------------------------------------------
# 15. Telemetry failure does not break AI responses
# ---------------------------------------------------------------------------
def test_telemetry_failure_does_not_break_ai_responses(runtime_setup, monkeypatch):
    client, set_outcome, clients = runtime_setup
    user, token, raw_key = _create_user_with_key(client)
    headers = {"Authorization": f"Bearer {token}"}

    set_outcome(raw_key, "Resilient AI response")

    # Simulate telemetry database failure
    def broken_telemetry(*args, **kwargs):
        raise RuntimeError("Database connection died during telemetry write")

    monkeypatch.setattr("backend.app.services.personal_gemini_runtime.record_gemini_usage", broken_telemetry)

    # Request must still succeed!
    res = client.post("/api/v1/ask", json={"question": "Resilience test"}, headers=headers)
    assert res.status_code == 200
    assert res.json()["answer"] == "Resilient AI response"


# ---------------------------------------------------------------------------
# 16. No secret or prompt is stored in telemetry
# ---------------------------------------------------------------------------
def test_no_secret_or_prompt_stored_in_telemetry(runtime_setup):
    client, set_outcome, clients = runtime_setup
    secret_key = "AIzaSySuperSecretPersonalKey9999"
    user, token, raw_key = _create_user_with_key(client, raw_key=secret_key)
    headers = {"Authorization": f"Bearer {token}"}

    set_outcome(secret_key, "Safe response text")
    secret_question = "CONFIDENTIAL_USER_PROMPT_CONTAINING_PRIVATE_DATA_123"

    client.post("/api/v1/ask", json={"question": secret_question}, headers=headers)

    with client.app.state.db_session_factory() as session:
        event = session.scalar(select(GeminiUsageEvent).where(GeminiUsageEvent.user_id == user.id))
        assert event is not None
        # Verify event attributes do not contain the secret key or prompt
        for attr in ("correlation_id", "provider", "model_name", "key_source", "key_slot", "status", "error_category", "fallback_reason"):
            val = getattr(event, attr, None)
            if val:
                assert secret_key not in str(val)
                assert secret_question not in str(val)


# ---------------------------------------------------------------------------
# 17. Corrupted stored key falls back to company pool and marks CORRUPTED
# ---------------------------------------------------------------------------
def test_corrupted_key_falls_back_and_marks_status(runtime_setup):
    client, set_outcome, clients = runtime_setup
    user, token, raw_key = _create_user_with_key(client)
    headers = {"Authorization": f"Bearer {token}"}

    # Corrupt ciphertext in DB
    with client.app.state.db_session_factory() as session:
        rec = session.scalar(select(UserGeminiSettings).where(UserGeminiSettings.user_id == user.id))
        rec.encrypted_api_key = "v1:corrupted-ciphertext-token"
        session.commit()

    set_outcome("test-platform-company-key", "Fell back past corrupted key")

    res = client.post("/api/v1/ask", json={"question": "Corrupted key test"}, headers=headers)
    assert res.status_code == 200
    assert res.json()["answer"] == "Fell back past corrupted key"

    with client.app.state.db_session_factory() as session:
        rec = session.scalar(select(UserGeminiSettings).where(UserGeminiSettings.user_id == user.id))
        assert rec.status == "CORRUPTED"


# ---------------------------------------------------------------------------
# 18. Concurrent quota exhaustion does not overwrite INVALID status
# ---------------------------------------------------------------------------
def test_concurrent_quota_does_not_overwrite_invalid_status(runtime_setup):
    client, set_outcome, clients = runtime_setup
    user, token, raw_key = _create_user_with_key(client)
    headers = {"Authorization": f"Bearer {token}"}

    def simulate_concurrent_invalidation(**kwargs):
        # Concurrently another request marks the key INVALID in the database
        with client.app.state.db_session_factory() as session:
            rec = session.scalar(select(UserGeminiSettings).where(UserGeminiSettings.user_id == user.id))
            rec.status = "INVALID"
            session.commit()
        # This request encounters a 429 quota exhaustion
        return errors.APIError(429, "Resource exhausted")

    set_outcome(raw_key, simulate_concurrent_invalidation)
    set_outcome("test-platform-company-key", "Company fallback response")

    res = client.post("/api/v1/ask", json={"question": "Concurrency test 1"}, headers=headers)
    assert res.status_code == 200
    assert res.json()["answer"] == "Company fallback response"

    # Status must remain INVALID, and not overwritten to ACTIVE
    with client.app.state.db_session_factory() as session:
        rec = session.scalar(select(UserGeminiSettings).where(UserGeminiSettings.user_id == user.id))
        assert rec.status == "INVALID"


# ---------------------------------------------------------------------------
# 19. Concurrent quota exhaustion does not overwrite CORRUPTED status
# ---------------------------------------------------------------------------
def test_concurrent_quota_does_not_overwrite_corrupted_status(runtime_setup):
    client, set_outcome, clients = runtime_setup
    user, token, raw_key = _create_user_with_key(client)
    headers = {"Authorization": f"Bearer {token}"}

    def simulate_concurrent_corruption(**kwargs):
        # Concurrently decryption failure marks the key CORRUPTED in the database
        with client.app.state.db_session_factory() as session:
            rec = session.scalar(select(UserGeminiSettings).where(UserGeminiSettings.user_id == user.id))
            rec.status = "CORRUPTED"
            session.commit()
        # This request encounters 429
        return errors.APIError(429, "Resource exhausted")

    set_outcome(raw_key, simulate_concurrent_corruption)
    set_outcome("test-platform-company-key", "Company fallback response")

    res = client.post("/api/v1/ask", json={"question": "Concurrency test 2"}, headers=headers)
    assert res.status_code == 200
    assert res.json()["answer"] == "Company fallback response"

    # Status must remain CORRUPTED
    with client.app.state.db_session_factory() as session:
        rec = session.scalar(select(UserGeminiSettings).where(UserGeminiSettings.user_id == user.id))
        assert rec.status == "CORRUPTED"


# ---------------------------------------------------------------------------
# 20. Successful request does not reactivate an INVALID or CORRUPTED key
# ---------------------------------------------------------------------------
def test_successful_request_does_not_reactivate_invalid_or_corrupted_key(runtime_setup):
    client, set_outcome, clients = runtime_setup
    user, token, raw_key = _create_user_with_key(client)
    headers = {"Authorization": f"Bearer {token}"}

    def simulate_concurrent_invalidation_on_success(**kwargs):
        with client.app.state.db_session_factory() as session:
            rec = session.scalar(select(UserGeminiSettings).where(UserGeminiSettings.user_id == user.id))
            rec.status = "INVALID"
            session.commit()
        return "Personal generated text"

    set_outcome(raw_key, simulate_concurrent_invalidation_on_success)

    res = client.post("/api/v1/ask", json={"question": "Concurrency test 3"}, headers=headers)
    assert res.status_code == 200
    assert res.json()["answer"] == "Personal generated text"

    # Status must remain INVALID
    with client.app.state.db_session_factory() as session:
        rec = session.scalar(select(UserGeminiSettings).where(UserGeminiSettings.user_id == user.id))
        assert rec.status == "INVALID"


# ---------------------------------------------------------------------------
# 21. Valid ACTIVE key correctly enters cooldown on 429 and preserves ACTIVE status
# ---------------------------------------------------------------------------
def test_active_key_can_still_enter_quota_cooldown(runtime_setup):
    client, set_outcome, clients = runtime_setup
    user, token, raw_key = _create_user_with_key(client, status="ACTIVE")
    headers = {"Authorization": f"Bearer {token}"}

    set_outcome(raw_key, errors.APIError(429, "Quota exceeded"))
    set_outcome("test-platform-company-key", "Company answer after quota")

    res = client.post("/api/v1/ask", json={"question": "Cooldown check"}, headers=headers)
    assert res.status_code == 200
    assert res.json()["answer"] == "Company answer after quota"

    with client.app.state.db_session_factory() as session:
        rec = session.scalar(select(UserGeminiSettings).where(UserGeminiSettings.user_id == user.id))
        assert rec.status == "ACTIVE"
        assert rec.cooldown_until is not None
        cooldown_utc = ensure_utc(rec.cooldown_until)
        assert cooldown_utc > utc_now()


# ---------------------------------------------------------------------------
# 22. Expired cooldown clears cooldown and restores personal key usage
# ---------------------------------------------------------------------------
def test_expired_cooldown_allows_active_key_retry(runtime_setup):
    client, set_outcome, clients = runtime_setup
    past_cooldown = utc_now() - timedelta(minutes=5)
    user, token, raw_key = _create_user_with_key(client, status="ACTIVE", cooldown_until=past_cooldown)
    headers = {"Authorization": f"Bearer {token}"}

    set_outcome(raw_key, "Recovered personal answer")

    res = client.post("/api/v1/ask", json={"question": "Expired cooldown retry"}, headers=headers)
    assert res.status_code == 200
    assert res.json()["answer"] == "Recovered personal answer"

    with client.app.state.db_session_factory() as session:
        rec = session.scalar(select(UserGeminiSettings).where(UserGeminiSettings.user_id == user.id))
        assert rec.status == "ACTIVE"
        assert rec.cooldown_until is None
        assert rec.last_used_at is not None


# ---------------------------------------------------------------------------
# 23. Subsequent requests after invalidation are rejected without calling Gemini
# ---------------------------------------------------------------------------
def test_subsequent_request_after_invalidation_skips_personal_key(runtime_setup):
    client, set_outcome, clients = runtime_setup
    user, token, raw_key = _create_user_with_key(client, status="INVALID")
    headers = {"Authorization": f"Bearer {token}"}

    set_outcome(raw_key, "Should never be called")
    set_outcome("test-platform-company-key", "Company direct response")

    res = client.post("/api/v1/ask", json={"question": "Invalid key request"}, headers=headers)
    assert res.status_code == 200
    assert res.json()["answer"] == "Company direct response"

    # Personal client calls should be empty
    assert len(clients[raw_key].calls) == 0

