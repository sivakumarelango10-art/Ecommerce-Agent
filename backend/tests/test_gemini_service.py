from types import SimpleNamespace

import httpx
import pytest
from google.genai import errors, types
from pydantic import ValidationError

from backend.app.core.config import ConfigurationError, Settings, load_settings
from backend.app.services import gemini_service
from backend.app.services.gemini_service import (
    GeminiProviderError,
    GeminiResponseError,
    GeminiService,
    GeminiTimeoutError,
)


class FakeModels:
    def __init__(self, outcome: object) -> None:
        self.outcome = outcome
        self.calls: list[dict[str, object]] = []

    def generate_content(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        if hasattr(self.outcome, "text"):
            return self.outcome
        return SimpleNamespace(text=self.outcome, function_calls=[])


class FakeClient:
    def __init__(self, outcome: object) -> None:
        self.models = FakeModels(outcome)
        self.closed = False

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def service_factory(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-sent")
    monkeypatch.setenv("GEMINI_MODEL", "test-gemini-model")
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///:memory:")
    monkeypatch.setenv("AUTH_SECRET_KEY", "test-auth-secret-key-0123456789abcdef")

    def create(outcome: object) -> tuple[GeminiService, FakeClient]:
        client = FakeClient(outcome)

        def fake_client_factory(**kwargs: object) -> FakeClient:
            assert kwargs["api_key"] == "test-key-not-sent"
            http_options = kwargs["http_options"]
            assert http_options.timeout == 30_000
            retry = http_options.retry_options
            assert retry is not None
            assert retry.attempts == 4
            assert retry.initial_delay == 1.0
            assert retry.max_delay == 4.0
            assert retry.http_status_codes == [408, 429, 500, 502, 503, 504]
            return client

        monkeypatch.setattr(gemini_service.genai, "Client", fake_client_factory)
        settings = Settings(_env_file=None)
        return GeminiService(settings), client

    return create


def test_service_initializes_configured_client_and_returns_text(service_factory):
    service, client = service_factory("  A useful answer.  ")

    assert service.answer("What should I compare?") == "A useful answer."
    call = client.models.calls[0]
    assert call["model"] == "test-gemini-model"
    assert call["contents"] == "What should I compare?"
    assert call["config"].tools is None
    assert call["config"].automatic_function_calling.disable is True

    service.close()
    assert client.closed


def test_function_declarations_are_forwarded_without_automatic_execution(
    service_factory,
):
    service, client = service_factory(
        SimpleNamespace(
            text=None,
            function_calls=[
                types.FunctionCall(name="lookup_product", args={"query": "phone"})
            ],
        )
    )
    tool = types.Tool(
        function_declarations=[
            types.FunctionDeclaration(
                name="lookup_product",
                description="Look up a product.",
            )
        ]
    )

    result = service.generate("Find a product", tools=[tool])

    config = client.models.calls[0]["config"]
    assert config.tools == [tool]
    assert config.automatic_function_calling.disable is True
    assert result.text is None
    assert result.function_calls[0].name == "lookup_product"


def test_missing_api_key_is_rejected(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)

    with pytest.raises(ConfigurationError, match="Application configuration is invalid"):
        load_settings(env_file=None)


def test_default_model_is_gemini_38_flash(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-sent")
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///:memory:")
    monkeypatch.setenv("AUTH_SECRET_KEY", "test-auth-secret-key-0123456789abcdef")
    monkeypatch.delenv("GEMINI_MODEL", raising=False)

    assert Settings(_env_file=None).gemini_model == "gemini-3.8-flash"


def test_api_failure_is_mapped_to_provider_error(service_factory):
    service, _ = service_factory(errors.APIError(503, {"error": "mock failure"}))

    with pytest.raises(GeminiProviderError, match="Gemini request failed"):
        service.answer("A question")


def test_timeout_is_mapped_to_timeout_error(service_factory):
    service, _ = service_factory(httpx.ReadTimeout("mock timeout"))

    with pytest.raises(GeminiTimeoutError, match="timed out"):
        service.answer("A question")


@pytest.mark.parametrize("response_text", [None, "", "   "])
def test_empty_gemini_response_is_rejected(service_factory, response_text):
    service, _ = service_factory(response_text)

    with pytest.raises(GeminiResponseError, match="no usable content"):
        service.answer("A question")


def test_blank_questions_are_rejected(service_factory):
    service, client = service_factory("Unused")

    with pytest.raises(ValueError, match="must not be blank"):
        service.answer("  ")

    assert client.models.calls == []


def test_oversized_questions_are_rejected(service_factory):
    service, client = service_factory("Unused")

    with pytest.raises(ValueError, match="must not exceed"):
        service.answer("x" * 4001)

    assert client.models.calls == []


def test_custom_system_instruction_and_content_sequence(service_factory):
    service, client = service_factory(
        SimpleNamespace(
            text="Order status is shipped.",
            function_calls=[],
            candidates=[
                SimpleNamespace(
                    content=types.Content(
                        role="model",
                        parts=[types.Part.from_text(text="Order status is shipped.")],
                    )
                )
            ],
        )
    )

    custom_sys = "Custom order support instruction."
    history = [
        types.Content(
            role="user",
            parts=[types.Part.from_text(text="Where is my order?")],
        )
    ]

    result = service.generate(history, system_instruction=custom_sys)

    assert result.text == "Order status is shipped."
    assert result.candidate_content is not None
    config = client.models.calls[0]["config"]
    assert config.system_instruction == custom_sys
    assert client.models.calls[0]["contents"] == history


def test_gemini_service_configures_sdk_http_retry_options(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-configured")
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///:memory:")
    monkeypatch.setenv("AUTH_SECRET_KEY", "test-auth-secret-key-0123456789abcdef")
    monkeypatch.setenv("GEMINI_TIMEOUT_SECONDS", "30")
    settings = Settings(_env_file=None)
    service = GeminiService(settings)
    try:
        retry_options = service._client._api_client._http_options.retry_options
        assert retry_options is not None
        assert retry_options.attempts == 4
        assert retry_options.initial_delay == 1.0
        assert retry_options.max_delay == 4.0
        assert retry_options.http_status_codes == [408, 429, 500, 502, 503, 504]

        # Verify underlying tenacity retry settings configured in google-genai
        assert service._client._api_client._retry.stop.max_attempt_number == 4
        assert service._client._api_client._retry.wait.initial == 1.0
        assert service._client._api_client._retry.wait.max == 4.0
    finally:
        service.close()
