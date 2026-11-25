from __future__ import annotations

from datetime import timedelta
import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import httpx
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
from backend.app.services.providers.groq_adapter import GroqProviderAdapter
from backend.app.services.providers.orchestrator import AIProviderOrchestrator
from backend.app.services.providers.resolver import (
    DisabledModelError,
    DisabledProviderError,
    ProviderAdapterUnavailableError,
    ProviderRuntimeResolver,
    ProviderTrafficNotPermittedError,
    UnknownModelError,
    UnknownProviderError,
)
from backend.app.services.providers.telemetry import (
    NormalizedTelemetryRecord,
    record_normalized_telemetry,
)

logger = logging.getLogger(__name__)

TEST_SECRET = SecretStr("test-encryption-secret-32-bytes-long-12345")


# ---------------------------------------------------------------------------
# Fixtures & Mocks
# ---------------------------------------------------------------------------

@pytest.fixture
def db_session() -> Any:
    """Fresh in-memory SQLite database session seeded with AI catalogue."""
    engine = create_engine("sqlite+pysqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    session = session_factory()
    try:
        seed_ai_provider_catalogue(session)
        session.commit()
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


def make_test_settings() -> Settings:
    return Settings(
        gemini_api_key=SecretStr("test-platform-key"),
        gemini_model="gemini-2.5-flash",
        gemini_timeout_seconds=5.0,
        gemini_user_key_secret=TEST_SECRET,
        database_url=SecretStr("sqlite+pysqlite:///:memory:"),
        auth_secret_key=SecretStr("test-auth-secret-key-0123456789abcdef"),
    )


# ---------------------------------------------------------------------------
# 1. Orchestrator Initialization & Basic Resolution Tests
# ---------------------------------------------------------------------------

def test_orchestrator_initialization(db_session: Session) -> None:
    """Orchestrator initializes with resolver, session, and default provider."""
    resolver = ProviderRuntimeResolver()
    service = GeminiService(make_test_settings(), client_factory=lambda **kw: FakeClient("test"))
    adapter = GeminiProviderAdapter(service)
    resolver.register_adapter(adapter)

    orchestrator = AIProviderOrchestrator(
        resolver=resolver,
        session=db_session,
        default_provider="google_gemini",
        default_model="gemini-2.5-flash",
        correlation_id="corr-init-123",
    )

    assert orchestrator.default_provider == "google_gemini"
    assert orchestrator.default_model == "gemini-2.5-flash"
    assert orchestrator.correlation_id == "corr-init-123"


def test_orchestrator_successful_generate_text(db_session: Session) -> None:
    """Orchestrator generate_text invokes Gemini adapter and returns response text."""
    fake = FakeClient("test-key", outcome="StoreFlow features high quality products.")
    service = GeminiService(make_test_settings(), client_factory=lambda **kw: fake)
    adapter = GeminiProviderAdapter(service, default_model="gemini-2.5-flash")
    resolver = ProviderRuntimeResolver()
    resolver.register_adapter(adapter)

    orchestrator = AIProviderOrchestrator(
        resolver=resolver,
        session=db_session,
        default_provider="google_gemini",
        default_model="gemini-2.5-flash",
    )

    answer = orchestrator.generate_text("Tell me about StoreFlow")
    assert answer == "StoreFlow features high quality products."
    assert len(fake.calls) == 1


def test_orchestrator_successful_generate_request_and_response(db_session: Session) -> None:
    """Orchestrator generate() returns GenerationResponse with usage and latency."""
    usage_mock = SimpleNamespace(
        prompt_token_count=12,
        candidates_token_count=24,
        total_token_count=36,
    )
    fake = FakeClient("test-key", outcome="Response text", usage_metadata=usage_mock)
    service = GeminiService(make_test_settings(), client_factory=lambda **kw: fake)
    adapter = GeminiProviderAdapter(service, default_model="gemini-2.5-flash")
    resolver = ProviderRuntimeResolver()
    resolver.register_adapter(adapter)

    orchestrator = AIProviderOrchestrator(
        resolver=resolver,
        session=db_session,
        default_provider="google_gemini",
        default_model="gemini-2.5-flash",
    )

    req = GenerationRequest(prompt="Hello", correlation_id="req-corr-99")
    resp = orchestrator.generate(req)

    assert isinstance(resp, GenerationResponse)
    assert resp.text == "Response text"
    assert resp.provider == "google_gemini"
    assert resp.model == "gemini-2.5-flash"
    assert resp.latency_ms > 0
    assert resp.usage is not None
    assert resp.usage.input_tokens == 12
    assert resp.usage.output_tokens == 24
    assert resp.usage.total_tokens == 36


def test_orchestrator_answer_protocol_compatibility(db_session: Session) -> None:
    """Orchestrator.answer() implements QuestionAnswerer protocol."""
    fake = FakeClient("test-key", outcome="Protocol answer")
    service = GeminiService(make_test_settings(), client_factory=lambda **kw: fake)
    adapter = GeminiProviderAdapter(service, default_model="gemini-2.5-flash")
    resolver = ProviderRuntimeResolver()
    resolver.register_adapter(adapter)

    orchestrator = AIProviderOrchestrator(
        resolver=resolver,
        session=db_session,
        default_provider="google_gemini",
    )

    result = orchestrator.answer("What is the protocol?")
    assert result == "Protocol answer"


# ---------------------------------------------------------------------------
# 2. Provider Resolution & Metadata Security Gates
# ---------------------------------------------------------------------------

def test_orchestrator_rejects_unknown_provider(db_session: Session) -> None:
    """Orchestrator strictly rejects unknown providers with UnknownProviderError (404)."""
    resolver = ProviderRuntimeResolver()
    orchestrator = AIProviderOrchestrator(
        resolver=resolver,
        session=db_session,
        default_provider="google_gemini",
    )

    with pytest.raises(UnknownProviderError) as exc_info:
        orchestrator.generate(GenerationRequest(prompt="test"), provider="unknown_ai_corp")

    assert exc_info.value.status_code == 404
    assert "unknown_ai_corp" in str(exc_info.value)


def test_orchestrator_rejects_disabled_provider(db_session: Session) -> None:
    """Orchestrator strictly rejects disabled providers with DisabledProviderError (403)."""
    gemini_record = db_session.scalar(select(AIProvider).where(AIProvider.code == "google_gemini"))
    assert gemini_record is not None
    gemini_record.is_enabled = False
    db_session.flush()

    resolver = ProviderRuntimeResolver()
    resolver.register_adapter(GeminiProviderAdapter(MagicMock()))
    orchestrator = AIProviderOrchestrator(
        resolver=resolver,
        session=db_session,
        default_provider="google_gemini",
    )

    with pytest.raises(DisabledProviderError) as exc_info:
        orchestrator.generate(GenerationRequest(prompt="test"))

    assert exc_info.value.status_code == 403


def test_orchestrator_rejects_unknown_model(db_session: Session) -> None:
    """Orchestrator rejects models not in the catalogue for the provider (404)."""
    resolver = ProviderRuntimeResolver()
    resolver.register_adapter(GeminiProviderAdapter(MagicMock()))
    orchestrator = AIProviderOrchestrator(
        resolver=resolver,
        session=db_session,
        default_provider="google_gemini",
    )

    with pytest.raises(UnknownModelError) as exc_info:
        orchestrator.generate(GenerationRequest(prompt="test", model="gemini-nonexistent-99"))

    assert exc_info.value.status_code == 404


def test_orchestrator_rejects_disabled_model(db_session: Session) -> None:
    """Orchestrator rejects disabled models under active provider (403)."""
    model_record = db_session.scalar(
        select(AIModel).where(AIModel.model_identifier == "gemini-3.8-flash")
    )
    assert model_record is not None
    model_record.is_enabled = False
    db_session.flush()

    resolver = ProviderRuntimeResolver()
    resolver.register_adapter(GeminiProviderAdapter(MagicMock()))
    orchestrator = AIProviderOrchestrator(
        resolver=resolver,
        session=db_session,
        default_provider="google_gemini",
    )

    with pytest.raises(DisabledModelError) as exc_info:
        orchestrator.generate(GenerationRequest(prompt="test", model="gemini-3.8-flash"))

    assert exc_info.value.status_code == 403


def test_orchestrator_rejects_groq_planned_provider_traffic(db_session: Session) -> None:
    """Groq adapter is registered, but its catalogue status is PLANNED, so it fails closed (403)."""
    resolver = ProviderRuntimeResolver()
    groq_adapter = GroqProviderAdapter()
    resolver.register_adapter(groq_adapter)

    orchestrator = AIProviderOrchestrator(
        resolver=resolver,
        session=db_session,
        default_provider="groq",
    )

    with pytest.raises(ProviderTrafficNotPermittedError) as exc_info:
        orchestrator.generate(GenerationRequest(prompt="Hello Groq"))

    assert exc_info.value.status_code == 403
    assert "PLANNED" in str(exc_info.value)
    assert "not permitted for customer traffic" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 3. Strict Refusal of Silent Fallback on Failure
# ---------------------------------------------------------------------------

def test_orchestrator_refuses_silent_fallback_on_provider_error(db_session: Session) -> None:
    """When the requested provider encounters an error, orchestrator propagates it immediately without silent fallback."""
    fake_gemini = FakeClient("gemini-key", outcome=errors.APIError(503, "Gemini capacity exhausted"))
    gemini_svc = GeminiService(make_test_settings(), client_factory=lambda **kw: fake_gemini)
    gemini_adapter = GeminiProviderAdapter(gemini_svc)

    groq_mock = MagicMock()
    groq_mock.provider_code = "groq"

    resolver = ProviderRuntimeResolver()
    resolver.register_adapter(gemini_adapter)
    resolver.register_adapter(groq_mock)

    orchestrator = AIProviderOrchestrator(
        resolver=resolver,
        session=db_session,
        default_provider="google_gemini",
    )

    with pytest.raises(AIProviderError) as exc_info:
        orchestrator.generate_text("Question that fails on Gemini")

    # Error was raised directly
    assert exc_info.value.provider_code == "google_gemini"
    # Groq was NEVER silently invoked
    assert groq_mock.generate.call_count == 0


def test_orchestrator_refuses_silent_fallback_on_timeout(db_session: Session) -> None:
    """When a provider request times out, orchestrator raises AIProviderTimeoutError without secondary retry."""
    fake_gemini = FakeClient("gemini-key", outcome=httpx.ReadTimeout("Socket read timed out"))
    gemini_svc = GeminiService(make_test_settings(), client_factory=lambda **kw: fake_gemini)
    gemini_adapter = GeminiProviderAdapter(gemini_svc)

    resolver = ProviderRuntimeResolver()
    resolver.register_adapter(gemini_adapter)

    orchestrator = AIProviderOrchestrator(
        resolver=resolver,
        session=db_session,
        default_provider="google_gemini",
    )

    with pytest.raises(AIProviderTimeoutError) as exc_info:
        orchestrator.generate_text("Prompt causing timeout")

    assert exc_info.value.provider_code == "google_gemini"
    assert exc_info.value.is_retryable is True


# ---------------------------------------------------------------------------
# 4. Customer Personal Key Propagation & Telemetry Verification
# ---------------------------------------------------------------------------

def test_orchestrator_telemetry_recorded_without_prompt_or_secrets(db_session: Session) -> None:
    """Orchestrator telemetry stores token usage and latency without prompt text or API keys."""
    usage_mock = SimpleNamespace(
        prompt_token_count=15,
        candidates_token_count=30,
        total_token_count=45,
    )
    fake = FakeClient("test-key", outcome="Safe response", usage_metadata=usage_mock)
    service = GeminiService(make_test_settings(), client_factory=lambda **kw: fake)
    adapter = GeminiProviderAdapter(service)
    resolver = ProviderRuntimeResolver()
    resolver.register_adapter(adapter)

    corr_id = f"corr-sec-test-{uuid4()}"
    orchestrator = AIProviderOrchestrator(
        resolver=resolver,
        session=db_session,
        default_provider="google_gemini",
        correlation_id=corr_id,
    )

    orchestrator.generate(GenerationRequest(prompt="Sensitive prompt text with internal secrets 12345"))

    event = db_session.scalar(
        select(GeminiUsageEvent).where(GeminiUsageEvent.correlation_id == corr_id)
    )
    assert event is not None
    assert event.provider == "google_gemini"
    assert event.status == "SUCCESS"
    assert event.input_tokens == 15
    assert event.output_tokens == 30
    assert event.total_tokens == 45
    assert event.latency_ms is not None and event.latency_ms > 0

    # Verify no prompt text in any column
    for col in event.__table__.columns:
        val = getattr(event, col.name)
        if isinstance(val, str):
            assert "Sensitive prompt" not in val
            assert "12345" not in val


def test_orchestrator_empty_prompt_validation(db_session: Session) -> None:
    """Orchestrator rejects empty or whitespace-only prompts with AIProviderBadRequestError (400)."""
    resolver = ProviderRuntimeResolver()
    resolver.register_adapter(GeminiProviderAdapter(MagicMock()))
    orchestrator = AIProviderOrchestrator(
        resolver=resolver,
        session=db_session,
        default_provider="google_gemini",
    )

    with pytest.raises(AIProviderBadRequestError) as exc_info:
        orchestrator.generate_text("   ")

    assert exc_info.value.status_code == 400
    assert "Prompt must not be empty" in str(exc_info.value)


def test_orchestrator_empty_response_text_raises_response_error(db_session: Session) -> None:
    """Orchestrator raises AIProviderResponseError if provider returns empty text."""
    fake = FakeClient("test-key", outcome="")
    service = GeminiService(make_test_settings(), client_factory=lambda **kw: fake)
    adapter = GeminiProviderAdapter(service)
    resolver = ProviderRuntimeResolver()
    resolver.register_adapter(adapter)

    orchestrator = AIProviderOrchestrator(
        resolver=resolver,
        session=db_session,
        default_provider="google_gemini",
    )

    with pytest.raises(AIProviderResponseError) as exc_info:
        orchestrator.generate_text("Generate something")

    assert exc_info.value.provider_code == "google_gemini"
    assert exc_info.value.error_category in ("EMPTY_RESPONSE", "RESPONSE_ERROR")


def test_orchestrator_normalizes_upstream_rate_limit(db_session: Session) -> None:
    """Orchestrator normalizes upstream 429 errors into AIProviderRateLimitError."""
    fake = FakeClient("test-key", outcome=errors.APIError(429, "Resource exhausted: quota exceeded"))
    service = GeminiService(make_test_settings(), client_factory=lambda **kw: fake)
    adapter = GeminiProviderAdapter(service)
    resolver = ProviderRuntimeResolver()
    resolver.register_adapter(adapter)

    orchestrator = AIProviderOrchestrator(
        resolver=resolver,
        session=db_session,
        default_provider="google_gemini",
    )

    with pytest.raises(AIProviderError) as exc_info:
        orchestrator.generate(GenerationRequest(prompt="Hello"))

    assert exc_info.value.status_code == 429
    assert exc_info.value.error_category in ("RATE_LIMIT", "QUOTA_EXHAUSTED")
