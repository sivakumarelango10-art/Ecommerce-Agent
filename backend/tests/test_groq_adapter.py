from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch
from uuid import uuid4

import httpx
import pytest
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
    PlatformAIKey,
    PlatformKeyStatus,
    ProviderConnectionStatus,
)
from backend.app.db.seed_ai_catalogue import seed_ai_provider_catalogue
from backend.app.security.key_encryption import (
    encrypt_platform_key,
    encrypt_user_key,
)
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
from backend.app.services.providers.groq_adapter import (
    DEFAULT_GROQ_BASE_URL,
    DEFAULT_GROQ_MODEL,
    GROQ_MODEL_REGISTRY,
    GROQ_PROVIDER_CODE,
    SUPPORTED_GROQ_CHAT_MODELS,
    SUPPORTED_GROQ_MODELS,
    GroqKeySlot,
    GroqModelSpec,
    GroqModelStatus,
    GroqModelTask,
    GroqProviderAdapter,
    _extract_groq_cooldown_and_limit_type,
    _parse_cooldown_header,
    _sanitize_groq_error_message,
)
from backend.app.services.providers.resolver import (
    DisabledModelError,
    DisabledProviderError,
    ProviderAdapterUnavailableError,
    ProviderRuntimeResolver,
    ProviderTrafficNotPermittedError,
    UnknownModelError,
    UnknownProviderError,
)
from backend.app.services.providers.security import (
    MAX_SAFE_RESPONSE_BYTES,
    AIProviderSSRFError,
    build_safe_provider_http_client,
    is_trusted_provider_host,
    mask_sensitive_token,
)
from backend.app.services.providers.telemetry import NormalizedTelemetryRecord

logger = logging.getLogger(__name__)

TEST_PLATFORM_SECRET = SecretStr("test-platform-encryption-secret-32-bytes-long-12345")


# ---------------------------------------------------------------------------
# Test Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db_session() -> Any:
    """Create fresh in-memory SQLite database session with seeded catalogue."""
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


def make_mock_groq_client(
    handler: Any,
    allow_localhost_for_testing: bool = True,
) -> httpx.Client:
    """Construct a safe provider client backed by a MockTransport for hermetic testing."""
    transport = httpx.MockTransport(handler)
    return build_safe_provider_http_client(
        transport=transport,
        allow_localhost_for_testing=allow_localhost_for_testing,
    )


def resolve_for_test_harness(
    resolver: ProviderRuntimeResolver,
    session: Session,
    provider_code: str,
    model_name: str | None = None,
) -> tuple[AIProviderAdapter, AIModel | None]:
    """Test helper isolated from production ProviderRuntimeResolver class.

    Retrieves the registered candidate adapter for offline test harness execution
    without requiring the provider to be promoted to 'AVAILABLE' in the production catalogue.
    """
    adapter = resolver.get_adapter(provider_code)
    if adapter is None:
        raise ProviderAdapterUnavailableError(provider_code)
    return adapter, None


async def resolve_async_for_test_harness(
    resolver: ProviderRuntimeResolver,
    session: Any,
    provider_code: str,
    model_name: str | None = None,
) -> tuple[AIProviderAdapter, AIModel | None]:
    """Async test helper isolated from production ProviderRuntimeResolver class."""
    adapter = resolver.get_adapter(provider_code)
    if adapter is None:
        raise ProviderAdapterUnavailableError(provider_code)
    return adapter, None


# ---------------------------------------------------------------------------
# 1. Interface & Capabilities Contract
# ---------------------------------------------------------------------------

def test_groq_adapter_implements_ai_provider_adapter() -> None:
    """GroqProviderAdapter correctly subclasses and implements AIProviderAdapter."""
    mock_client = make_mock_groq_client(lambda req: httpx.Response(200, json={}))
    adapter = GroqProviderAdapter(
        api_key="gsk-test-fake-key-material-1234",
        http_client=mock_client,
        allow_localhost_for_testing=True,
    )

    assert isinstance(adapter, AIProviderAdapter)
    assert adapter.provider_code == "groq"
    assert adapter.display_name == "Groq"
    assert adapter.base_url == "https://api.groq.com/openai/v1"
    assert adapter.default_model == "openai/gpt-oss-120b"

    capabilities = adapter.get_capabilities()
    assert isinstance(capabilities, ProviderCapabilities)
    assert capabilities.supports_text is True
    assert capabilities.supports_structured_output is True
    assert capabilities.supports_function_calling is True
    assert capabilities.supports_streaming is False
    assert capabilities.supports_vision is False
    assert capabilities.max_context_window == 131072
    assert capabilities.max_output_tokens == 65536


# ---------------------------------------------------------------------------
# 2. Model Allowlist & Capability Registry (No Prefix Matching)
# ---------------------------------------------------------------------------

def test_groq_adapter_supports_exact_production_chat_models() -> None:
    """supports_model only returns True for exact documented production chat models."""
    mock_client = make_mock_groq_client(lambda req: httpx.Response(200, json={}))
    adapter = GroqProviderAdapter(
        api_key="gsk-test-key-5678",
        http_client=mock_client,
        allow_localhost_for_testing=True,
    )

    # Documented current production chat models MUST succeed
    assert adapter.supports_model("openai/gpt-oss-120b") is True
    assert adapter.supports_model("openai/gpt-oss-20b") is True

    # Case & whitespace normalization:
    assert adapter.supports_model("OPENAI/GPT-OSS-120B") is True
    assert adapter.supports_model("  openai/gpt-oss-20b  ") is True

    # Enterprise-restricted, audio, preview, and deprecated models return False
    assert adapter.supports_model("llama-3.3-70b-versatile") is False
    assert adapter.supports_model("llama-3.1-8b-instant") is False
    assert adapter.supports_model("mixtral-8x7b-32768") is False
    assert adapter.supports_model("gemma2-9b-it") is False


def test_groq_gpt_oss_models_registry_specifications() -> None:
    """GPT-OSS models conform to documented specifications as of Nov 25, 2026."""
    spec_120b = GROQ_MODEL_REGISTRY.get("openai/gpt-oss-120b")
    assert spec_120b is not None
    assert spec_120b.task == GroqModelTask.CHAT_COMPLETION
    assert spec_120b.status == GroqModelStatus.PRODUCTION
    assert spec_120b.context_window == 131072
    assert spec_120b.max_output_tokens == 65536
    assert spec_120b.access_class == "PAY_AS_YOU_GO"

    spec_20b = GROQ_MODEL_REGISTRY.get("openai/gpt-oss-20b")
    assert spec_20b is not None
    assert spec_20b.task == GroqModelTask.CHAT_COMPLETION
    assert spec_20b.status == GroqModelStatus.PRODUCTION
    assert spec_20b.context_window == 131072
    assert spec_20b.max_output_tokens == 65536
    assert spec_20b.access_class == "PAY_AS_YOU_GO"


def test_groq_enterprise_restricted_models_fail_closed_unconditionally() -> None:
    """Enterprise-restricted models fail closed unconditionally; client-supplied flags are rejected."""
    mock_client = make_mock_groq_client(lambda req: httpx.Response(200, json={}))
    adapter = GroqProviderAdapter(
        api_key="gsk-test-key",
        http_client=mock_client,
        allow_localhost_for_testing=True,
    )

    restricted_models = ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"]

    for model_id in restricted_models:
        # supports_model must return False
        assert adapter.supports_model(model_id) is False

        # 1. Missing entitlement (no metadata)
        with pytest.raises(AIProviderBadRequestError) as exc_missing:
            adapter.generate(GenerationRequest(prompt="Test", model=model_id))
        assert exc_missing.value.status_code == 400
        assert "enterprise-restricted" in str(exc_missing.value).lower()
        assert "no verified contractual committed-spend enterprise entitlement" in str(exc_missing.value).lower()

        # 2. False entitlement metadata
        with pytest.raises(AIProviderBadRequestError) as exc_false:
            adapter.generate(
                GenerationRequest(
                    prompt="Test",
                    model=model_id,
                    metadata={"is_enterprise_tier": False},
                )
            )
        assert exc_false.value.status_code == 400
        assert "enterprise-restricted" in str(exc_false.value).lower()

        # 3. Client passing is_enterprise_tier=True in request data (spoof attempt fails closed)
        with pytest.raises(AIProviderBadRequestError) as exc_spoof:
            adapter.generate(
                GenerationRequest(
                    prompt="Test",
                    model=model_id,
                    metadata={"is_enterprise_tier": True},
                )
            )
        assert exc_spoof.value.status_code == 400
        assert "enterprise-restricted" in str(exc_spoof.value).lower()

        # 4. Client passing string truthy or role-based bypass attempts
        with pytest.raises(AIProviderBadRequestError) as exc_spoof_str:
            adapter.generate(
                GenerationRequest(
                    prompt="Test",
                    model=model_id,
                    metadata={"is_enterprise_tier": "true", "role": "admin", "plan": "enterprise"},
                )
            )
        assert exc_spoof_str.value.status_code == 400
        assert "enterprise-restricted" in str(exc_spoof_str.value).lower()

        # 5. Unknown entitlement state
        with pytest.raises(AIProviderBadRequestError) as exc_unknown:
            adapter.generate(
                GenerationRequest(
                    prompt="Test",
                    model=model_id,
                    metadata={"contract_status": "COMMITTED_SPEND", "tier": "ENTERPRISE"},
                )
            )
        assert exc_unknown.value.status_code == 400
        assert "enterprise-restricted" in str(exc_unknown.value).lower()


def test_ordinary_customer_cannot_use_enterprise_restricted_models() -> None:
    """Ordinary customer traffic cannot invoke enterprise-restricted models."""
    mock_client = make_mock_groq_client(lambda req: httpx.Response(200, json={}))
    adapter = GroqProviderAdapter(
        api_key="gsk-test-key",
        http_client=mock_client,
        allow_localhost_for_testing=True,
    )

    # Customer request with normal customer prompt and no special privileges
    with pytest.raises(AIProviderBadRequestError) as exc_info:
        adapter.generate(
            GenerationRequest(
                prompt="Customer inquiry about order status",
                model="llama-3.3-70b-versatile",
            )
        )
    assert exc_info.value.status_code == 400
    assert "enterprise-restricted" in str(exc_info.value).lower()


def test_groq_deprecated_models_rejected_with_explicit_message() -> None:
    """Deprecated and decommissioned models are rejected with explicit deprecation messages."""
    mock_client = make_mock_groq_client(lambda req: httpx.Response(200, json={}))
    adapter = GroqProviderAdapter(
        api_key="gsk-test-key",
        http_client=mock_client,
        allow_localhost_for_testing=True,
    )

    deprecated_candidates = [
        "deepseek-r1-distill-llama-70b",  # Shutdown Oct 2, 2025
        "mixtral-8x7b-32768",
        "gemma2-9b-it",
        "qwen-qwq-32b",
        "distil-whisper-large-v3-en",
        "llama-guard-3-8b",
        "llama-3.2-1b-preview",
        "llama-3.2-3b-preview",
        "llama-3.2-11b-vision-preview",
        "llama-3.2-90b-vision-preview",
        "llama-3.2-90b-text-preview",
        "llama3-70b-8192",
        "llama3-8b-8192",
        "llama-3.1-70b-versatile",
    ]

    for model_id in deprecated_candidates:
        assert adapter.supports_model(model_id) is False
        with pytest.raises(AIProviderBadRequestError) as exc_info:
            adapter.generate(GenerationRequest(prompt="Test", model=model_id))
        assert "deprecated" in str(exc_info.value).lower(), f"Expected 'deprecated' in error for {model_id}"


def test_groq_adapter_rejects_audio_whisper_models() -> None:
    """Whisper audio models require audio endpoints and are rejected by chat completion adapter."""
    mock_client = make_mock_groq_client(lambda req: httpx.Response(200, json={}))
    adapter = GroqProviderAdapter(
        api_key="gsk-test-key",
        http_client=mock_client,
        allow_localhost_for_testing=True,
    )

    assert adapter.supports_model("whisper-large-v3") is False
    assert adapter.supports_model("whisper-large-v3-turbo") is False
    assert adapter.supports_model("distil-whisper-large-v3-en") is False

    with pytest.raises(AIProviderBadRequestError) as exc_info:
        adapter.generate(GenerationRequest(prompt="Test", model="whisper-large-v3"))
    assert exc_info.value.status_code == 400
    assert "audio_transcription" in str(exc_info.value)


def test_groq_adapter_rejects_preview_and_deprecated_models() -> None:
    """Preview (if any) and deprecated models are strictly rejected for production chat."""
    mock_client = make_mock_groq_client(lambda req: httpx.Response(200, json={}))
    adapter = GroqProviderAdapter(
        api_key="gsk-test-key",
        http_client=mock_client,
        allow_localhost_for_testing=True,
    )

    # Decommissioned/deprecated models
    for model_id in ["deepseek-r1-distill-llama-70b", "llama-3.2-1b-preview", "llama3-70b-8192"]:
        assert adapter.supports_model(model_id) is False
        with pytest.raises(AIProviderBadRequestError) as exc_info:
            adapter.generate(GenerationRequest(prompt="Test", model=model_id))
        assert "deprecated" in str(exc_info.value).lower()


def test_groq_adapter_rejects_generic_prefixes() -> None:
    """Generic prefix matching is removed; unknown models with familiar prefixes are rejected."""
    mock_client = make_mock_groq_client(lambda req: httpx.Response(200, json={}))
    adapter = GroqProviderAdapter(
        api_key="gsk-test-key",
        http_client=mock_client,
        allow_localhost_for_testing=True,
    )

    assert adapter.supports_model("llama-3.3-custom-variant") is False
    assert adapter.supports_model("llama-unknown") is False
    assert adapter.supports_model("mixtral-future-model") is False
    assert adapter.supports_model("qwen-2.5-72b") is False
    assert adapter.supports_model("gpt-4o") is False
    assert adapter.supports_model("claude-3-5-sonnet") is False
    assert adapter.supports_model("") is False


# ---------------------------------------------------------------------------
# 3. Successful Chat Completion & Parameter Handling
# ---------------------------------------------------------------------------

def test_groq_successful_chat_completion() -> None:
    """Groq adapter completes chat generation and extracts message text and usage."""
    captured_requests: list[httpx.Request] = []

    def mock_handler(req: httpx.Request) -> httpx.Response:
        captured_requests.append(req)
        resp_data = {
            "id": "chatcmpl-test-123",
            "object": "chat.completion",
            "created": 1720000000,
            "model": "openai/gpt-oss-120b",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "This is a response generated by GPT-OSS 120B on Groq.",
                        "tool_calls": None,
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 15,
                "completion_tokens": 12,
                "total_tokens": 27,
            },
        }
        return httpx.Response(200, json=resp_data)

    client = make_mock_groq_client(mock_handler)
    adapter = GroqProviderAdapter(
        api_key="gsk-test-fake-key-material-abcd",
        http_client=client,
        allow_localhost_for_testing=True,
    )

    req = GenerationRequest(
        prompt="Tell me about e-commerce scalability.",
        model="openai/gpt-oss-120b",
        temperature=0.5,
        max_tokens=256,
        correlation_id="corr-groq-001",
    )
    resp = adapter.generate(req)

    assert isinstance(resp, GenerationResponse)
    assert resp.text == "This is a response generated by GPT-OSS 120B on Groq."
    assert resp.model == "openai/gpt-oss-120b"
    assert resp.provider == "groq"
    assert resp.usage is not None
    assert resp.usage.input_tokens == 15
    assert resp.usage.output_tokens == 12
    assert resp.usage.total_tokens == 27
    assert resp.latency_ms > 0

    # Verify documented request fields
    assert len(captured_requests) == 1
    sent_req = captured_requests[0]
    assert sent_req.method == "POST"
    assert sent_req.url.path == "/openai/v1/chat/completions"
    assert sent_req.headers["authorization"] == "Bearer gsk-test-fake-key-material-abcd"

    body = json.loads(sent_req.content.decode("utf-8"))
    assert set(body.keys()).issubset({"model", "messages", "temperature", "max_tokens", "tools", "tool_choice"})
    assert body["model"] == "openai/gpt-oss-120b"
    assert body["temperature"] == 0.5
    assert body["max_tokens"] == 256
    assert body["messages"] == [{"role": "user", "content": "Tell me about e-commerce scalability."}]


def test_groq_generate_text_convenience_method() -> None:
    """generate_text returns plain string response."""
    def mock_handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"index": 0, "message": {"role": "assistant", "content": "Plain text output."}}
                ]
            },
        )

    client = make_mock_groq_client(mock_handler)
    adapter = GroqProviderAdapter(
        api_key="gsk-test-key",
        http_client=client,
        allow_localhost_for_testing=True,
    )
    result = adapter.generate_text("Hello Groq")
    assert result == "Plain text output."


def test_groq_http_503_transient_error_with_bounded_retries() -> None:
    """HTTP 503 transient server error is retried and raises AIProviderTransientError after exhausting attempts."""
    attempts = 0

    def mock_handler(req: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(
            503,
            headers={"retry-after": "0.1"},
            json={"error": {"message": "Service unavailable", "type": "server_error"}},
        )

    client = make_mock_groq_client(mock_handler)
    adapter = GroqProviderAdapter(
        api_key="gsk-test-key",
        http_client=client,
        allow_localhost_for_testing=True,
    )

    with pytest.raises(AIProviderTransientError) as exc_info:
        adapter.generate(GenerationRequest(prompt="Test transient 503"))

    assert exc_info.value.status_code == 503
    assert attempts == 3  # Initial + 2 retries


def test_groq_timeout_retries_and_raises_timeout_error() -> None:
    """Network timeout retries up to max_retries and raises AIProviderTimeoutError."""
    attempts = 0

    def mock_handler(req: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ReadTimeout("Read timed out")

    client = make_mock_groq_client(mock_handler)
    adapter = GroqProviderAdapter(
        api_key="gsk-test-key",
        http_client=client,
        default_timeout_seconds=0.1,
        allow_localhost_for_testing=True,
    )

    with pytest.raises(AIProviderTimeoutError) as exc_info:
        adapter.generate(GenerationRequest(prompt="Test timeout", timeout_seconds=0.1))

    assert "timed out" in str(exc_info.value).lower()
    assert attempts == 3


def test_groq_empty_choices_raises_response_error() -> None:
    """Empty choices list in 200 response raises AIProviderResponseError."""
    def mock_handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": []})

    client = make_mock_groq_client(mock_handler)
    adapter = GroqProviderAdapter(
        api_key="gsk-test-key",
        http_client=client,
        allow_localhost_for_testing=True,
    )

    with pytest.raises(AIProviderResponseError) as exc_info:
        adapter.generate(GenerationRequest(prompt="Test empty choices"))

    assert "no completion choices" in str(exc_info.value).lower()


def test_groq_invalid_or_non_json_body_raises_response_error() -> None:
    """Non-JSON or malformed payload in 200 response raises AIProviderResponseError."""
    def mock_handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<!DOCTYPE html><html>Bad gateway</html>")

    client = make_mock_groq_client(mock_handler)
    adapter = GroqProviderAdapter(
        api_key="gsk-test-key",
        http_client=client,
        allow_localhost_for_testing=True,
    )

    with pytest.raises(AIProviderResponseError) as exc_info:
        adapter.generate(GenerationRequest(prompt="Test non-json"))

    assert "invalid or empty json" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# 4. Tool Calls As Untrusted Model Output (Not Executed)
# ---------------------------------------------------------------------------

def test_groq_tool_calls_parsed_without_execution() -> None:
    """Tool calls are safely parsed and returned as structured data; never executed in adapter."""
    def mock_handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_12345",
                                    "type": "function",
                                    "function": {
                                        "name": "check_inventory",
                                        "arguments": '{"sku": "SKU-999", "quantity": 2}',
                                    },
                                }
                            ],
                        },
                    }
                ]
            },
        )

    client = make_mock_groq_client(mock_handler)
    adapter = GroqProviderAdapter(
        api_key="gsk-test-key",
        http_client=client,
        allow_localhost_for_testing=True,
    )

    resp = adapter.generate(
        GenerationRequest(
            prompt="Check inventory for SKU-999",
            tools=[{"type": "function", "function": {"name": "check_inventory"}}],
        )
    )

    assert resp.text is None
    assert len(resp.function_calls) == 1
    call = resp.function_calls[0]
    assert call["function"]["name"] == "check_inventory"
    assert call["function"]["arguments"] == {"sku": "SKU-999", "quantity": 2}


def test_groq_malformed_tool_arguments_handled_safely() -> None:
    """Malformed JSON in tool arguments is captured safely without throwing an unhandled crash."""
    def mock_handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_malformed",
                                    "type": "function",
                                    "function": {
                                        "name": "broken_tool",
                                        "arguments": "{unparseable: json",
                                    },
                                }
                            ],
                        },
                    }
                ]
            },
        )

    client = make_mock_groq_client(mock_handler)
    adapter = GroqProviderAdapter(
        api_key="gsk-test-key",
        http_client=client,
        allow_localhost_for_testing=True,
    )

    resp = adapter.generate(GenerationRequest(prompt="Test"))
    assert len(resp.function_calls) == 1
    args = resp.function_calls[0]["function"]["arguments"]
    assert "_parse_error" in args
    assert args["_raw_arguments"] == "{unparseable: json"


# ---------------------------------------------------------------------------
# 5. Key Selection, Precedence & Gemini Key Protection
# ---------------------------------------------------------------------------

def test_groq_ignores_personal_user_key_to_prevent_cross_contamination() -> None:
    """Personal user keys (e.g. from Gemini) are ignored by Groq to prevent credential contamination."""
    company_key = SecretStr("gsk-test-company-key-valid")
    gemini_user_key = "AIzaSyFakeGeminiPersonalKey12345"

    auth_headers: list[str] = []

    def mock_handler(req: httpx.Request) -> httpx.Response:
        auth_headers.append(req.headers["authorization"])
        return httpx.Response(
            200,
            json={"choices": [{"index": 0, "message": {"role": "assistant", "content": "Ok"}}]},
        )

    client = make_mock_groq_client(mock_handler)
    adapter = GroqProviderAdapter(
        api_key=company_key,
        http_client=client,
        allow_localhost_for_testing=True,
    )

    # Send request with user_key attached
    adapter.generate(GenerationRequest(prompt="Test", user_key=gemini_user_key))

    # The authorization header must use the company key, NOT the user's Gemini key!
    assert auth_headers[0] == "Bearer gsk-test-company-key-valid"
    assert gemini_user_key not in auth_headers[0]


def test_groq_database_vault_keys_precede_static_key() -> None:
    """Database Platform Key Vault slots take strict precedence over static environment key."""
    static_key = SecretStr("gsk-static-shadow-key-1111")
    vault_raw_key = "gsk-vault-authoritative-key-2222"
    encrypted_vault_key = encrypt_platform_key(vault_raw_key, TEST_PLATFORM_SECRET)

    slot = GroqKeySlot(
        key_id="v1",
        key_secret=SecretStr(vault_raw_key),
        key_hint="...2222",
        quota_group="vault-group",
        is_enabled=True,
        status=PlatformKeyStatus.ACTIVE.value,
    )

    auth_headers: list[str] = []

    def mock_handler(req: httpx.Request) -> httpx.Response:
        auth_headers.append(req.headers["authorization"])
        return httpx.Response(
            200,
            json={"choices": [{"index": 0, "message": {"role": "assistant", "content": "Ok"}}]},
        )

    client = make_mock_groq_client(mock_handler)
    adapter = GroqProviderAdapter(
        api_key=static_key,  # Configured static key
        key_slots=[slot],    # Provisioned vault slots
        http_client=client,
        allow_localhost_for_testing=True,
    )

    adapter.generate(GenerationRequest(prompt="Test"))

    # Must use vault slot key, not the static key
    assert auth_headers[0] == f"Bearer {vault_raw_key}"
    assert "gsk-static-shadow-key-1111" not in auth_headers[0]


def test_groq_key_vault_rejects_non_active_keys() -> None:
    """UNVERIFIED, REVOKED, INVALID, and QUOTA_EXHAUSTED keys are rejected."""
    key_unverified = PlatformAIKey(
        id=uuid4(),
        encrypted_api_key=encrypt_platform_key("gsk-key-1", TEST_PLATFORM_SECRET),
        key_hint="...0001",
        quota_group="default",
        key_label="Key 1",
        is_enabled=True,
        status=PlatformKeyStatus.UNVERIFIED.value,
    )
    key_revoked = PlatformAIKey(
        id=uuid4(),
        encrypted_api_key=encrypt_platform_key("gsk-key-2", TEST_PLATFORM_SECRET),
        key_hint="...0002",
        quota_group="default",
        key_label="Key 2",
        is_enabled=True,
        status=PlatformKeyStatus.REVOKED.value,
    )

    mock_client = make_mock_groq_client(lambda req: httpx.Response(200, json={}))
    adapter = GroqProviderAdapter.from_platform_keys(
        keys=[key_unverified, key_revoked],
        secret=TEST_PLATFORM_SECRET,
        http_client=mock_client,
        allow_localhost_for_testing=True,
    )

    health = adapter.health_check()
    assert health["total_slots"] == 0
    assert health["active_slots"] == 0

    with pytest.raises(AIProviderAuthenticationError) as exc_info:
        adapter.generate(GenerationRequest(prompt="Test"))
    assert "No Groq API keys configured" in str(exc_info.value)


def test_groq_vault_ineligible_fails_closed_without_static_fallback() -> None:
    """When vault slots exist but are cooling down, adapter fails closed and does NOT use static key by default."""
    slot_cooldown = GroqKeySlot(
        key_id="k-cd",
        key_secret=SecretStr("gsk-vault-cooling"),
        key_hint="...cd01",
        quota_group="default",
        is_enabled=True,
        status=PlatformKeyStatus.ACTIVE.value,
        cooldown_until=utc_now() + timedelta(seconds=120),
    )
    static_key = SecretStr("gsk-static-company-key-1234")

    client = make_mock_groq_client(lambda req: httpx.Response(200, json={}))
    adapter = GroqProviderAdapter(
        key_slots=[slot_cooldown],
        api_key=static_key,
        allow_static_fallback=False,  # Strict fail-closed default
        http_client=client,
        allow_localhost_for_testing=True,
    )

    # Must raise AIProviderRateLimitError rather than silently using static_key
    with pytest.raises(AIProviderRateLimitError) as exc_info:
        adapter.generate(GenerationRequest(prompt="Test fail closed"))
    assert "cooling down" in str(exc_info.value).lower()


def test_groq_vault_ineligible_falls_back_when_explicitly_allowed() -> None:
    """When allow_static_fallback=True, adapter falls back to static key when vault keys are cooling down."""
    slot_cooldown = GroqKeySlot(
        key_id="k-cd",
        key_secret=SecretStr("gsk-vault-cooling"),
        key_hint="...cd01",
        quota_group="default",
        is_enabled=True,
        status=PlatformKeyStatus.ACTIVE.value,
        cooldown_until=utc_now() + timedelta(seconds=120),
    )
    static_key = SecretStr("gsk-static-company-key-fallback")
    auth_headers: list[str] = []

    def mock_handler(req: httpx.Request) -> httpx.Response:
        auth_headers.append(req.headers["authorization"])
        return httpx.Response(
            200,
            json={"choices": [{"index": 0, "message": {"role": "assistant", "content": "Fallback success."}}]},
        )

    client = make_mock_groq_client(mock_handler)
    adapter = GroqProviderAdapter(
        key_slots=[slot_cooldown],
        api_key=static_key,
        allow_static_fallback=True,  # Opt-in fallback
        http_client=client,
        allow_localhost_for_testing=True,
    )

    resp = adapter.generate(GenerationRequest(prompt="Test fallback"))
    assert resp.text == "Fallback success."
    assert auth_headers[0] == "Bearer gsk-static-company-key-fallback"


# ---------------------------------------------------------------------------
# 6. Quota Group Cooldown & Shared Organization Limits
# ---------------------------------------------------------------------------

def test_groq_rate_limit_propagates_to_same_quota_group() -> None:
    """HTTP 429 organization limit sets cooldown on ALL keys sharing the same quota group."""
    slot_a1 = GroqKeySlot(
        key_id="k-a1",
        key_secret=SecretStr("gsk-test-a1"),
        key_hint="...00a1",
        quota_group="org-finance",
        is_enabled=True,
        status=PlatformKeyStatus.ACTIVE.value,
    )
    slot_a2 = GroqKeySlot(
        key_id="k-a2",
        key_secret=SecretStr("gsk-test-a2"),
        key_hint="...00a2",
        quota_group="org-finance",  # Shares org quota with a1
        is_enabled=True,
        status=PlatformKeyStatus.ACTIVE.value,
    )

    def mock_handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            headers={"retry-after": "30"},
            json={
                "error": {
                    "message": "Rate limit reached for organization org_12345 on requests per minute (RPM)",
                    "type": "requests",
                }
            },
        )

    client = make_mock_groq_client(mock_handler)
    adapter = GroqProviderAdapter(
        key_slots=[slot_a1, slot_a2],
        http_client=client,
        allow_localhost_for_testing=True,
    )

    with pytest.raises(AIProviderRateLimitError):
        adapter.generate(GenerationRequest(prompt="Test"))

    # Both keys in the shared quota group must now be in cooldown
    assert slot_a1.is_active() is False
    assert slot_a2.is_active() is False
    assert slot_a1.cooldown_until is not None
    assert slot_a2.cooldown_until is not None


def test_groq_bounded_cooldown_parsing() -> None:
    """Cooldown header parsing enforces lower and upper bounds."""
    # Enforces minimum 0.1s and maximum 86400s
    assert _parse_cooldown_header("0") is None
    assert _parse_cooldown_header("-10") is None
    assert _parse_cooldown_header("1000000") == 86400.0  # Upper bound


def test_groq_documented_rate_limit_headers_rpd_and_tpm() -> None:
    """_extract_groq_cooldown_and_limit_type parses documented RPD, TPM, and Retry-After headers."""
    # x-ratelimit-reset-requests represents Requests Per Day (RPD) countdown (daily quota exhaustion)
    resp_rpd = httpx.Response(429, headers={"x-ratelimit-reset-requests": "2m59.56s"})
    cd_rpd, is_daily_rpd = _extract_groq_cooldown_and_limit_type(resp_rpd, None)
    assert cd_rpd == pytest.approx(179.56, rel=1e-2)
    assert is_daily_rpd is True

    # x-ratelimit-reset-tokens represents Tokens Per Minute (TPM) short-window countdown
    resp_tpm = httpx.Response(429, headers={"x-ratelimit-reset-tokens": "7.66s"})
    cd_tpm, is_daily_tpm = _extract_groq_cooldown_and_limit_type(resp_tpm, None)
    assert cd_tpm == pytest.approx(7.66, rel=1e-2)
    assert is_daily_tpm is False

    # retry-after header takes precedence and parses integer seconds
    resp_retry_after = httpx.Response(429, headers={"retry-after": "45"})
    cd_retry, is_daily_retry = _extract_groq_cooldown_and_limit_type(resp_retry_after, None)
    assert cd_retry == 45.0
    assert is_daily_retry is False

    # Combined with daily exhaustion indicator in JSON error message
    resp_daily = httpx.Response(
        429,
        headers={"retry-after": "120"},
    )
    error_json = {"error": {"message": "Daily tokens per day (tpd) limit exceeded", "type": "tokens"}}
    cd_daily, is_daily = _extract_groq_cooldown_and_limit_type(resp_daily, error_json)
    assert cd_daily == 120.0
    assert is_daily is True


# ---------------------------------------------------------------------------
# 7. 401 vs 403 Distinction & Safe Error Sanitization
# ---------------------------------------------------------------------------

def test_groq_401_invalidates_key_slot() -> None:
    """HTTP 401 marks key slot as INVALID."""
    slot = GroqKeySlot(
        key_id="k-auth",
        key_secret=SecretStr("gsk-invalid-auth"),
        key_hint="...auth",
        quota_group="default",
        is_enabled=True,
        status=PlatformKeyStatus.ACTIVE.value,
    )

    def mock_handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={"error": {"message": "Invalid API Key provided: gsk_sample_auth_token_for_redaction_test", "type": "invalid_request_error"}},
        )

    client = make_mock_groq_client(mock_handler)
    adapter = GroqProviderAdapter(
        key_slots=[slot],
        http_client=client,
        allow_localhost_for_testing=True,
    )

    with pytest.raises(AIProviderAuthenticationError) as exc_info:
        adapter.generate(GenerationRequest(prompt="Test"))

    assert slot.status == PlatformKeyStatus.INVALID.value
    # Sensitive key material must be redacted in error message
    assert "gsk_sample_auth_token_for_redaction_test" not in str(exc_info.value)
    assert "gsk_***" in str(exc_info.value)


def test_groq_403_permission_denied_does_not_invalidate_key() -> None:
    """HTTP 403 due to model permission denied does NOT mark key as invalid."""
    slot = GroqKeySlot(
        key_id="k-perm",
        key_secret=SecretStr("gsk-valid-perm-key"),
        key_hint="...perm",
        quota_group="default",
        is_enabled=True,
        status=PlatformKeyStatus.ACTIVE.value,
    )

    def mock_handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={
                "error": {
                    "message": "Access restricted: model permission denied in project proj_secret789",
                    "type": "permission_denied",
                }
            },
        )

    client = make_mock_groq_client(mock_handler)
    adapter = GroqProviderAdapter(
        key_slots=[slot],
        http_client=client,
        allow_localhost_for_testing=True,
    )

    with pytest.raises(AIProviderBadRequestError) as exc_info:
        adapter.generate(GenerationRequest(prompt="Test"))

    assert exc_info.value.status_code == 403
    # Key must NOT be disabled because key is valid; only model permission was denied
    assert slot.status == PlatformKeyStatus.ACTIVE.value
    # Project ID must be redacted
    assert "proj_secret789" not in str(exc_info.value)
    assert "proj_***" in str(exc_info.value)


def test_sanitize_groq_error_message() -> None:
    """_sanitize_groq_error_message redacts organizations, projects, keys, and URLs."""
    raw = "Rate limit in org_987654321 for proj_abcdef123 with key gsk_xyz789. See https://console.groq.com/docs/rate-limits"
    sanitized = _sanitize_groq_error_message(raw)
    assert "org_987654321" not in sanitized
    assert "org_***" in sanitized
    assert "proj_abcdef123" not in sanitized
    assert "proj_***" in sanitized
    assert "gsk_xyz789" not in sanitized
    assert "gsk_***" in sanitized
    assert "https://" not in sanitized


# ---------------------------------------------------------------------------
# 8. Provider Runtime Resolver: PLANNED Status & Customer Traffic Gate
# ---------------------------------------------------------------------------

def test_resolver_planned_provider_rejected_for_customer_traffic(db_session: Session) -> None:
    """A PLANNED provider is rejected for ordinary customer traffic even when adapter is registered."""
    resolver = ProviderRuntimeResolver()

    mock_client = make_mock_groq_client(lambda req: httpx.Response(200, json={}))
    groq_adapter = GroqProviderAdapter(
        api_key="gsk-test-key",
        http_client=mock_client,
        allow_localhost_for_testing=True,
    )
    resolver.register_adapter(groq_adapter)

    # Ordinary customer resolution must FAIL because Groq status in DB is PLANNED
    with pytest.raises(ProviderTrafficNotPermittedError) as exc_info:
        resolver.resolve(db_session, provider_code="groq")

    assert exc_info.value.status_code == 403
    assert exc_info.value.error_category == "TRAFFIC_NOT_PERMITTED"
    assert "PLANNED" in str(exc_info.value)

    # Internal/test resolution using dedicated test helper function succeeds
    resolved_adapter, model_rec = resolve_for_test_harness(
        resolver,
        db_session,
        provider_code="groq",
    )
    assert resolved_adapter is groq_adapter
    assert model_rec is None


def test_resolver_async_test_harness_isolation() -> None:
    """Asynchronous resolver enforces customer traffic gating and isolates test harness method."""
    import asyncio
    from unittest.mock import AsyncMock

    async def _run() -> None:
        resolver = ProviderRuntimeResolver()

        groq_adapter = GroqProviderAdapter(
            api_key="gsk-test-key",
            http_client=make_mock_groq_client(lambda req: httpx.Response(200, json={})),
            allow_localhost_for_testing=True,
        )
        resolver.register_adapter(groq_adapter)

        mock_provider = AIProvider(
            id=uuid4(),
            code="groq",
            display_name="Groq",
            is_enabled=True,
            connection_status="PLANNED",
        )

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_provider
        mock_session.execute.return_value = mock_result

        # resolve_async() must fail with ProviderTrafficNotPermittedError
        with pytest.raises(ProviderTrafficNotPermittedError) as exc_info:
            await resolver.resolve_async(mock_session, provider_code="groq")
        assert exc_info.value.status_code == 403

        # resolve_async_for_test_harness() helper must succeed
        adapter, model_rec = await resolve_async_for_test_harness(resolver, mock_session, provider_code="groq")
        assert adapter is groq_adapter
        assert model_rec is None

    asyncio.run(_run())


def test_resolver_lifecycle_state_inspection(db_session: Session) -> None:
    """get_provider_lifecycle_state reports all 5 distinct statuses without collapsing."""
    resolver = ProviderRuntimeResolver()

    # Before adapter registration
    state_before = resolver.get_provider_lifecycle_state(db_session, "groq")
    assert state_before["metadata_validated"] is True
    assert state_before["metadata_enabled"] is True
    assert state_before["connection_status"] == "PLANNED"
    assert state_before["adapter_registered"] is False
    assert state_before["is_operational"] is False
    assert state_before["customer_traffic_permitted"] is False

    # Register adapter
    groq_adapter = GroqProviderAdapter(
        api_key="gsk-test-key",
        http_client=make_mock_groq_client(lambda req: httpx.Response(200, json={})),
        allow_localhost_for_testing=True,
    )
    resolver.register_adapter(groq_adapter)

    # After adapter registration
    state_after = resolver.get_provider_lifecycle_state(db_session, "groq")
    assert state_after["adapter_registered"] is True
    assert state_after["is_operational"] is True
    # Still NOT permitted for customer traffic because status is PLANNED
    assert state_after["customer_traffic_permitted"] is False


def test_resolver_missing_adapter_raises_501(db_session: Session) -> None:
    """Resolver raises ProviderAdapterUnavailableError (501) when adapter is not registered."""
    resolver = ProviderRuntimeResolver()
    with pytest.raises(ProviderAdapterUnavailableError) as exc_info:
        resolver.resolve(db_session, provider_code="openrouter")
    assert exc_info.value.status_code == 501
    assert exc_info.value.error_category == "ADAPTER_UNAVAILABLE"


# ---------------------------------------------------------------------------
# 9. Transport Security: Redirects and Oversized Bodies
# ---------------------------------------------------------------------------

def test_groq_redirect_rejected() -> None:
    """HTTP 301/302 redirects are intercepted and rejected to prevent SSRF hopping."""
    def mock_handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://malicious.internal.target"})

    client = make_mock_groq_client(mock_handler)
    adapter = GroqProviderAdapter(
        api_key="gsk-test-key",
        http_client=client,
        allow_localhost_for_testing=True,
    )
    with pytest.raises(AIProviderBadRequestError) as exc_info:
        adapter.generate(GenerationRequest(prompt="Test"))
    assert "Redirects are disabled" in str(exc_info.value)


def test_groq_oversized_response_rejected() -> None:
    """Streamed response exceeding 10MB raises 413 AIProviderBadRequestError."""
    def mock_handler(req: httpx.Request) -> httpx.Response:
        headers = {"content-length": str(MAX_SAFE_RESPONSE_BYTES + 1024)}
        return httpx.Response(200, headers=headers, content=b"large")

    client = make_mock_groq_client(mock_handler)
    adapter = GroqProviderAdapter(
        api_key="gsk-test-key",
        http_client=client,
        allow_localhost_for_testing=True,
    )
    with pytest.raises(AIProviderBadRequestError) as exc_info:
        adapter.generate(GenerationRequest(prompt="Test"))
    assert exc_info.value.status_code == 413


# ---------------------------------------------------------------------------
# 10. Gemini Default Runtime Invariance
# ---------------------------------------------------------------------------

def test_gemini_remains_active_and_default(db_session: Session) -> None:
    """Google Gemini remains the default operational provider in DB catalogue."""
    gemini_provider = db_session.scalar(select(AIProvider).where(AIProvider.code == "google_gemini"))
    assert gemini_provider is not None
    assert gemini_provider.connection_status == ProviderConnectionStatus.AVAILABLE.value
    assert gemini_provider.is_enabled is True
    assert gemini_provider.priority == 10

    groq_provider = db_session.scalar(select(AIProvider).where(AIProvider.code == "groq"))
    assert groq_provider is not None
    assert groq_provider.connection_status == ProviderConnectionStatus.PLANNED.value
    assert groq_provider.priority == 20


def test_seed_ai_catalogue_groq_models_parity(db_session: Session) -> None:
    """Verify seed_ai_provider_catalogue seeds Groq models while keeping provider PLANNED."""
    groq_provider = db_session.scalar(select(AIProvider).where(AIProvider.code == "groq"))
    assert groq_provider is not None
    assert groq_provider.connection_status == ProviderConnectionStatus.PLANNED.value
    assert groq_provider.is_enabled is True

    groq_models = db_session.scalars(
        select(AIModel).where(AIModel.provider_id == groq_provider.id)
    ).all()
    all_groq_models = {m.model_identifier: m for m in groq_models}

    # Verify active production candidate models
    assert "openai/gpt-oss-120b" in all_groq_models
    assert all_groq_models["openai/gpt-oss-120b"].is_enabled is True

    assert "openai/gpt-oss-20b" in all_groq_models
    assert all_groq_models["openai/gpt-oss-20b"].is_enabled is True

    # Verify enterprise-restricted models seeded as disabled by default with UNKNOWN access class and notation
    assert "llama-3.3-70b-versatile" in all_groq_models
    llama_70b = all_groq_models["llama-3.3-70b-versatile"]
    assert llama_70b.is_enabled is False
    assert llama_70b.is_default is False
    assert "[ENTERPRISE_RESTRICTED]" in llama_70b.description
    assert llama_70b.access_class == ModelAccessClass.UNKNOWN.value

    assert "llama-3.1-8b-instant" in all_groq_models
    llama_8b = all_groq_models["llama-3.1-8b-instant"]
    assert llama_8b.is_enabled is False
    assert llama_8b.is_default is False
    assert "[ENTERPRISE_RESTRICTED]" in llama_8b.description
    assert llama_8b.access_class == ModelAccessClass.UNKNOWN.value

    # Verify adapter registry also aligns access_class with UNKNOWN
    from backend.app.services.providers.groq_adapter import GROQ_MODEL_REGISTRY
    assert GROQ_MODEL_REGISTRY["llama-3.3-70b-versatile"].access_class == "UNKNOWN"
    assert GROQ_MODEL_REGISTRY["llama-3.1-8b-instant"].access_class == "UNKNOWN"

    # Verify other Groq models maintain PAY_AS_YOU_GO (no regression)
    assert all_groq_models["openai/gpt-oss-120b"].access_class == ModelAccessClass.PAY_AS_YOU_GO.value
    assert all_groq_models["openai/gpt-oss-20b"].access_class == ModelAccessClass.PAY_AS_YOU_GO.value

    # Verify Gemini provider models maintain FREE_TIER (no regression)
    gemini_provider = db_session.scalar(select(AIProvider).where(AIProvider.code == "google_gemini"))
    assert gemini_provider is not None
    gemini_models = {
        m.model_identifier: m
        for m in db_session.scalars(select(AIModel).where(AIModel.provider_id == gemini_provider.id)).all()
    }
    assert gemini_models["gemini-3.8-flash"].access_class == ModelAccessClass.FREE_TIER.value
    assert gemini_models["gemini-3.5-flash-lite"].access_class == ModelAccessClass.FREE_TIER.value

    # Verify candidate default model for the Groq provider
    gpt_120b = all_groq_models["openai/gpt-oss-120b"]
    assert gpt_120b.is_default is True

    # Verify retired models are in RETIRED_GROQ_MODEL_IDENTIFIERS and not active
    from backend.app.db.seed_ai_catalogue import RETIRED_GROQ_MODEL_IDENTIFIERS
    assert "deepseek-r1-distill-llama-70b" in RETIRED_GROQ_MODEL_IDENTIFIERS
    assert "deepseek-r1-distill-llama-70b" not in all_groq_models  # Not enabled or created by baseline seed

    for retired_id in RETIRED_GROQ_MODEL_IDENTIFIERS:
        if retired_id in all_groq_models:
            assert all_groq_models[retired_id].is_enabled is False


def test_deepseek_pre_existing_record_retired_by_seed(db_session: Session) -> None:
    """Pre-existing DeepSeek records in DB are deactivated and marked retired by seed reconciliation."""
    groq_provider = db_session.scalar(select(AIProvider).where(AIProvider.code == "groq"))
    assert groq_provider is not None

    # Simulate pre-existing active DeepSeek model in database
    pre_existing_deepseek = AIModel(
        provider_id=groq_provider.id,
        model_identifier="deepseek-r1-distill-llama-70b",
        display_name="DeepSeek R1 Distill Llama 70B (Legacy)",
        description="Legacy preview reasoning model",
        is_enabled=True,
        is_default=False,
    )
    db_session.add(pre_existing_deepseek)
    db_session.commit()

    # Run catalogue seed reconciliation
    seed_ai_provider_catalogue(db_session)
    db_session.commit()
    db_session.refresh(pre_existing_deepseek)

    # Verify that seed reconciliation disabled and marked it retired
    assert pre_existing_deepseek.is_enabled is False
    assert pre_existing_deepseek.is_default is False
    assert "[RETIRED" in pre_existing_deepseek.description

    # Verify that running seed again cannot enable it
    seed_ai_provider_catalogue(db_session)
    db_session.commit()
    db_session.refresh(pre_existing_deepseek)
    assert pre_existing_deepseek.is_enabled is False
