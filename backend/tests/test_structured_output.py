from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from google.genai import errors, types
from pydantic import BaseModel, Field, SecretStr, create_model
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.core.config import Settings
from backend.app.db.base import Base
from backend.app.db.models import (
    AIModel,
    AIProvider,
    GeminiUsageEvent,
    Product,
    ProductCategory,
    User,
    UserGeminiSettings,
)
from backend.app.db.seed_ai_catalogue import seed_ai_provider_catalogue
from backend.app.security.key_encryption import encrypt_user_key
from backend.app.schemas.shopping import ShoppingIntent, ShoppingSearchRequest
from backend.app.services.gemini_service import (
    GeminiResult,
    GeminiService,
    GeminiTimeoutError,
)
from backend.app.services.personal_gemini_runtime import RequestScopedGeminiService
from backend.app.services.providers.base import (
    AIProviderBadRequestError,
    AIProviderError,
    AIProviderResponseError,
    AIProviderSchemaError,
    AIProviderTimeoutError,
    AIProviderValidationError,
    GenerationRequest,
    GenerationResponse,
    TokenUsage,
    parse_and_validate_structured_output,
    validate_structured_schema,
)
from backend.app.services.providers.gemini_adapter import GeminiProviderAdapter
from backend.app.services.providers.groq_adapter import GroqProviderAdapter
from backend.app.services.providers.orchestrator import AIProviderOrchestrator
from backend.app.services.providers.resolver import (
    ProviderRuntimeResolver,
    ProviderTrafficNotPermittedError,
)
from backend.app.services.shopping_agent import ShoppingAgent, _fallback_extract_intent

TEST_SECRET = SecretStr("test-encryption-secret-32-bytes-long-12345")


# ---------------------------------------------------------------------------
# Test Schemas
# ---------------------------------------------------------------------------

class SampleItem(BaseModel):
    name: str
    price: float
    tags: list[str] = Field(default_factory=list)


class NestedLevel2(BaseModel):
    value: str


class NestedLevel1(BaseModel):
    child: NestedLevel2


class NestedRoot(BaseModel):
    item: NestedLevel1


# ---------------------------------------------------------------------------
# Fixtures & Mocks
# ---------------------------------------------------------------------------

@pytest.fixture
def db_session_factory() -> Any:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as session:
        seed_ai_provider_catalogue(session)
        session.commit()
    yield factory
    engine.dispose()


@pytest.fixture
def db_session(db_session_factory: Any) -> Any:
    with db_session_factory() as session:
        yield session


class FakeGenAIClient:
    def __init__(self, key: str, outcome: Any = "{\"name\": \"keyboard\", \"price\": 49.99, \"tags\": [\"tech\"]}") -> None:
        self.key = key
        self.outcome = outcome
        self.calls: list[dict[str, Any]] = []

    @property
    def models(self) -> FakeGenAIClient:
        return self

    def generate_content(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        usage = types.GenerateContentResponseUsageMetadata(
            prompt_token_count=15,
            candidates_token_count=25,
            total_token_count=40,
        )
        candidate = types.Candidate(
            content=types.Content(
                parts=[types.Part.from_text(text=str(self.outcome))]
            )
        )
        return types.GenerateContentResponse(
            candidates=[candidate],
            usage_metadata=usage,
        )


# ---------------------------------------------------------------------------
# 1. Schema Validation Unit Tests
# ---------------------------------------------------------------------------

def test_validate_structured_schema_valid() -> None:
    res = validate_structured_schema(SampleItem)
    assert res is SampleItem


def test_validate_structured_schema_rejects_non_pydantic() -> None:
    class PlainClass:
        pass

    with pytest.raises(AIProviderSchemaError) as exc_info:
        validate_structured_schema(PlainClass)
    assert "expected a subclass of pydantic.BaseModel" in str(exc_info.value)

    with pytest.raises(AIProviderSchemaError):
        validate_structured_schema({"key": "value"})

    with pytest.raises(AIProviderSchemaError):
        validate_structured_schema(123)


def test_validate_structured_schema_field_limit() -> None:
    fields = {f"field_{i}": (str, "default") for i in range(55)}
    OversizedModel = create_model("OversizedModel", **fields)

    with pytest.raises(AIProviderSchemaError) as exc_info:
        validate_structured_schema(OversizedModel, max_fields=50)
    assert "exceeds maximum allowed field count of 50" in str(exc_info.value)


def test_validate_structured_schema_nesting_depth_limit() -> None:
    class Level6(BaseModel):
        val: str

    class Level5(BaseModel):
        child: Level6

    class Level4(BaseModel):
        child: Level5

    class Level3(BaseModel):
        child: Level4

    class Level2(BaseModel):
        child: Level3

    class Level1(BaseModel):
        child: Level2

    with pytest.raises(AIProviderSchemaError) as exc_info:
        validate_structured_schema(Level1, max_depth=5)
    assert "exceeds maximum allowed nesting depth of 5" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 2. Output Parsing & Validation Tests
# ---------------------------------------------------------------------------

def test_parse_and_validate_structured_output_valid() -> None:
    raw = "{\"name\": \"Headphones\", \"price\": 99.5, \"tags\": [\"audio\", \"wireless\"]}"
    parsed = parse_and_validate_structured_output(raw, SampleItem)
    assert isinstance(parsed, SampleItem)
    assert parsed.name == "Headphones"
    assert parsed.price == 99.5
    assert parsed.tags == ["audio", "wireless"]


def test_parse_and_validate_structured_output_markdown_fences() -> None:
    raw = "```json\n{\"name\": \"Mouse\", \"price\": 29.99, \"tags\": []}\n```"
    parsed = parse_and_validate_structured_output(raw, SampleItem)
    assert parsed.name == "Mouse"
    assert parsed.price == 29.99


def test_parse_and_validate_structured_output_invalid_json() -> None:
    raw = "{broken json here"
    with pytest.raises(AIProviderValidationError) as exc_info:
        parse_and_validate_structured_output(raw, SampleItem)
    assert exc_info.value.error_category == "VALIDATION_ERROR"
    assert "malformed JSON" in str(exc_info.value)


def test_parse_and_validate_structured_output_empty() -> None:
    with pytest.raises(AIProviderResponseError):
        parse_and_validate_structured_output("", SampleItem)

    with pytest.raises(AIProviderResponseError):
        parse_and_validate_structured_output(None, SampleItem)


def test_parse_and_validate_structured_output_non_dict() -> None:
    with pytest.raises(AIProviderValidationError) as exc_info:
        parse_and_validate_structured_output("[\"apple\", \"banana\"]", SampleItem)
    assert "Expected JSON object (dict)" in str(exc_info.value)


def test_parse_and_validate_structured_output_missing_required_fields() -> None:
    raw = "{\"name\": \"Incomplete Item\"}"  # price is required
    with pytest.raises(AIProviderValidationError) as exc_info:
        parse_and_validate_structured_output(raw, SampleItem)
    assert "validation failed" in str(exc_info.value)


def test_parse_and_validate_structured_output_unexpected_fields_strict() -> None:
    raw = "{\"name\": \"Item\", \"price\": 10.0, \"tags\": [], \"unauthorized_extra\": 123}"
    with pytest.raises(AIProviderValidationError) as exc_info:
        parse_and_validate_structured_output(raw, SampleItem, strict=True)
    assert "Unexpected fields in structured output under strict validation" in str(exc_info.value)


def test_parse_and_validate_structured_output_unexpected_fields_non_strict() -> None:
    raw = "{\"name\": \"Item\", \"price\": 10.0, \"tags\": [], \"extra_field\": 999}"
    parsed = parse_and_validate_structured_output(raw, SampleItem, strict=False)
    assert parsed.name == "Item"
    assert parsed.price == 10.0


# ---------------------------------------------------------------------------
# 3. Groq Capability Safety Tests
# ---------------------------------------------------------------------------

def test_groq_capabilities_supports_structured_output_false() -> None:
    adapter = GroqProviderAdapter(
        api_key=SecretStr("gsk-test-fake-key-1234"),
        allow_localhost_for_testing=True,
    )
    caps = adapter.get_capabilities()
    assert caps.supports_structured_output is False


def test_groq_adapter_generate_rejects_schema() -> None:
    adapter = GroqProviderAdapter(
        api_key=SecretStr("gsk-test-fake-key-1234"),
        allow_localhost_for_testing=True,
    )
    req = GenerationRequest(
        prompt="Output items",
        response_schema=SampleItem,
        model="llama-3.1-8b-instant",
    )
    with pytest.raises(AIProviderBadRequestError) as exc_info:
        adapter.generate(req)
    assert "Structured output is not supported by Groq" in str(exc_info.value)


def test_orchestrator_generate_structured_groq_rejection(db_session: Session) -> None:
    resolver = ProviderRuntimeResolver()
    adapter = GroqProviderAdapter(
        api_key=SecretStr("gsk-test-fake-key-1234"),
        allow_localhost_for_testing=True,
    )
    resolver.register_adapter(adapter)
    orchestrator = AIProviderOrchestrator(
        resolver=resolver,
        session=db_session,
        default_provider="groq",
    )
    # Groq is PLANNED -> fails with ProviderTrafficNotPermittedError or AIProviderBadRequestError
    with pytest.raises((ProviderTrafficNotPermittedError, AIProviderBadRequestError)):
        orchestrator.generate_structured("test", SampleItem, provider="groq")


# ---------------------------------------------------------------------------
# 4. Orchestrator & Gemini Structured Generation Tests
# ---------------------------------------------------------------------------

def test_orchestrator_generate_structured_gemini_success(db_session: Session) -> None:
    settings = Settings(
        secret_key=TEST_SECRET,
        database_url="sqlite+pysqlite:///:memory:",
        gemini_api_key=SecretStr("mock-gemini-key"),
    )
    fake_client = FakeGenAIClient("mock-gemini-key", outcome="{\"name\": \"Tablet\", \"price\": 299.0, \"tags\": [\"mobile\"]}")
    gemini_svc = GeminiService(
        settings,
        client_factory=lambda **kw: fake_client,
    )
    gemini_adapter = GeminiProviderAdapter(gemini_svc, settings=settings)

    resolver = ProviderRuntimeResolver()
    resolver.register_adapter(gemini_adapter)

    orchestrator = AIProviderOrchestrator(
        resolver=resolver,
        session=db_session,
        default_provider="google_gemini",
        default_model="gemini-2.5-flash",
    )

    res = orchestrator.generate_structured(
        "Find tablet",
        SampleItem,
        strict_schema=True,
    )
    assert isinstance(res, SampleItem)
    assert res.name == "Tablet"
    assert res.price == 299.0
    assert res.tags == ["mobile"]


def test_orchestrator_structured_gemini_personal_key(
    db_session: Session,
    db_session_factory: Any,
) -> None:
    settings = Settings(
        user_key_encryption_secret=TEST_SECRET,
        database_url=SecretStr("sqlite+pysqlite:///:memory:"),
        gemini_api_key=SecretStr("company-fallback-key"),
    )

    user = User(
        id=uuid4(),
        email="personal@example.com",
        password_hash="fakehash",
        role="customer",
        is_active=True,
    )
    encrypted_key = encrypt_user_key("AIzaSyPersonalKey12345", TEST_SECRET)
    user_settings = UserGeminiSettings(
        user_id=user.id,
        encrypted_api_key=encrypted_key,
        key_hint="...12345",
        status="ACTIVE",
    )
    db_session.add(user)
    db_session.add(user_settings)
    db_session.commit()

    personal_client = FakeGenAIClient("personal-key", outcome="{\"name\": \"Monitor\", \"price\": 199.99, \"tags\": [\"display\"]}")
    company_client = FakeGenAIClient("company-key", outcome="{\"name\": \"Company\", \"price\": 1.0, \"tags\": []}")

    platform_svc = GeminiService(settings, client_factory=lambda **kw: company_client)
    scoped_svc = RequestScopedGeminiService(
        platform_svc,
        settings,
        user=user,
        session_factory=db_session_factory,
        client_factory=lambda **kw: personal_client,
    )

    gemini_adapter = GeminiProviderAdapter(scoped_svc, settings=settings)
    resolver = ProviderRuntimeResolver()
    resolver.register_adapter(gemini_adapter)

    orchestrator = AIProviderOrchestrator(
        resolver=resolver,
        session=db_session,
        default_provider="google_gemini",
        default_model="gemini-2.5-flash",
    )

    item = orchestrator.generate_structured("Search monitor", SampleItem)
    assert item.name == "Monitor"
    assert len(personal_client.calls) == 1
    assert len(company_client.calls) == 0

    # Verify exactly-once telemetry
    with db_session_factory() as sess:
        events = sess.execute(select(GeminiUsageEvent)).scalars().all()
        assert len(events) == 1
        assert events[0].key_source == "PERSONAL"
        assert events[0].status == "SUCCESS"


def test_orchestrator_structured_gemini_personal_key_quota_fallback(
    db_session: Session,
    db_session_factory: Any,
) -> None:
    settings = Settings(
        user_key_encryption_secret=TEST_SECRET,
        database_url=SecretStr("sqlite+pysqlite:///:memory:",),
        gemini_api_key=SecretStr("company-fallback-key"),
    )

    user = User(
        id=uuid4(),
        email="quota_user@example.com",
        password_hash="fakehash",
        role="customer",
        is_active=True,
    )
    encrypted_key = encrypt_user_key("AIzaSyPersonalKey99999", TEST_SECRET)
    user_settings = UserGeminiSettings(
        user_id=user.id,
        encrypted_api_key=encrypted_key,
        key_hint="...99999",
        status="ACTIVE",
    )
    db_session.add(user)
    db_session.add(user_settings)
    db_session.commit()

    # Personal client raises 429 quota exhaustion
    quota_error = errors.APIError(429, "Quota exceeded (resource exhausted)")
    personal_client = FakeGenAIClient("personal-key", outcome=quota_error)
    company_client = FakeGenAIClient("company-key", outcome="{\"name\": \"Fallback Laptop\", \"price\": 799.0, \"tags\": [\"pc\"]}")

    platform_svc = GeminiService(settings, client_factory=lambda **kw: company_client)
    scoped_svc = RequestScopedGeminiService(
        platform_svc,
        settings,
        user=user,
        session_factory=db_session_factory,
        client_factory=lambda **kw: personal_client,
    )

    gemini_adapter = GeminiProviderAdapter(scoped_svc, settings=settings)
    resolver = ProviderRuntimeResolver()
    resolver.register_adapter(gemini_adapter)

    orchestrator = AIProviderOrchestrator(
        resolver=resolver,
        session=db_session,
        default_provider="google_gemini",
        default_model="gemini-2.5-flash",
    )

    item = orchestrator.generate_structured("Search laptop", SampleItem)
    assert item.name == "Fallback Laptop"
    assert len(personal_client.calls) == 1
    assert len(company_client.calls) == 1

    # Verify telemetry shows personal attempt failure + company attempt success
    with db_session_factory() as sess:
        events = sess.execute(select(GeminiUsageEvent).order_by(GeminiUsageEvent.attempt_number)).scalars().all()
        assert len(events) == 2
        assert events[0].key_source == "PERSONAL"
        assert events[0].status == "COOLDOWN"
        assert events[1].key_source == "COMPANY"
        assert events[1].status == "SUCCESS"


# ---------------------------------------------------------------------------
# 5. Shopping Intent Extraction & ShoppingAgent Migration Tests
# ---------------------------------------------------------------------------

def test_shopping_agent_extract_intent_via_orchestrator(db_session: Session) -> None:
    intent_json = json.dumps({
        "keywords": ["wireless", "gaming", "mouse"],
        "category": "Electronics",
        "min_budget": 20.0,
        "max_budget": 80.0,
        "is_shopping_intent": True,
        "is_ambiguous_or_insufficient": False,
        "clarification_message": None,
    })
    settings = Settings(
        secret_key=TEST_SECRET,
        database_url="sqlite+pysqlite:///:memory:",
        gemini_api_key=SecretStr("mock-key"),
    )
    fake_client = FakeGenAIClient("mock-key", outcome=intent_json)
    gemini_svc = GeminiService(settings, client_factory=lambda **kw: fake_client)
    gemini_adapter = GeminiProviderAdapter(gemini_svc, settings=settings)

    resolver = ProviderRuntimeResolver()
    resolver.register_adapter(gemini_adapter)

    orchestrator = AIProviderOrchestrator(
        resolver=resolver,
        session=db_session,
        default_provider="google_gemini",
        default_model="gemini-2.5-flash",
    )

    agent = ShoppingAgent(ai_service=orchestrator, catalogue_currency="USD")
    intent = agent.extract_intent("wireless gaming mouse between $20 and $80")

    assert intent.is_shopping_intent is True
    assert "wireless" in intent.keywords
    assert "mouse" in intent.keywords
    assert intent.min_budget == 20.0
    assert intent.max_budget == 80.0


def test_shopping_agent_extract_intent_orchestrator_failure_fallback(db_session: Session) -> None:
    settings = Settings(
        secret_key=TEST_SECRET,
        database_url="sqlite+pysqlite:///:memory:",
        gemini_api_key=SecretStr("mock-key"),
    )
    # Orchestrator client raises timeout
    fake_client = FakeGenAIClient("mock-key", outcome=TimeoutError("Gemini timed out"))
    gemini_svc = GeminiService(settings, client_factory=lambda **kw: fake_client)
    gemini_adapter = GeminiProviderAdapter(gemini_svc, settings=settings)

    resolver = ProviderRuntimeResolver()
    resolver.register_adapter(gemini_adapter)

    orchestrator = AIProviderOrchestrator(
        resolver=resolver,
        session=db_session,
        default_provider="google_gemini",
        default_model="gemini-2.5-flash",
    )

    agent = ShoppingAgent(ai_service=orchestrator, catalogue_currency="USD")
    # Should not raise; deterministically fall back
    intent = agent.extract_intent("running shoes under $120")

    assert intent.is_shopping_intent is True
    assert intent.max_budget == 120.0
    assert "shoes" in intent.keywords or "running" in intent.keywords


def test_shopping_agent_backward_compatibility_with_text_generator() -> None:
    intent_json = json.dumps({
        "keywords": ["leather", "boots"],
        "category": "Footwear",
        "min_budget": None,
        "max_budget": 150.0,
        "is_shopping_intent": True,
        "is_ambiguous_or_insufficient": False,
        "clarification_message": None,
    })

    class LegacyService:
        def answer(self, prompt: str) -> str:
            return intent_json

    agent = ShoppingAgent(gemini_service=LegacyService(), catalogue_currency="USD")
    intent = agent.extract_intent("leather boots under $150")

    assert intent.is_shopping_intent is True
    assert intent.max_budget == 150.0
    assert "boots" in intent.keywords
