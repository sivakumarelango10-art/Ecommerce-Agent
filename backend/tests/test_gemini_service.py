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


def test_legacy_single_key_configuration(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-single-key-fixture")
    monkeypatch.delenv("GEMINI_API_KEYS", raising=False)
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///:memory:")
    monkeypatch.setenv("AUTH_SECRET_KEY", "test-auth-secret-key-0123456789abcdef")

    settings = Settings(_env_file=None)
    assert len(settings.platform_gemini_keys) == 1
    assert settings.platform_gemini_keys[0].quota_group == "default"
    assert settings.platform_gemini_keys[0].key.get_secret_value() == "test-gemini-single-key-fixture"
    assert settings.gemini_api_key.get_secret_value() == "test-gemini-single-key-fixture"

    fake_client = FakeClient("legacy response")
    service = GeminiService(settings, client_factory=lambda **kwargs: fake_client)
    try:
        assert service.answer("Test legacy") == "legacy response"
        assert len(service._keys) == 1
        assert service._keys[0].quota_group == "default"
    finally:
        service.close()


def test_multiple_key_configuration(monkeypatch: pytest.MonkeyPatch):
    raw_json = (
        '[{"key": "key-alpha-1", "quota_group": "project-alpha"}, '
        '{"key": "key-alpha-2", "quota_group": "project-alpha"}, '
        '{"key": "key-beta-1", "quota_group": "project-beta"}]'
    )
    monkeypatch.setenv("GEMINI_API_KEYS", raw_json)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///:memory:")
    monkeypatch.setenv("AUTH_SECRET_KEY", "test-auth-secret-key-0123456789abcdef")

    settings = Settings(_env_file=None)
    assert len(settings.platform_gemini_keys) == 3
    assert settings.platform_gemini_keys[0].quota_group == "project-alpha"
    assert settings.platform_gemini_keys[1].quota_group == "project-alpha"
    assert settings.platform_gemini_keys[2].quota_group == "project-beta"
    assert settings.gemini_api_key.get_secret_value() == "key-alpha-1"

    clients = {}
    def mock_factory(api_key: str, http_options: object) -> FakeClient:
        fc = FakeClient(f"response-from-{api_key}")
        clients[api_key] = fc
        return fc

    service = GeminiService(settings, client_factory=mock_factory)
    try:
        assert len(service._keys) == 3
        assert len(clients) == 3
    finally:
        service.close()


def test_maximum_six_key_enforcement(monkeypatch: pytest.MonkeyPatch):
    seven_keys = [
        {"key": f"key-test-{i}", "quota_group": f"project-{i}"}
        for i in range(7)
    ]
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///:memory:")
    monkeypatch.setenv("AUTH_SECRET_KEY", "test-auth-secret-key-0123456789abcdef")

    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None, gemini_api_keys=seven_keys)

    assert "at most 6 keys" in str(exc_info.value)


def test_duplicate_key_rejection(monkeypatch: pytest.MonkeyPatch):
    duplicate_keys = [
        {"key": "super-secret-key-duplicate", "quota_group": "project-alpha"},
        {"key": "super-secret-key-duplicate", "quota_group": "project-beta"},
    ]
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///:memory:")
    monkeypatch.setenv("AUTH_SECRET_KEY", "test-auth-secret-key-0123456789abcdef")

    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None, gemini_api_keys=duplicate_keys)

    err_str = str(exc_info.value)
    assert "duplicate API keys" in err_str
    # Critical security requirement: never leak secret key in error messages
    assert "super-secret-key-duplicate" not in err_str


def test_missing_or_blank_quota_group_rejection(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///:memory:")
    monkeypatch.setenv("AUTH_SECRET_KEY", "test-auth-secret-key-0123456789abcdef")

    # Missing quota_group
    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None, gemini_api_keys=[{"key": "key-1"}])
    assert "quota_group" in str(exc_info.value)

    # Blank quota_group
    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None, gemini_api_keys=[{"key": "key-1", "quota_group": "   "}])
    assert "quota_group" in str(exc_info.value)


def test_normal_successful_round_robin_selection(monkeypatch: pytest.MonkeyPatch):
    keys_config = [
        {"key": "key-rr-1", "quota_group": "group-1"},
        {"key": "key-rr-2", "quota_group": "group-2"},
        {"key": "key-rr-3", "quota_group": "group-3"},
    ]
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///:memory:")
    monkeypatch.setenv("AUTH_SECRET_KEY", "test-auth-secret-key-0123456789abcdef")
    settings = Settings(_env_file=None, gemini_api_keys=keys_config)

    clients: dict[str, FakeClient] = {}
    for cfg in keys_config:
        k = cfg["key"]
        clients[k] = FakeClient(f"answer-{k}")

    service = GeminiService(settings, client_factory=lambda api_key, **kwargs: clients[api_key])
    try:
        # Four consecutive calls should cycle 0 -> 1 -> 2 -> 0
        ans1 = service.answer("Question 1")
        ans2 = service.answer("Question 2")
        ans3 = service.answer("Question 3")
        ans4 = service.answer("Question 4")

        assert ans1 == "answer-key-rr-1"
        assert ans2 == "answer-key-rr-2"
        assert ans3 == "answer-key-rr-3"
        assert ans4 == "answer-key-rr-1"

        assert len(clients["key-rr-1"].models.calls) == 2
        assert len(clients["key-rr-2"].models.calls) == 1
        assert len(clients["key-rr-3"].models.calls) == 1
    finally:
        service.close()


def test_429_on_one_project_group_skips_all_keys_in_that_group(monkeypatch: pytest.MonkeyPatch):
    # Two keys in project-alpha, one key in project-beta
    keys_config = [
        {"key": "key-alpha-1", "quota_group": "project-alpha"},
        {"key": "key-alpha-2", "quota_group": "project-alpha"},
        {"key": "key-beta-1", "quota_group": "project-beta"},
    ]
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///:memory:")
    monkeypatch.setenv("AUTH_SECRET_KEY", "test-auth-secret-key-0123456789abcdef")
    settings = Settings(_env_file=None, gemini_api_keys=keys_config)

    rate_limit_err = errors.APIError(429, {"error": {"code": 429, "message": "Resource exhausted"}})
    clients = {
        "key-alpha-1": FakeClient(rate_limit_err),
        "key-alpha-2": FakeClient("should never be called"),
        "key-beta-1": FakeClient("success from beta"),
    }

    service = GeminiService(settings, client_factory=lambda api_key, **kwargs: clients[api_key])
    try:
        result = service.answer("Test query")
        assert result == "success from beta"

        # key-alpha-1 failed with 429
        assert len(clients["key-alpha-1"].models.calls) == 1
        # key-alpha-2 was skipped entirely because project-alpha was placed into cooldown!
        assert len(clients["key-alpha-2"].models.calls) == 0
        # key-beta-1 was called and succeeded
        assert len(clients["key-beta-1"].models.calls) == 1
    finally:
        service.close()


def test_429_on_project_a_successfully_falls_back_to_project_b(monkeypatch: pytest.MonkeyPatch):
    keys_config = [
        {"key": "key-proj-a", "quota_group": "project-a"},
        {"key": "key-proj-b", "quota_group": "project-b"},
    ]
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///:memory:")
    monkeypatch.setenv("AUTH_SECRET_KEY", "test-auth-secret-key-0123456789abcdef")
    settings = Settings(_env_file=None, gemini_api_keys=keys_config)

    rate_limit_err = errors.APIError(
        429,
        {
            "error": {
                "code": 429,
                "message": "Quota exceeded",
                "details": [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retry_delay": "45s"}],
            }
        },
    )
    clients = {
        "key-proj-a": FakeClient(rate_limit_err),
        "key-proj-b": FakeClient("fallback response from project b"),
    }

    service = GeminiService(settings, client_factory=lambda api_key, **kwargs: clients[api_key])
    try:
        res = service.answer("Query")
        assert res == "fallback response from project b"
        assert service._quota_group_cooldowns["project-a"] > 0
    finally:
        service.close()


def test_daily_quota_retry_information_produces_appropriately_long_cooldown(
    monkeypatch: pytest.MonkeyPatch,
):
    keys_config = [
        {"key": "key-daily-a", "quota_group": "project-a"},
        {"key": "key-daily-b", "quota_group": "project-b"},
    ]
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///:memory:")
    monkeypatch.setenv("AUTH_SECRET_KEY", "test-auth-secret-key-0123456789abcdef")
    settings = Settings(_env_file=None, gemini_api_keys=keys_config)

    daily_err = errors.APIError(
        429,
        {
            "error": {
                "code": 429,
                "message": "Resource has been exhausted. You have exceeded your daily requests limit.",
                "details": [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retry_delay": "86400s"}],
            }
        },
    )
    clients = {
        "key-daily-a": FakeClient(daily_err),
        "key-daily-b": FakeClient("project b answer"),
    }

    import time
    start_time = time.time()
    service = GeminiService(settings, client_factory=lambda api_key, **kwargs: clients[api_key])
    try:
        res = service.answer("Daily quota query")
        assert res == "project b answer"
        cooldown_expiry = service._quota_group_cooldowns["project-a"]
        # Cooldown must be at least 86000 seconds in the future
        assert cooldown_expiry - start_time >= 86000.0
    finally:
        service.close()


def test_all_project_groups_exhausted_produces_controlled_provider_error(
    monkeypatch: pytest.MonkeyPatch,
):
    keys_config = [
        {"key": "key-ex-a", "quota_group": "project-a"},
        {"key": "key-ex-b", "quota_group": "project-b"},
    ]
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///:memory:")
    monkeypatch.setenv("AUTH_SECRET_KEY", "test-auth-secret-key-0123456789abcdef")
    settings = Settings(_env_file=None, gemini_api_keys=keys_config)

    err_a = errors.APIError(429, {"error": {"code": 429, "message": "Resource exhausted"}})
    err_b = errors.APIError(429, {"error": {"code": 429, "message": "Resource exhausted"}})
    clients = {
        "key-ex-a": FakeClient(err_a),
        "key-ex-b": FakeClient(err_b),
    }

    service = GeminiService(settings, client_factory=lambda api_key, **kwargs: clients[api_key])
    try:
        with pytest.raises(GeminiProviderError, match="Gemini API quota exhausted across all available project groups"):
            service.answer("Should fail cleanly")
    finally:
        service.close()


def test_invalid_credential_disables_only_affected_key(monkeypatch: pytest.MonkeyPatch):
    keys_config = [
        {"key": "key-bad", "quota_group": "project-common"},
        {"key": "key-good", "quota_group": "project-common"},
    ]
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///:memory:")
    monkeypatch.setenv("AUTH_SECRET_KEY", "test-auth-secret-key-0123456789abcdef")
    settings = Settings(_env_file=None, gemini_api_keys=keys_config)

    invalid_cred_err = errors.APIError(
        400,
        {
            "error": {
                "code": 400,
                "message": "API key not valid. Please pass a valid API key.",
                "status": "INVALID_ARGUMENT",
                "details": [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "API_KEY_INVALID"}],
            }
        },
    )
    clients = {
        "key-bad": FakeClient(invalid_cred_err),
        "key-good": FakeClient("valid key response"),
    }

    service = GeminiService(settings, client_factory=lambda api_key, **kwargs: clients[api_key])
    try:
        # First call: key-bad is tried, marked inactive; key-good succeeds
        ans1 = service.answer("Call 1")
        assert ans1 == "valid key response"
        assert service._keys[0].is_active is False
        assert service._keys[1].is_active is True

        # Second call: key-bad is skipped immediately because is_active is False
        ans2 = service.answer("Call 2")
        assert ans2 == "valid key response"

        # key-bad was only invoked once during the initial discovery
        assert len(clients["key-bad"].models.calls) == 1
        # key-good was invoked twice
        assert len(clients["key-good"].models.calls) == 2
    finally:
        service.close()


def test_unrelated_403_does_not_disable_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GEMINI_API_KEY", "key-perm-test")
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///:memory:")
    monkeypatch.setenv("AUTH_SECRET_KEY", "test-auth-secret-key-0123456789abcdef")
    settings = Settings(_env_file=None)

    perm_denied_err = errors.APIError(
        403,
        {
            "error": {
                "code": 403,
                "message": "User location is not supported for the API use.",
                "status": "PERMISSION_DENIED",
                "details": [{"reason": "LOCATION_NOT_SUPPORTED"}],
            }
        },
    )
    fake_client = FakeClient(perm_denied_err)
    service = GeminiService(settings, client_factory=lambda **kwargs: fake_client)
    try:
        with pytest.raises(GeminiProviderError, match="Gemini request failed"):
            service.answer("Location query")

        # Key MUST NOT be disabled for unrelated permission/location errors
        assert service._keys[0].is_active is True
    finally:
        service.close()


def test_503_504_bounded_retries(monkeypatch: pytest.MonkeyPatch):
    keys_config = [
        {"key": "key-503-a", "quota_group": "group-a"},
        {"key": "key-503-b", "quota_group": "group-b"},
    ]
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///:memory:")
    monkeypatch.setenv("AUTH_SECRET_KEY", "test-auth-secret-key-0123456789abcdef")
    settings = Settings(_env_file=None, gemini_api_keys=keys_config)

    # 1. First key fails with 503, second key succeeds
    err_503 = errors.APIError(503, {"error": "Service Unavailable"})
    clients = {
        "key-503-a": FakeClient(err_503),
        "key-503-b": FakeClient("recovered from 503"),
    }
    service = GeminiService(settings, client_factory=lambda api_key, **kwargs: clients[api_key])
    try:
        res = service.answer("Test 503 failover")
        assert res == "recovered from 503"
    finally:
        service.close()

    # 2. Both keys fail with 503: bounded attempts stop and raise GeminiProviderError
    clients_all_fail = {
        "key-503-a": FakeClient(err_503),
        "key-503-b": FakeClient(err_503),
    }
    service_all_fail = GeminiService(
        settings, client_factory=lambda api_key, **kwargs: clients_all_fail[api_key]
    )
    try:
        with pytest.raises(GeminiProviderError, match="Gemini request failed"):
            service_all_fail.answer("Test bounded 503")
    finally:
        service_all_fail.close()


def test_concurrent_selection_is_thread_safe(monkeypatch: pytest.MonkeyPatch):
    import concurrent.futures

    keys_config = [
        {"key": f"key-thread-{i}", "quota_group": f"group-{i}"}
        for i in range(3)
    ]
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///:memory:")
    monkeypatch.setenv("AUTH_SECRET_KEY", "test-auth-secret-key-0123456789abcdef")
    settings = Settings(_env_file=None, gemini_api_keys=keys_config)

    clients = {
        f"key-thread-{i}": FakeClient(f"thread-ans-{i}")
        for i in range(3)
    }
    service = GeminiService(settings, client_factory=lambda api_key, **kwargs: clients[api_key])

    try:
        def worker(q_num: int) -> str:
            return service.answer(f"Concurrent question {q_num}")

        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
            futures = [executor.submit(worker, i) for i in range(15)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        assert len(results) == 15
        total_calls = sum(len(c.models.calls) for c in clients.values())
        assert total_calls == 15
        # Verify calls were distributed across all 3 keys
        for c in clients.values():
            assert len(c.models.calls) > 0
    finally:
        service.close()


def test_client_cleanup_during_application_shutdown(monkeypatch: pytest.MonkeyPatch):
    keys_config = [
        {"key": "key-clean-1", "quota_group": "grp-1"},
        {"key": "key-clean-2", "quota_group": "grp-2"},
    ]
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///:memory:")
    monkeypatch.setenv("AUTH_SECRET_KEY", "test-auth-secret-key-0123456789abcdef")
    settings = Settings(_env_file=None, gemini_api_keys=keys_config)

    clients = {
        "key-clean-1": FakeClient("c1"),
        "key-clean-2": FakeClient("c2"),
    }
    service = GeminiService(settings, client_factory=lambda api_key, **kwargs: clients[api_key])

    assert clients["key-clean-1"].closed is False
    assert clients["key-clean-2"].closed is False

    service.close()

    assert clients["key-clean-1"].closed is True
    assert clients["key-clean-2"].closed is True


def test_rpm_violation_with_unrelated_daily_project_name_avoids_daily_cooldown(
    monkeypatch: pytest.MonkeyPatch,
):
    """An RPM rate-limit violation with a project name containing 'daily' does NOT cause a 24-hour cooldown."""
    import time
    keys_config = [
        {"key": "key-rpm-daily-proj", "quota_group": "group-daily-proj"},
        {"key": "key-backup", "quota_group": "group-backup"},
    ]
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///:memory:")
    monkeypatch.setenv("AUTH_SECRET_KEY", "test-auth-secret-key-0123456789abcdef")
    settings = Settings(_env_file=None, gemini_api_keys=keys_config)

    rpm_err = errors.APIError(
        429,
        {
            "error": {
                "code": 429,
                "message": "Resource has been exhausted (e.g. check quota).",
                "status": "RESOURCE_EXHAUSTED",
                "details": [
                    {
                        "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                        "violations": [
                            {
                                "subject": "project:my-daily-deals-store",
                                "quota_metric": "generate_content_requests_per_minute",
                                "description": "Quota exceeded for quota metric 'GenerateContent requests per minute' and limit 'Requests per minute'.",
                            }
                        ],
                    }
                ],
            }
        },
    )
    clients = {
        "key-rpm-daily-proj": FakeClient(rpm_err),
        "key-backup": FakeClient("backup success"),
    }

    start_time = time.time()
    service = GeminiService(settings, client_factory=lambda api_key, **kwargs: clients[api_key])
    try:
        res = service.answer("Test RPM with daily project name")
        assert res == "backup success"
        cooldown = service._quota_group_cooldowns["group-daily-proj"] - start_time
        # Must be sensible ~60s RPM cooldown, NOT 86400s (24h) or >= 3600s
        assert 55.0 <= cooldown <= 65.0
    finally:
        service.close()


def test_explicit_short_retry_duration_on_daily_quota_is_respected(
    monkeypatch: pytest.MonkeyPatch,
):
    """An explicit 120-second retry duration on a daily quota error is respected without artificial 1-hour floor."""
    import time
    keys_config = [
        {"key": "key-daily-120", "quota_group": "group-daily-120"},
        {"key": "key-backup", "quota_group": "group-backup"},
    ]
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///:memory:")
    monkeypatch.setenv("AUTH_SECRET_KEY", "test-auth-secret-key-0123456789abcdef")
    settings = Settings(_env_file=None, gemini_api_keys=keys_config)

    daily_short_err = errors.APIError(
        429,
        {
            "error": {
                "code": 429,
                "message": "Quota exceeded for quota metric 'Requests per day'. Daily limit exceeded.",
                "details": [
                    {
                        "@type": "type.googleapis.com/google.rpc.RetryInfo",
                        "retry_delay": "120s",
                    }
                ],
            }
        },
    )
    clients = {
        "key-daily-120": FakeClient(daily_short_err),
        "key-backup": FakeClient("backup success"),
    }

    start_time = time.time()
    service = GeminiService(settings, client_factory=lambda api_key, **kwargs: clients[api_key])
    try:
        res = service.answer("Test daily with 120s explicit duration")
        assert res == "backup success"
        cooldown = service._quota_group_cooldowns["group-daily-120"] - start_time
        # Must respect explicit 120s duration, NOT forced to >= 3600s
        assert 115.0 <= cooldown <= 125.0
    finally:
        service.close()


def test_missing_retry_information_on_daily_quota_uses_documented_fallback(
    monkeypatch: pytest.MonkeyPatch,
):
    """Missing retry duration on daily quota uses the documented 24-hour fallback."""
    import time
    keys_config = [
        {"key": "key-daily-nofallback", "quota_group": "group-daily-nofallback"},
        {"key": "key-backup", "quota_group": "group-backup"},
    ]
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///:memory:")
    monkeypatch.setenv("AUTH_SECRET_KEY", "test-auth-secret-key-0123456789abcdef")
    settings = Settings(_env_file=None, gemini_api_keys=keys_config)

    daily_err_no_delay = errors.APIError(
        429,
        {
            "error": {
                "code": 429,
                "message": "Resource has been exhausted. You have exceeded your daily requests limit.",
            }
        },
    )
    clients = {
        "key-daily-nofallback": FakeClient(daily_err_no_delay),
        "key-backup": FakeClient("backup success"),
    }

    start_time = time.time()
    service = GeminiService(settings, client_factory=lambda api_key, **kwargs: clients[api_key])
    try:
        res = service.answer("Test daily with missing retry info")
        assert res == "backup success"
        cooldown = service._quota_group_cooldowns["group-daily-nofallback"] - start_time
        # Must fall back to conservative 86400s (24 hours)
        assert cooldown >= 86000.0
    finally:
        service.close()


def test_structured_protobuf_duration_parsed_correctly(
    monkeypatch: pytest.MonkeyPatch,
):
    """Structured duration dictionary and protobuf Duration object are accurately converted with nanos."""
    import time
    keys_config = [
        {"key": "key-proto-dict", "quota_group": "group-proto-dict"},
        {"key": "key-backup", "quota_group": "group-backup"},
    ]
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///:memory:")
    monkeypatch.setenv("AUTH_SECRET_KEY", "test-auth-secret-key-0123456789abcdef")
    settings = Settings(_env_file=None, gemini_api_keys=keys_config)

    # 1. Test dictionary with seconds and nanos (e.g. 45 seconds + 500_000_000 nanos = 45.5s)
    proto_dict_err = errors.APIError(
        429,
        {
            "error": {
                "code": 429,
                "message": "Resource exhausted",
                "details": [
                    {
                        "@type": "type.googleapis.com/google.rpc.RetryInfo",
                        "retry_delay": {"seconds": 45, "nanos": 500000000},
                    }
                ],
            }
        },
    )
    clients = {
        "key-proto-dict": FakeClient(proto_dict_err),
        "key-backup": FakeClient("backup success"),
    }

    start_time = time.time()
    service = GeminiService(settings, client_factory=lambda api_key, **kwargs: clients[api_key])
    try:
        res = service.answer("Test protobuf duration dict")
        assert res == "backup success"
        cooldown = service._quota_group_cooldowns["group-proto-dict"] - start_time
        assert 45.0 <= cooldown <= 46.5
    finally:
        service.close()

    # 2. Test direct protobuf Duration object with attributes (.seconds and .nanos)
    class MockProtobufDuration:
        def __init__(self, seconds: int, nanos: int):
            self.seconds = seconds
            self.nanos = nanos

    class MockRetryInfo:
        def __init__(self, duration: object):
            self.retry_delay = duration

    proto_obj_err = errors.APIError(
        429,
        {
            "error": {
                "code": 429,
                "message": "Resource exhausted",
                "details": [MockRetryInfo(MockProtobufDuration(seconds=75, nanos=250000000))],
            }
        },
    )
    clients_obj = {
        "key-proto-dict": FakeClient(proto_obj_err),
        "key-backup": FakeClient("backup success 2"),
    }
    service_obj = GeminiService(
        settings, client_factory=lambda api_key, **kwargs: clients_obj[api_key]
    )
    try:
        start_time2 = time.time()
        res2 = service_obj.answer("Test protobuf duration object")
        assert res2 == "backup success 2"
        cooldown2 = service_obj._quota_group_cooldowns["group-proto-dict"] - start_time2
        assert 74.5 <= cooldown2 <= 76.0
    finally:
        service_obj.close()


def test_invalid_or_negative_retry_durations_ignored_safely(
    monkeypatch: pytest.MonkeyPatch,
):
    """Negative, non-numeric, or malformed retry durations are ignored safely and fall back to default."""
    import time
    keys_config = [
        {"key": "key-negative", "quota_group": "group-negative"},
        {"key": "key-backup", "quota_group": "group-backup"},
    ]
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///:memory:")
    monkeypatch.setenv("AUTH_SECRET_KEY", "test-auth-secret-key-0123456789abcdef")
    settings = Settings(_env_file=None, gemini_api_keys=keys_config)

    for invalid_val in [-30, "-15s", "not-a-number", {"seconds": -5}]:
        err = errors.APIError(
            429,
            {
                "error": {
                    "code": 429,
                    "message": "Quota exceeded for quota metric 'Requests per minute'.",
                    "details": [
                        {
                            "@type": "type.googleapis.com/google.rpc.RetryInfo",
                            "retry_delay": invalid_val,
                        }
                    ],
                }
            },
        )
        clients = {
            "key-negative": FakeClient(err),
            "key-backup": FakeClient("backup success"),
        }
        start_time = time.time()
        service = GeminiService(settings, client_factory=lambda api_key, **kwargs: clients[api_key])
        try:
            res = service.answer("Test invalid duration")
            assert res == "backup success"
            cooldown = service._quota_group_cooldowns["group-negative"] - start_time
            # Must safely fall back to 60s default, not negative or error
            assert 55.0 <= cooldown <= 65.0
        finally:
            service.close()
