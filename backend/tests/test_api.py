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


def test_configuration_error_prevents_startup(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    app = create_app(settings_loader=lambda: load_settings(env_file=None))

    with pytest.raises(ConfigurationError, match="Application configuration is invalid"):
        with TestClient(app):
            pass
