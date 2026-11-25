from __future__ import annotations

from datetime import timedelta
import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from google.genai import errors, types
from pydantic import SecretStr
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from backend.app.core.config import Settings
from backend.app.core.datetime_utils import utc_now
from backend.app.db.base import Base
from backend.app.db.models import (
    AIModel,
    AIProvider,
    GeminiUsageEvent,
    ModelAccessClass,
    ProviderConnectionStatus,
    User,
    UserGeminiSettings,
)
from backend.app.db.seed_ai_catalogue import seed_ai_provider_catalogue
from backend.app.security.key_encryption import encrypt_user_key
from backend.app.services.gemini_service import (
    GeminiProviderError,
    GeminiResponseError,
    GeminiResult,
    GeminiService,
    GeminiTimeoutError,
)
from backend.app.services.personal_gemini_runtime import RequestScopedGeminiService
from backend.app.services.providers.base import (
    AIProviderAdapter,
    AIProviderAuthenticationError,
    AIProviderBadRequestError,
    AIProviderError,
    AIProviderQuotaExhaustedError,
    AIProviderRateLimitError,
    AIProviderResponseError,
    AIProviderTimeoutError,
    AIProviderTransientError,
    GenerationRequest,
    GenerationResponse,
    ProviderCapabilities,
    TokenUsage,
)
from backend.app.services.providers.gemini_adapter import GeminiProviderAdapter
from backend.app.services.providers.resolver import (
    DisabledModelError,
    DisabledProviderError,
    ProviderAdapterUnavailableError,
    ProviderRuntimeResolver,
    UnknownModelError,
    UnknownProviderError,
)
from backend.app.services.providers.security import (
    DEFAULT_OUTBOUND_TIMEOUT_SECONDS,
    FOLLOW_REDIRECTS,
    MAX_SAFE_RESPONSE_BYTES,
    mask_sensitive_token,
    validate_provider_endpoint,
)
from backend.app.services.providers.telemetry import (
    NormalizedTelemetryRecord,
    record_normalized_telemetry,
)

logger = logging.getLogger(__name__)

TEST_USER_SECRET = SecretStr("test-user-encryption-secret-32-bytes-long-12345")


# ---------------------------------------------------------------------------
# Test Fixtures & Mocks
# ---------------------------------------------------------------------------

@pytest.fixture
def db_session() -> Any:
    """Create a fresh in-memory SQLite database session with seeded catalogue."""
    engine = create_engine("sqlite+pysqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    session = session_factory()
    try:
        seed_ai_provider_catalogue(session)
        yield session
    finally:
        session.close()
        engine.dispose()


class FakeClient:
    def __init__(self, key: str, outcome: Any = "Mocked answer", usage_metadata: Any = None) -> None:
        self.key = key
        self.outcome = outcome
        self.usage_metadata = usage_metadata
        self.calls: list[dict[str, Any]] = []
        self.closed = False

    @property
    def models(self) -> FakeClient:
        return self

    def generate_content(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        text = str(self.outcome)
        return SimpleNamespace(
            text=text,
            function_calls=[],
            candidates=[SimpleNamespace(content=SimpleNamespace(parts=[]))],
            usage_metadata=self.usage_metadata,
        )

    def close(self) -> None:
        self.closed = True


# ---------------------------------------------------------------------------
# 1. Adapter Interface & Data Models
# ---------------------------------------------------------------------------

def test_adapter_interface_cannot_be_instantiated_directly() -> None:
    """AIProviderAdapter is an ABC and cannot be directly instantiated."""
    with pytest.raises(TypeError):
        AIProviderAdapter()  # type: ignore[abstract]


def test_token_usage_no_fabrication() -> None:
    """TokenUsage preserves None values and does not fabricate missing counts."""
    usage_none = TokenUsage()
    assert usage_none.input_tokens is None
    assert usage_none.output_tokens is None
    assert usage_none.total_tokens is None
    assert usage_none.to_dict() == {
        "input_tokens": None,
        "output_tokens": None,
        "total_tokens": None,
    }

    usage_real = TokenUsage(input_tokens=150, output_tokens=42, total_tokens=192)
    assert usage_real.input_tokens == 150
    assert usage_real.output_tokens == 42
    assert usage_real.total_tokens == 192


def test_generation_request_and_response_structures() -> None:
    """GenerationRequest and GenerationResponse encapsulate provider-neutral data."""
    req = GenerationRequest(
        prompt="Explain quantum computing",
        model="gemini-3.8-flash",
        temperature=0.7,
        max_tokens=1000,
        correlation_id="corr-123",
        metadata={"user_role": "admin"},
    )
    assert req.prompt == "Explain quantum computing"
    assert req.model == "gemini-3.8-flash"
    assert req.timeout_seconds == 30.0

    resp = GenerationResponse(
        text="Quantum computing utilizes qubits...",
        usage=TokenUsage(input_tokens=10, output_tokens=50, total_tokens=60),
        model="gemini-3.8-flash",
        provider="google_gemini",
        latency_ms=125.4,
    )
    assert resp.text.startswith("Quantum")
    assert resp.usage.total_tokens == 60
    assert resp.provider == "google_gemini"
    assert resp.latency_ms == 125.4


# ---------------------------------------------------------------------------
# 2. Normalized Error Hierarchy & Retryability
# ---------------------------------------------------------------------------

def test_normalized_error_hierarchy_attributes() -> None:
    """Every normalized error specifies provider_code, retryability, and error_category."""
    base_err = AIProviderError("Base issue", provider_code="test_prov", model="m1", status_code=500)
    assert base_err.provider_code == "test_prov"
    assert base_err.is_retryable is False
    assert base_err.error_category == "PROVIDER_ERROR"
    assert "[test_prov]" in str(base_err)

    # Timeout: retryable
    timeout_err = AIProviderTimeoutError("Exceeded 30s", provider_code="google_gemini", model="gemini-3.8-flash")
    assert timeout_err.is_retryable is True
    assert timeout_err.error_category == "TIMEOUT"

    # Rate limit: retryable
    rate_err = AIProviderRateLimitError("RPM limit hit", provider_code="google_gemini", retry_after_seconds=12.5)
    assert rate_err.is_retryable is True
    assert rate_err.retry_after_seconds == 12.5
    assert rate_err.error_category == "RATE_LIMIT"

    # Quota exhausted: non-retryable
    quota_err = AIProviderQuotaExhaustedError("Daily limit reached", provider_code="google_gemini")
    assert quota_err.is_retryable is False
    assert quota_err.error_category == "QUOTA_EXHAUSTED"

    # Auth failure: non-retryable
    auth_err = AIProviderAuthenticationError("Invalid API key", provider_code="google_gemini", status_code=401)
    assert auth_err.is_retryable is False
    assert auth_err.status_code == 401
    assert auth_err.error_category == "AUTHENTICATION_FAILED"

    # Transient error: retryable
    trans_err = AIProviderTransientError("Service unavailable", provider_code="google_gemini", status_code=503)
    assert trans_err.is_retryable is True
    assert trans_err.error_category == "TRANSIENT_ERROR"

    # Bad request: non-retryable
    bad_req = AIProviderBadRequestError("Blank prompt", provider_code="google_gemini", status_code=400)
    assert bad_req.is_retryable is False
    assert bad_req.error_category == "BAD_REQUEST"

    # Response error: non-retryable
    resp_err = AIProviderResponseError("Empty response", provider_code="google_gemini")
    assert resp_err.is_retryable is False
    assert resp_err.error_category == "EMPTY_RESPONSE"


# ---------------------------------------------------------------------------
# 3. Gemini Adapter Compatibility & Execution
# ---------------------------------------------------------------------------

def test_gemini_adapter_properties_and_capabilities() -> None:
    """GeminiProviderAdapter reports correct provider identity and capabilities."""
    mock_service = MagicMock(spec=GeminiService)
    adapter = GeminiProviderAdapter(mock_service, default_model="gemini-3.8-flash")

    assert adapter.provider_code == "google_gemini"
    assert adapter.display_name == "Google Gemini"
    assert adapter.underlying_service is mock_service

    caps = adapter.get_capabilities()
    assert caps.supports_text is True
    assert caps.supports_structured_output is True
    assert caps.supports_function_calling is True
    assert caps.max_context_window == 1048576

    assert adapter.supports_model("gemini-3.8-flash") is True
    assert adapter.supports_model("gemini-3.5-flash-lite") is True
    assert adapter.supports_model("gpt-4o") is False
    assert adapter.supports_model("claude-3-5-sonnet") is False


def test_gemini_adapter_generate_success_with_token_usage() -> None:
    """GeminiProviderAdapter delegates generate() and extracts usage metrics without fabrication."""
    mock_service = MagicMock(spec=GeminiService)
    fake_usage = SimpleNamespace(
        prompt_token_count=120,
        candidates_token_count=45,
        total_token_count=165,
    )
    mock_service.generate.return_value = GeminiResult(
        text="StoreFlow returns verified products.",
        function_calls=(),
        candidate_content=None,
        usage_metadata=fake_usage,
    )

    adapter = GeminiProviderAdapter(mock_service, default_model="gemini-3.8-flash")
    req = GenerationRequest(
        prompt="Tell me about products",
        model="gemini-3.8-flash",
    )
    resp = adapter.generate(req)

    assert resp.text == "StoreFlow returns verified products."
    assert resp.provider == "google_gemini"
    assert resp.model == "gemini-3.8-flash"
    assert resp.usage is not None
    assert resp.usage.input_tokens == 120
    assert resp.usage.output_tokens == 45
    assert resp.usage.total_tokens == 165
    assert resp.latency_ms >= 0


def test_gemini_adapter_generate_text_and_empty_handling() -> None:
    """GeminiProviderAdapter generate_text returns plain string and rejects blank prompts."""
    mock_service = MagicMock(spec=GeminiService)
    mock_service.answer.return_value = "Verified product response."
    adapter = GeminiProviderAdapter(mock_service, default_model="gemini-3.8-flash")

    text = adapter.generate_text("What is StoreFlow?")
    assert text == "Verified product response."
    mock_service.answer.assert_called_once_with("What is StoreFlow?")

    # Rejects empty prompt
    with pytest.raises(AIProviderBadRequestError) as exc_info:
        adapter.generate_text("   ")
    assert "Prompt must not be empty" in str(exc_info.value)


def test_gemini_adapter_async_wrappers() -> None:
    """GeminiProviderAdapter provides working async wrappers for async callers."""
    import asyncio

    mock_service = MagicMock(spec=GeminiService)
    mock_service.generate.return_value = GeminiResult(
        text="Async response text",
        function_calls=(),
        candidate_content=None,
        usage_metadata=None,
    )
    adapter = GeminiProviderAdapter(mock_service, default_model="gemini-3.8-flash")

    resp = asyncio.run(adapter.generate_async(GenerationRequest(prompt="Async test")))
    assert resp.text == "Async response text"
    assert resp.usage is None  # Not fabricated


# ---------------------------------------------------------------------------
# 4. Exception Normalization in Gemini Adapter
# ---------------------------------------------------------------------------

def test_gemini_adapter_normalizes_timeout_error() -> None:
    """GeminiTimeoutError maps to AIProviderTimeoutError."""
    mock_service = MagicMock(spec=GeminiService)
    mock_service.generate.side_effect = GeminiTimeoutError("Request timed out after 30s")

    adapter = GeminiProviderAdapter(mock_service, default_model="gemini-3.8-flash")
    with pytest.raises(AIProviderTimeoutError) as exc_info:
        adapter.generate(GenerationRequest(prompt="test timeout"))

    err = exc_info.value
    assert err.is_retryable is True
    assert err.error_category == "TIMEOUT"
    assert err.provider_code == "google_gemini"


def test_gemini_adapter_normalizes_rate_limit_and_quota_exhaustion() -> None:
    """429 errors distinguish rate limits from daily quota exhaustion."""
    mock_service = MagicMock(spec=GeminiService)

    # 1. Rate limit (RPM)
    mock_service.generate.side_effect = GeminiProviderError("429 ResourceExhausted: rate limit exceeded, retry in 15s")
    adapter = GeminiProviderAdapter(mock_service, default_model="gemini-3.8-flash")

    with pytest.raises(AIProviderRateLimitError) as exc_info:
        adapter.generate(GenerationRequest(prompt="test 429"))
    err = exc_info.value
    assert err.is_retryable is True
    assert err.retry_after_seconds == 15.0
    assert err.error_category == "RATE_LIMIT"

    # 2. Daily quota exhaustion (RPD)
    mock_service.generate.side_effect = GeminiProviderError("429 ResourceExhausted: exceeded your daily requests quota")
    with pytest.raises(AIProviderQuotaExhaustedError) as exc_info2:
        adapter.generate(GenerationRequest(prompt="test daily 429"))
    err2 = exc_info2.value
    assert err2.is_retryable is False
    assert err2.error_category == "QUOTA_EXHAUSTED"


def test_gemini_adapter_normalizes_auth_and_transient_errors() -> None:
    """Invalid key and 503 errors map to appropriate normalized classes."""
    mock_service = MagicMock(spec=GeminiService)

    # Auth error
    mock_service.generate.side_effect = GeminiProviderError("400 API_KEY_INVALID: API key not valid")
    adapter = GeminiProviderAdapter(mock_service, default_model="gemini-3.8-flash")
    with pytest.raises(AIProviderAuthenticationError) as exc_info:
        adapter.generate(GenerationRequest(prompt="test auth"))
    assert exc_info.value.is_retryable is False
    assert exc_info.value.error_category == "AUTHENTICATION_FAILED"

    # Transient 503
    mock_service.generate.side_effect = GeminiProviderError("503 Service Unavailable: backend transient error")
    with pytest.raises(AIProviderTransientError) as exc_info2:
        adapter.generate(GenerationRequest(prompt="test 503"))
    assert exc_info2.value.is_retryable is True
    assert exc_info2.value.error_category == "TRANSIENT_ERROR"


# ---------------------------------------------------------------------------
# 5. Personal Key Priority & Company Fallback Preservation
# ---------------------------------------------------------------------------

def test_gemini_adapter_with_request_scoped_personal_key_priority(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GeminiProviderAdapter wrapping RequestScopedGeminiService executes personal key first."""
    settings = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        auth_secret_key="test-auth-secret-key-0123456789abcdef",
        gemini_api_key=SecretStr("company-fallback-key"),
        gemini_model="gemini-3.8-flash",
        user_key_encryption_secret=TEST_USER_SECRET,
    )

    # Create user with active personal key
    user = User(
        id=uuid4(),
        email="testuser@storeflow.internal",
        password_hash="pw",
        role="customer",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    encrypted_key = encrypt_user_key("test-only-fake-gemini-key-material-2345", TEST_USER_SECRET)
    user_settings = UserGeminiSettings(
        user_id=user.id,
        encrypted_api_key=encrypted_key,
        key_hint="2345",
        status="ACTIVE",
        is_enabled=True,
    )
    db_session.add(user_settings)
    db_session.commit()

    # Track which key is called
    called_keys: list[str] = []

    def mock_client_factory(api_key: str, **kwargs: Any) -> FakeClient:
        called_keys.append(api_key)
        return FakeClient(api_key, outcome="Personal key generated response")

    company_service = GeminiService(
        settings=settings,
        client_factory=mock_client_factory,
    )

    scoped_service = RequestScopedGeminiService(
        platform_service=company_service,
        settings=settings,
        user=user,
        session_factory=lambda: db_session,
        client_factory=mock_client_factory,
    )

    # Clear initial key slot initialization call
    called_keys.clear()

    adapter = GeminiProviderAdapter(scoped_service, default_model="gemini-3.8-flash")
    resp = adapter.generate(GenerationRequest(prompt="Hello personal key"))

    assert resp.text == "Personal key generated response"
    # Personal key must have been called for generation
    assert len(called_keys) == 1
    assert called_keys[0] == "test-only-fake-gemini-key-material-2345"


def test_gemini_adapter_personal_key_fallback_to_company_pool_on_429(
    db_session: Session,
) -> None:
    """When personal key receives 429, it sets cooldown and falls back to company pool."""
    settings = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        auth_secret_key="test-auth-secret-key-0123456789abcdef",
        gemini_api_key=SecretStr("company-fallback-key"),
        gemini_model="gemini-3.8-flash",
        user_key_encryption_secret=TEST_USER_SECRET,
    )

    user = User(
        id=uuid4(),
        email="quotauser@storeflow.internal",
        password_hash="pw",
        role="customer",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    encrypted_key = encrypt_user_key("test-only-fake-gemini-key-material-429", TEST_USER_SECRET)
    user_settings = UserGeminiSettings(
        user_id=user.id,
        encrypted_api_key=encrypted_key,
        key_hint="2345",
        status="ACTIVE",
        is_enabled=True,
    )
    db_session.add(user_settings)
    db_session.commit()

    called_keys: list[str] = []

    def mock_client_factory(api_key: str, **kwargs: Any) -> FakeClient:
        called_keys.append(api_key)
        if api_key == "test-only-fake-gemini-key-material-429":
            return FakeClient(
                api_key,
                outcome=errors.APIError(
                    429,
                    "ResourceExhausted: rate limit exceeded, retry in 60s",
                ),
            )
        return FakeClient(api_key, outcome="Company fallback response")

    company_service = GeminiService(
        settings=settings,
        client_factory=mock_client_factory,
    )

    scoped_service = RequestScopedGeminiService(
        platform_service=company_service,
        settings=settings,
        user=user,
        session_factory=lambda: db_session,
        client_factory=mock_client_factory,
    )

    # Clear initial key slot initialization call
    called_keys.clear()

    adapter = GeminiProviderAdapter(scoped_service, default_model="gemini-3.8-flash")
    resp = adapter.generate(GenerationRequest(prompt="Hello fallback"))

    assert resp.text == "Company fallback response"
    # Personal key was attempted before falling back
    assert "test-only-fake-gemini-key-material-429" in called_keys

    # Verify personal key cooldown was recorded in DB
    updated_settings = db_session.scalar(
        select(UserGeminiSettings).where(UserGeminiSettings.user_id == user.id)
    )
    assert updated_settings is not None
    assert updated_settings.cooldown_until is not None


# ---------------------------------------------------------------------------
# 6. Provider Runtime Resolver & Rejection Rules
# ---------------------------------------------------------------------------

def test_resolver_successful_resolution(db_session: Session) -> None:
    """Resolver successfully finds google_gemini adapter and model metadata."""
    mock_service = MagicMock(spec=GeminiService)
    adapter = GeminiProviderAdapter(mock_service, default_model="gemini-3.8-flash")

    resolver = ProviderRuntimeResolver()
    resolver.register_adapter(adapter)

    resolved_adapter, model_record = resolver.resolve(
        db_session,
        provider_code="google_gemini",
        model_name="gemini-3.8-flash",
    )

    assert resolved_adapter is adapter
    assert model_record is not None
    assert model_record.model_identifier == "gemini-3.8-flash"
    assert model_record.is_enabled is True


def test_resolver_async_resolution(db_session: Session) -> None:
    """Resolver supports resolve_async via async session."""
    import asyncio
    from unittest.mock import AsyncMock

    mock_service = MagicMock(spec=GeminiService)
    adapter = GeminiProviderAdapter(mock_service, default_model="gemini-3.8-flash")
    resolver = ProviderRuntimeResolver()
    resolver.register_adapter(adapter)

    async_session = AsyncMock()
    mock_provider = AIProvider(
        id=uuid4(),
        code="google_gemini",
        display_name="Google Gemini",
        adapter_type="google_gemini",
        connection_status="AVAILABLE",
        is_enabled=True,
    )
    mock_model = AIModel(
        id=uuid4(),
        provider_id=mock_provider.id,
        model_identifier="gemini-3.8-flash",
        is_enabled=True,
    )

    mock_exec_p = MagicMock()
    mock_exec_p.scalar_one_or_none.return_value = mock_provider
    mock_exec_m = MagicMock()
    mock_exec_m.scalar_one_or_none.return_value = mock_model
    async_session.execute.side_effect = [mock_exec_p, mock_exec_m]

    res_adapter, res_model = asyncio.run(
        resolver.resolve_async(async_session, "google_gemini", "gemini-3.8-flash")
    )
    assert res_adapter is adapter
    assert res_model is not None
    assert res_model.model_identifier == "gemini-3.8-flash"


def test_resolver_rejects_unknown_provider(db_session: Session) -> None:
    """Resolver raises UnknownProviderError for unseeded or nonexistent provider."""
    resolver = ProviderRuntimeResolver()
    with pytest.raises(UnknownProviderError) as exc_info:
        resolver.resolve(db_session, provider_code="unknown_custom_ai")

    assert exc_info.value.status_code == 404
    assert exc_info.value.provider_code == "unknown_custom_ai"
    assert "not registered" in str(exc_info.value)


def test_resolver_rejects_disabled_provider(db_session: Session) -> None:
    """Resolver raises DisabledProviderError when provider is disabled in registry."""
    # Disable google_gemini in DB
    provider = db_session.scalar(select(AIProvider).where(AIProvider.code == "google_gemini"))
    assert provider is not None
    provider.is_enabled = False
    db_session.commit()

    mock_service = MagicMock(spec=GeminiService)
    adapter = GeminiProviderAdapter(mock_service)
    resolver = ProviderRuntimeResolver()
    resolver.register_adapter(adapter)

    with pytest.raises(DisabledProviderError) as exc_info:
        resolver.resolve(db_session, provider_code="google_gemini")

    assert exc_info.value.status_code == 403
    assert exc_info.value.error_category == "DISABLED_PROVIDER"


def test_resolver_rejects_unknown_model(db_session: Session) -> None:
    """Resolver raises UnknownModelError when requested model is not found under provider."""
    mock_service = MagicMock(spec=GeminiService)
    adapter = GeminiProviderAdapter(mock_service)
    resolver = ProviderRuntimeResolver()
    resolver.register_adapter(adapter)

    with pytest.raises(UnknownModelError) as exc_info:
        resolver.resolve(db_session, provider_code="google_gemini", model_name="nonexistent-model-v9")

    assert exc_info.value.status_code == 404
    assert exc_info.value.error_category == "UNKNOWN_MODEL"
    assert exc_info.value.model == "nonexistent-model-v9"


def test_resolver_rejects_disabled_model(db_session: Session) -> None:
    """Resolver raises DisabledModelError when requested model is disabled."""
    # Disable gemini-3.8-flash in DB
    model = db_session.scalar(select(AIModel).where(AIModel.model_identifier == "gemini-3.8-flash"))
    assert model is not None
    model.is_enabled = False
    db_session.commit()

    mock_service = MagicMock(spec=GeminiService)
    adapter = GeminiProviderAdapter(mock_service)
    resolver = ProviderRuntimeResolver()
    resolver.register_adapter(adapter)

    with pytest.raises(DisabledModelError) as exc_info:
        resolver.resolve(db_session, provider_code="google_gemini", model_name="gemini-3.8-flash")

    assert exc_info.value.status_code == 403
    assert exc_info.value.error_category == "DISABLED_MODEL"


def test_resolver_rejects_catalogue_provider_without_operational_adapter(db_session: Session) -> None:
    """Resolver raises ProviderAdapterUnavailableError for PLANNED providers without runtime adapters."""
    resolver = ProviderRuntimeResolver()
    # Notice: groq is in the catalogue with status PLANNED, but no adapter is registered
    with pytest.raises(ProviderAdapterUnavailableError) as exc_info:
        resolver.resolve(db_session, provider_code="groq")

    assert exc_info.value.status_code == 501
    assert exc_info.value.error_category == "ADAPTER_UNAVAILABLE"
    assert "Only 'google_gemini' runtime adapter is operational" in str(exc_info.value)


def test_resolver_avoids_silent_fallback(db_session: Session) -> None:
    """Resolver never silently substitutes another provider when one fails or is unavailable."""
    resolver = ProviderRuntimeResolver()
    mock_service = MagicMock(spec=GeminiService)
    resolver.register_adapter(GeminiProviderAdapter(mock_service))

    # Requesting openrouter must NOT return google_gemini
    with pytest.raises(ProviderAdapterUnavailableError):
        resolver.resolve(db_session, provider_code="openrouter")


# ---------------------------------------------------------------------------
# 7. Security Protections
# ---------------------------------------------------------------------------

def test_endpoint_validation_security_rules() -> None:
    """validate_provider_endpoint enforces HTTPS and rejects SSRF targets."""
    # Valid HTTPS endpoint
    valid = validate_provider_endpoint("https://generativelanguage.googleapis.com")
    assert valid == "https://generativelanguage.googleapis.com"

    # Insecure HTTP rejected
    with pytest.raises(AIProviderBadRequestError) as exc_info1:
        validate_provider_endpoint("http://api.insecure.com")
    assert "Rejected insecure scheme: http" in str(exc_info1.value)

    # Loopback / SSRF hostnames rejected
    with pytest.raises(AIProviderBadRequestError) as exc_info2:
        validate_provider_endpoint("https://localhost:8000")
    assert "Forbidden host 'localhost'" in str(exc_info2.value)

    # Cloud metadata IP rejected
    with pytest.raises(AIProviderBadRequestError) as exc_info3:
        validate_provider_endpoint("https://169.254.169.254/latest")
    assert "Forbidden host '169.254.169.254'" in str(exc_info3.value)


def test_mask_sensitive_token() -> None:
    """mask_sensitive_token never exposes raw credentials in telemetry or logs."""
    assert mask_sensitive_token("test-only-fake-token-1234567890abcdef") == "tes...cdef"
    assert mask_sensitive_token("short") == "...hort"
    assert mask_sensitive_token("") == "****"
    assert mask_sensitive_token(None) == "****"


def test_security_constants() -> None:
    """Security defaults enforce timeout, redirect disabling, and response limits."""
    assert MAX_SAFE_RESPONSE_BYTES == 10 * 1024 * 1024  # 10 MB limit
    assert DEFAULT_OUTBOUND_TIMEOUT_SECONDS == 30.0
    assert FOLLOW_REDIRECTS is False  # Must not follow redirects


# ---------------------------------------------------------------------------
# 8. Telemetry Compatibility & Safe Storage
# ---------------------------------------------------------------------------

def test_record_normalized_telemetry_persists_safely(db_session: Session) -> None:
    """Normalized telemetry record persists into GeminiUsageEvent without prompt or secret storage."""
    rec = NormalizedTelemetryRecord(
        correlation_id="corr-telemetry-test",
        provider="google_gemini",
        model_name="gemini-3.8-flash",
        key_source="COMPANY",
        status="SUCCESS",
        key_slot="slot-1",
        attempt_number=1,
        input_tokens=85,
        output_tokens=32,
        total_tokens=117,
        latency_ms=210.5,
    )

    record_normalized_telemetry(db_session, rec)

    event = db_session.scalar(
        select(GeminiUsageEvent).where(GeminiUsageEvent.correlation_id == "corr-telemetry-test")
    )
    assert event is not None
    assert event.provider == "google_gemini"
    assert event.model_name == "gemini-3.8-flash"
    assert event.key_source == "COMPANY"
    assert event.status == "SUCCESS"
    assert event.input_tokens == 85
    assert event.output_tokens == 32
    assert event.total_tokens == 117
    assert event.latency_ms == 210.5

    # Confirm model columns have NO raw prompts or responses
    col_names = [c.name for c in GeminiUsageEvent.__table__.columns]
    assert "prompt" not in col_names
    assert "response" not in col_names
    assert "api_key" not in col_names


def test_record_normalized_telemetry_swallows_db_errors_safely() -> None:
    """Telemetry persistence errors are swallowed so user operations are never broken."""
    broken_session = MagicMock(spec=Session)
    broken_session.add.side_effect = RuntimeError("Database connection lost")

    rec = NormalizedTelemetryRecord(
        correlation_id="corr-broken",
        provider="google_gemini",
        model_name="gemini-3.8-flash",
        key_source="COMPANY",
        status="FAILED",
    )
    # Should not raise exception
    record_normalized_telemetry(broken_session, rec)
    broken_session.rollback.assert_called_once()
