from decimal import Decimal
import json
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session

import agent
from backend.app.core.config import Settings
from backend.app.core.constants import (
    CATALOGUE_UNAVAILABLE_MESSAGE,
    POLICY_UNAVAILABLE_MESSAGE,
)
from backend.app.schemas.shopping import ShoppingSearchRequest, SortOrder
from backend.app.services.gemini_service import GeminiService
from backend.app.services.supervisor_agent import SupervisorAgent


class SpyGeminiService:
    """Mock GeminiService tracking calls and raising if an unpermitted method is invoked."""

    def __init__(self, answer_return: str = "Mock answer"):
        self.answer_return = answer_return
        self.answer_calls: list[str] = []
        self.generate_calls: list[str] = []

    def answer(self, question: str) -> str:
        self.answer_calls.append(question)
        return self.answer_return

    def generate(self, prompt: str, tools=None):
        self.generate_calls.append(prompt)
        mock_result = MagicMock()
        mock_result.function_calls = []
        return mock_result

    def close(self):
        pass


@pytest.fixture
def mock_sensitive_settings():
    return Settings(
        gemini_api_key="mock-api-key-safe",
        database_url="postgresql+psycopg://secret_user:SuperSecretPassword123!@secret-db-host.internal:5432/ecommerce_agent_dev",
        auth_secret_key="a-very-secret-auth-key-at-least-32-chars-long",
        catalogue_currency="INR",
    )


# --- 1. Database Connection Failure During Shopping Request ---

def test_database_connection_failure_during_shopping_request(monkeypatch, mock_sensitive_settings):
    """If DB connection fails during a shopping request, agent must NOT call Gemini answer."""
    spy_service = SpyGeminiService("Hallucinated headphones for ₹999")

    monkeypatch.setattr(agent, "load_settings", lambda: mock_sensitive_settings)
    monkeypatch.setattr(agent, "GeminiService", lambda s: spy_service)

    def failing_db_engine(*args, **kwargs):
        raise OperationalError(
            "connection to server at 'secret-db-host.internal' (10.0.0.5), port 5432 failed: password authentication failed for user 'secret_user'",
            params=None,
            orig=Exception("authentication failed"),
        )

    monkeypatch.setattr(agent, "create_database_engine", failing_db_engine)

    result = agent.answer_question("Find wireless headphones under ₹5000")

    # 1. Must return catalogue unavailable message
    assert result == CATALOGUE_UNAVAILABLE_MESSAGE
    # 2. Must NOT have called Gemini general answer
    assert len(spy_service.answer_calls) == 0
    # 3. Must NOT expose database credentials, secrets, or host in response
    assert "SuperSecretPassword123!" not in result
    assert "secret_user" not in result
    assert "secret-db-host.internal" not in result


# --- 2. Catalogue Query Failure During Shopping Request ---

def test_catalogue_query_failure_during_shopping_request(monkeypatch, mock_sensitive_settings):
    """If catalogue SQL query fails during a shopping request, agent must NOT call Gemini answer."""
    spy_service = SpyGeminiService("Hallucinated shoes for ₹2500")

    monkeypatch.setattr(agent, "load_settings", lambda: mock_sensitive_settings)
    monkeypatch.setattr(agent, "GeminiService", lambda s: spy_service)

    # Mock engine and session
    mock_engine = MagicMock()
    monkeypatch.setattr(agent, "create_database_engine", lambda url: mock_engine)

    def failing_session_factory():
        class FailingSessionContext:
            def __enter__(self):
                raise SQLAlchemyError("relation 'products' does not exist; secret schema error")
            def __exit__(self, exc_type, exc_val, exc_tb):
                pass
        return FailingSessionContext()

    monkeypatch.setattr(agent, "create_session_factory", lambda eng: failing_session_factory)

    result = agent.answer_question("Buy running shoes under ₹2000")

    # 1. Must return catalogue unavailable message
    assert result == CATALOGUE_UNAVAILABLE_MESSAGE
    # 2. Must NOT have called Gemini general answer
    assert len(spy_service.answer_calls) == 0
    # 3. Must NOT expose SQL error or sensitive internals
    assert "relation 'products'" not in result
    assert "SQLAlchemy" not in result


# --- 3. No Gemini General-Answer Fallback After Failures ---

def test_no_gemini_answer_fallback_on_any_shopping_failure(monkeypatch, mock_sensitive_settings):
    """Strict verification that GeminiService.answer is NEVER called for shopping queries on failure."""
    forbidden_service = MagicMock()
    forbidden_service.answer.side_effect = AssertionError("GeminiService.answer must NOT be called on catalogue failure!")

    mock_result = MagicMock()
    mock_result.function_calls = []
    forbidden_service.generate.return_value = mock_result

    monkeypatch.setattr(agent, "load_settings", lambda: mock_sensitive_settings)
    monkeypatch.setattr(agent, "GeminiService", lambda s: forbidden_service)
    monkeypatch.setattr(
        agent,
        "create_database_engine",
        MagicMock(side_effect=Exception("Database network partition timeout")),
    )

    shopping_queries = [
        "Find running shoes under ₹2000",
        "Buy laptop under ₹70,000",
        "Search headphones in stock",
        "Recommend products in audio",
    ]

    for q in shopping_queries:
        res = agent.answer_question(q)
        assert res == CATALOGUE_UNAVAILABLE_MESSAGE
        assert forbidden_service.answer.call_count == 0


# --- 4. General Informational Questions Still Work ---

def test_general_informational_questions_use_gemini_service(monkeypatch, mock_sensitive_settings):
    """General informational advice (not product discovery) correctly calls GeminiService.answer."""
    expected_advice = (
        "When choosing headphones, consider sound signature (neutral vs bass-heavy), "
        "battery life, active noise cancellation, and ear-cup comfort."
    )
    spy_service = SpyGeminiService(expected_advice)

    monkeypatch.setattr(agent, "load_settings", lambda: mock_sensitive_settings)
    monkeypatch.setattr(agent, "GeminiService", lambda s: spy_service)

    result = agent.answer_question("What should I compare when choosing online products?")

    # 1. Must return the Gemini advice
    assert result == expected_advice
    # 2. Gemini answer was indeed called
    assert len(spy_service.answer_calls) == 1
    assert "What should I compare" in spy_service.answer_calls[0]


# --- 5. Policy Questions Return Verified Message Without Gemini or DB ---

@pytest.mark.parametrize(
    "policy_question",
    [
        "What is your return policy?",
        "Can I get a refund on my purchase?",
        "How many days do I have to return an item?",
        "What is your shipping policy?",
        "Do you offer a warranty on products?",
        "How does delivery work?",
    ],
)
def test_policy_questions_return_unavailable_message_without_gemini_or_db(
    monkeypatch, mock_sensitive_settings, policy_question
):
    """Policy questions must immediately return verified POLICY_UNAVAILABLE_MESSAGE without touching DB or Gemini."""
    spy_service = MagicMock()
    spy_service.answer.side_effect = AssertionError("Gemini answer must not be called for policy questions!")
    spy_service.generate.side_effect = AssertionError("Gemini generate must not be called for policy questions!")

    failing_engine = MagicMock(side_effect=AssertionError("DB engine must not be initialized for policy questions!"))

    monkeypatch.setattr(agent, "load_settings", lambda: mock_sensitive_settings)
    monkeypatch.setattr(agent, "GeminiService", lambda s: spy_service)
    monkeypatch.setattr(agent, "create_database_engine", failing_engine)

    result = agent.answer_question(policy_question)

    assert result == POLICY_UNAVAILABLE_MESSAGE
    assert "not currently available" in result
    assert spy_service.answer.call_count == 0
    assert failing_engine.call_count == 0


# --- 6. Error Messages Do Not Expose Secrets ---

def test_error_messages_do_not_expose_database_credentials_or_secrets(monkeypatch):
    """Verify that even with ultra-sensitive connection strings, no secrets leak."""
    ultra_secret_url = (
        "postgresql+psycopg://super_admin:TopSecretP@ssword!@db-prod.internal:5432/ecommerce_prod"
    )
    secret_settings = Settings(
        gemini_api_key="secret-gemini-key",
        database_url=ultra_secret_url,
        auth_secret_key="ultra-secure-key-at-least-32-bytes-long!",
        catalogue_currency="INR",
    )

    spy_service = SpyGeminiService("Hallucinated text")
    monkeypatch.setattr(agent, "load_settings", lambda: secret_settings)
    monkeypatch.setattr(agent, "GeminiService", lambda s: spy_service)

    def crash_engine(*args, **kwargs):
        raise OperationalError(
            f"Could not connect to {ultra_secret_url}: FATAL password authentication failed",
            params=None,
            orig=Exception("Secret DB Auth Error"),
        )

    monkeypatch.setattr(agent, "create_database_engine", crash_engine)

    result = agent.answer_question("Find headphones under ₹1,000")

    assert result == CATALOGUE_UNAVAILABLE_MESSAGE
    assert "super_admin" not in result
    assert "TopSecretP@ssword!" not in result
    assert "db-prod.internal" not in result
    assert "ecommerce_prod" not in result
    assert "secret-gemini-key" not in result


# --- 7. SupervisorAgent Routing Failure Safety ---

def test_supervisor_routing_failure_safely_handles_shopping(mock_sensitive_settings):
    """When Gemini function-calling routing fails, identifiable shopping query routes to shopping agent safely."""
    failing_gemini = MagicMock()
    failing_gemini.generate.side_effect = Exception("Gemini routing API rate limited / 503")

    # If answer is called with user message directly as general assistant fallback, raise error
    def answer_spy(prompt):
        if "You are a shopping intent extractor" in prompt:
            return "{}"
        raise AssertionError("Gemini answer must not be called as a general fallback for shopping queries!")

    failing_gemini.answer.side_effect = answer_spy

    supervisor = SupervisorAgent(failing_gemini, catalogue_currency="INR")

    # Identifiable shopping request
    dest, conf, reason = supervisor.route("Find running shoes under ₹2000")
    assert dest == "shopping_agent"

    # In process(), if DB search fails, it returns safe unavailable message, not invented gemini text
    mock_session = MagicMock()
    mock_session.scalar.side_effect = SQLAlchemyError("DB failure")
    mock_session.scalars.side_effect = SQLAlchemyError("DB failure")

    resp = supervisor.process("Find running shoes under ₹2000", mock_session)
    assert resp.destination == "shopping_agent"
    assert resp.shopping_response is not None
    assert resp.shopping_response.message == CATALOGUE_UNAVAILABLE_MESSAGE
    assert resp.general_answer is None


def test_supervisor_route_exception_logging_does_not_leak_sensitive_exception_details(caplog):
    """Verify that exceptions in SupervisorAgent.route() log only error_type and do not leak exception messages or fake secrets."""
    import logging

    fake_secret = "AIzaSyFakeSecretGeminiKey12345_DO_NOT_LOG"
    sensitive_exception = RuntimeError(f"Connection to api.google.com failed with key {fake_secret}")

    mock_gemini = MagicMock()
    mock_gemini.generate.side_effect = sensitive_exception

    supervisor = SupervisorAgent(mock_gemini, catalogue_currency="INR")

    with caplog.at_level(logging.WARNING):
        dest, conf, reason = supervisor.route("can someone help me choose something special")

    assert fake_secret not in caplog.text
    assert "Connection to api.google.com failed with key" not in caplog.text

    found_fallback_log = False
    for record in caplog.records:
        if getattr(record, "event", None) == "supervisor_route_fallback":
            found_fallback_log = True
            assert getattr(record, "error_type", None) == "RuntimeError"
            assert not hasattr(record, "error")

    assert found_fallback_log is True


def test_shopping_agent_intent_extraction_exception_logging_sanitized(caplog):
    """Verify that exceptions in ShoppingAgent.extract_intent() log only error_type and do not leak exception message or secrets."""
    import logging
    from backend.app.services.shopping_agent import ShoppingAgent

    fake_secret = "AIzaSyFakeSecretGeminiKey99999_DO_NOT_LOG"
    sensitive_exception = RuntimeError(f"Failed to query Gemini endpoint with secret token {fake_secret}")

    mock_gemini = MagicMock()
    mock_gemini.answer.side_effect = sensitive_exception

    agent = ShoppingAgent(mock_gemini, catalogue_currency="INR")

    with caplog.at_level(logging.WARNING):
        intent = agent.extract_intent("Find running shoes under ₹2000")

    # Intent extraction falls back to deterministic heuristic
    assert intent is not None
    assert fake_secret not in caplog.text
    assert "Failed to query Gemini endpoint" not in caplog.text

    found_fallback_log = False
    for record in caplog.records:
        if getattr(record, "event", None) == "intent_extraction_fallback":
            found_fallback_log = True
            assert getattr(record, "error_type", None) == "RuntimeError"
            assert not hasattr(record, "error")

    assert found_fallback_log is True


def test_shopping_agent_intent_parse_error_logging_sanitized(caplog):
    """Verify that unparseable Gemini responses containing sensitive tokens do not leak raw response or secrets."""
    import logging
    from backend.app.services.shopping_agent import ShoppingAgent

    fake_secret = "ConfidentialCustomerSSN_000_12_3456_DO_NOT_LOG"
    unparseable_raw = f"```json\n{{invalid_json: true, sensitive_payload: '{fake_secret}'}}\n```"

    mock_gemini = MagicMock()
    mock_gemini.answer.return_value = unparseable_raw

    agent = ShoppingAgent(mock_gemini, catalogue_currency="INR")

    with caplog.at_level(logging.WARNING):
        intent = agent.extract_intent("Find running shoes under ₹2000")

    # Intent extraction falls back to heuristic
    assert intent is not None
    assert fake_secret not in caplog.text
    assert unparseable_raw not in caplog.text
    assert "ConfidentialCustomerSSN" not in caplog.text

    found_parse_error_log = False
    for record in caplog.records:
        if getattr(record, "event", None) == "intent_json_parse_error":
            found_parse_error_log = True
            assert getattr(record, "error_type", None) in ("JSONDecodeError", "ValidationError")
            assert not hasattr(record, "raw")
            assert not hasattr(record, "error")

    assert found_parse_error_log is True


# --- 8. Privacy-Log Sanitization Tests ---

def test_supervisor_routing_logging_does_not_leak_gemini_reason(caplog):
    """Verify that Gemini routing reason containing sensitive tokens is never logged in supervisor_routed."""
    import logging
    from types import SimpleNamespace

    fake_secret = "ConfidentialCustomerReason_SSN_111_22_3333_DO_NOT_LOG"
    fake_call = SimpleNamespace(
        name="handoff_to_shopping_agent",
        args={"query": "my request", "reason": f"Routing based on sensitive details: {fake_secret}"},
    )
    mock_result = SimpleNamespace(function_calls=[fake_call])

    mock_gemini = MagicMock()
    mock_gemini.generate.return_value = mock_result

    supervisor = SupervisorAgent(mock_gemini, catalogue_currency="INR")

    with caplog.at_level(logging.INFO):
        # Use a query that does not hit deterministic patterns so it invokes Gemini routing
        dest, conf, reason = supervisor.route("can someone guide me with special personal advice")
        resp = supervisor.process("can someone guide me with special personal advice", session=None)

    # 1. API response reason still contains the explanation
    assert fake_secret in reason
    assert fake_secret in resp.reason

    # 2. But the sensitive reason is NOT in any logs
    assert fake_secret not in caplog.text
    found_routed_log = False
    for record in caplog.records:
        if getattr(record, "event", None) == "supervisor_routed":
            found_routed_log = True
            assert not hasattr(record, "reason")
            assert getattr(record, "destination", None) == "shopping_agent"
            assert getattr(record, "confidence", None) == 0.9

    assert found_routed_log is True


def test_product_discovery_logging_does_not_leak_category(caplog):
    """Verify that catalogue search logging does not log raw user-supplied category names."""
    import logging
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from backend.app.db.base import Base
    from backend.app.services.product_discovery_service import search_catalogue_products

    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)

    fake_secret_cat = "SensitiveMedicalCondition_CancerTreatment_DO_NOT_LOG"

    try:
        with caplog.at_level(logging.INFO):
            products, total = search_catalogue_products(
                session,
                category=fake_secret_cat,
                min_price=Decimal("10.00"),
                max_price=Decimal("50.00"),
            )

        assert fake_secret_cat not in caplog.text
        found_search_log = False
        for record in caplog.records:
            if getattr(record, "event", None) == "catalogue_search":
                found_search_log = True
                assert not hasattr(record, "category")
                assert getattr(record, "has_category", None) is True
                assert getattr(record, "has_min_price", None) is True
                assert getattr(record, "has_max_price", None) is True
        assert found_search_log is True
    finally:
        session.close()
        engine.dispose()


def test_unsupported_currency_logging_does_not_leak_user_supplied_currency(caplog):
    """Verify that unsupported currency logging does not leak raw user-supplied currency strings."""
    import logging
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from backend.app.db.base import Base
    from backend.app.services.shopping_agent import ShoppingAgent

    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)

    fake_secret_curr = "FAKE_KEY"
    agent_inst = ShoppingAgent(catalogue_currency="INR")

    try:
        with caplog.at_level(logging.INFO):
            resp = agent_inst.search(
                ShoppingSearchRequest(category="audio", currency=fake_secret_curr),
                session,
            )

        assert resp.status == "unsupported_currency"
        assert fake_secret_curr in resp.message
        assert fake_secret_curr not in caplog.text

        found_currency_log = False
        for record in caplog.records:
            if getattr(record, "event", None) == "unsupported_currency":
                found_currency_log = True
                assert not hasattr(record, "requested_currency")
                assert getattr(record, "catalogue_currency", None) == "INR"
        assert found_currency_log is True
    finally:
        session.close()
        engine.dispose()
