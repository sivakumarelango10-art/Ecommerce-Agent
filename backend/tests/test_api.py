import httpx
import pytest
from fastapi.testclient import TestClient
from google.genai import errors

from backend.app.core.config import ConfigurationError, load_settings
from backend.app.main import create_app


def test_application_startup_and_health_endpoint(api_client):
    client, _ = api_client

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_question_endpoint_uses_gemini_without_order_tools(api_client):
    client, fake_client = api_client

    response = client.post(
        "/api/v1/ask",
        json={"question": "What should I compare when buying headphones?"},
    )

    assert response.status_code == 200
    assert response.json() == {"answer": "A mocked Gemini answer."}
    request_config = fake_client.models.calls[0]["config"]
    assert request_config.tools is None


@pytest.mark.parametrize(
    "payload",
    [
        {"question": ""},
        {"question": "   "},
        {"question": "x" * 4001},
        {"question": "A valid question", "order_id": "ORD1001"},
        {"unexpected": "field"},
    ],
)
def test_invalid_question_requests_are_rejected(api_client, payload):
    client, fake_client = api_client

    response = client.post("/api/v1/ask", json=payload)

    assert response.status_code == 422
    assert fake_client.models.calls == []


def test_gemini_api_error_returns_gateway_error(
    api_client,
):
    client, fake_client = api_client
    fake_client.models.outcome = errors.APIError(503, {"error": "mock failure"})

    response = client.post("/api/v1/ask", json={"question": "A question"})

    assert response.status_code == 502
    assert response.json()["detail"] == "The Gemini service could not complete the request."


def test_gemini_timeout_returns_gateway_timeout(api_client):
    client, fake_client = api_client
    fake_client.models.outcome = httpx.ReadTimeout("mock timeout")

    response = client.post("/api/v1/ask", json={"question": "A question"})

    assert response.status_code == 504
    assert response.json()["detail"] == "The Gemini request timed out."


def test_ask_endpoint_rejects_client_provider_selection_fields(api_client):
    """Client request fields cannot override the provider; extra fields are rejected with 422."""
    client, fake_client = api_client

    # 1. Attempting to override provider to groq
    res_groq = client.post(
        "/api/v1/ask",
        json={"question": "What is StoreFlow?", "provider": "groq"},
    )
    assert res_groq.status_code == 422
    assert "extra_forbidden" in str(res_groq.json())

    # 2. Attempting to pass provider='google_gemini' explicitly
    res_gemini = client.post(
        "/api/v1/ask",
        json={"question": "What is StoreFlow?", "provider": "google_gemini"},
    )
    assert res_gemini.status_code == 422

    # 3. Attempting to override model identifier
    res_model = client.post(
        "/api/v1/ask",
        json={"question": "What is StoreFlow?", "model": "llama-3.3-70b-versatile"},
    )
    assert res_model.status_code == 422

    # 4. Attempting to pass arbitrary provider_id
    res_id = client.post(
        "/api/v1/ask",
        json={"question": "What is StoreFlow?", "provider_id": "prov-id-999"},
    )
    assert res_id.status_code == 422

    # Ensure none of the rejected attempts invoked the AI provider
    assert fake_client.models.calls == []

    # 5. Normal question succeeds through trusted server-side Gemini configuration
    res_valid = client.post(
        "/api/v1/ask",
        json={"question": "What is StoreFlow?"},
    )
    assert res_valid.status_code == 200
    assert res_valid.json() == {"answer": "A mocked Gemini answer."}
    assert len(fake_client.models.calls) == 1


def test_configuration_error_prevents_startup(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    app = create_app(settings_loader=lambda: load_settings(env_file=None))

    with pytest.raises(ConfigurationError, match="Application configuration is invalid"):
        with TestClient(app):
            pass


def test_configuration_error_reports_safe_reason_without_exposing_secrets(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    secret_db_pw = "super_sensitive_db_password_xyz"
    secret_auth = "super_sensitive_auth_secret_0123456789abcdef"
    short_user_enc = "secret_short_val"
    monkeypatch.setenv("ENVIRONMENT", "staging")
    monkeypatch.setenv("DATABASE_URL", f"postgresql+psycopg://user:{secret_db_pw}@db.example.com/neondb")
    monkeypatch.setenv("AUTH_SECRET_KEY", secret_auth)
    monkeypatch.setenv("GEMINI_API_KEY", "super_sensitive_gemini_key_123")
    monkeypatch.setenv("USER_KEY_ENCRYPTION_SECRET", short_user_enc)

    with pytest.raises(ConfigurationError) as exc_info:
        load_settings(env_file=None)

    msg = str(exc_info.value)
    assert "Application configuration is invalid" in msg
    assert "USER_KEY_ENCRYPTION_SECRET must be at least 32 bytes" in msg
    assert secret_db_pw not in msg
    assert secret_auth not in msg
    assert short_user_enc not in msg
    assert secret_db_pw not in caplog.text
    assert secret_auth not in caplog.text
    assert short_user_enc not in caplog.text


def test_configuration_error_identifies_missing_staging_encryption_secret(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("ENVIRONMENT", "staging")
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///:memory:")
    monkeypatch.setenv("AUTH_SECRET_KEY", "test-auth-secret-key-0123456789abcdef")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-sent")
    monkeypatch.delenv("USER_KEY_ENCRYPTION_SECRET", raising=False)

    with pytest.raises(ConfigurationError) as exc_info:
        load_settings(env_file=None)

    assert "USER_KEY_ENCRYPTION_SECRET is required in staging and production" in str(exc_info.value)
