from collections.abc import Iterator
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend.app.db import models  # noqa: F401
from backend.app.db.base import Base
from backend.app.main import create_app
from backend.app.services import gemini_service


class FakeModels:
    def __init__(self, outcome: object = "A mocked Gemini answer.") -> None:
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
def api_client(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[TestClient, FakeClient]]:
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-sent")
    monkeypatch.setenv("GEMINI_MODEL", "test-gemini-model")
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///:memory:")
    monkeypatch.setenv("AUTH_SECRET_KEY", "test-auth-secret-key-0123456789abcdef")
    monkeypatch.setenv("OUTBOX_WORKER_ENABLED", "false")
    fake_client = FakeClient("A mocked Gemini answer.")

    def fake_client_factory(**kwargs: object) -> FakeClient:
        assert kwargs["api_key"] == "test-key-not-sent"
        return fake_client

    monkeypatch.setattr(gemini_service.genai, "Client", fake_client_factory)

    with TestClient(create_app()) as client:
        Base.metadata.create_all(client.app.state.db_engine)
        yield client, fake_client

    assert fake_client.closed
