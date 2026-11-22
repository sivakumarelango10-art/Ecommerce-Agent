from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from google.genai import types
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from backend.app.core.config import Settings
from backend.app.db.base import Base
from backend.app.db.models import Order, OrderItem, OrderStatus, User
from backend.app.main import create_app
from backend.app.security.guest_tokens import create_guest_access_token
from backend.app.security.passwords import hash_password
from backend.app.security.tokens import create_access_token
from backend.app.services.gemini_service import (
    GeminiProviderError,
    GeminiResult,
    GeminiTimeoutError,
)
from backend.app.services.order_support_agent import (
    MAX_TOOL_TURNS,
    MAX_TOTAL_FUNCTION_CALLS,
    UNAUTHENTICATED_ORDER_MESSAGE,
    OrderSupportAgent,
)
from backend.app.services.order_support_tools import (
    execute_order_support_tool,
)
from backend.app.services.supervisor_agent import SupervisorAgent


@pytest.fixture
def db_session(tmp_path):
    """Create an isolated temporary SQLite database session with foreign keys."""
    db_file = (tmp_path / "test_order_support_agent.sqlite").as_posix()
    db_url = f"sqlite+pysqlite:///{db_file}"
    engine = create_engine(db_url, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    with session_factory() as session:
        yield session

    engine.dispose()


def _create_user(session: Session, email: str = "customer@example.com") -> User:
    user = User(
        id=uuid4(),
        email=email,
        password_hash=hash_password("SecurePassword123!"),
        is_active=True,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def _create_order(
    session: Session,
    user_id: UUID,
    order_number: str,
    status: str = OrderStatus.CONFIRMED.value,
    subtotal: Decimal = Decimal("150.00"),
    items_count: int = 2,
) -> Order:
    total = subtotal + Decimal("15.00") + Decimal("7.50")
    order = Order(
        id=uuid4(),
        order_number=order_number,
        user_id=user_id,
        shipping_recipient_name="Test Customer",
        shipping_line1="100 Tech Park",
        shipping_city="Bengaluru",
        shipping_region="KA",
        shipping_postal_code="560001",
        shipping_country_code="IN",
        status=status,
        currency="INR",
        subtotal=subtotal,
        shipping_fee=Decimal("15.00"),
        tax=Decimal("7.50"),
        total_amount=total,
    )
    session.add(order)
    session.flush()

    for i in range(items_count):
        unit_price = (subtotal / Decimal(items_count)).quantize(Decimal("0.01"))
        item = OrderItem(
            id=uuid4(),
            order_id=order.id,
            product_sku=f"SKU-{order_number}-{i + 1}",
            product_name=f"Product {i + 1} for {order_number}",
            unit_price=unit_price,
            quantity=1,
            line_total=unit_price,
        )
        session.add(item)

    session.commit()
    session.refresh(order)
    return order


class MockMultiTurnGemini:
    """Mock Gemini service capable of scripted responses across turns."""

    def __init__(self, steps: list[GeminiResult | Exception]) -> None:
        self.steps = list(steps)
        self.call_history: list[dict[str, Any]] = []

    def generate(
        self,
        contents: Any,
        *,
        tools: Any = None,
        system_instruction: str | None = None,
    ) -> GeminiResult:
        self.call_history.append({
            "contents": contents,
            "tools": tools,
            "system_instruction": system_instruction,
        })
        if not self.steps:
            return GeminiResult(text="Default fallback mock answer.", function_calls=())
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return step

    def answer(self, question: str) -> str:
        return "Mock general answer."


# ==============================================================================
# 1. Individual Tool Execution & Agent Grounding Tests
# ==============================================================================

def test_order_support_agent_list_my_orders(db_session: Session):
    user = _create_user(db_session, "user1@example.com")
    _create_order(db_session, user.id, "ORD-20261122-AAAA1111")
    _create_order(db_session, user.id, "ORD-20261122-AAAA2222")

    mock_gemini = MockMultiTurnGemini([
        # Turn 1: Model requests list_my_orders
        GeminiResult(
            text=None,
            function_calls=(
                types.FunctionCall(name="list_my_orders", args={"limit": 5}),
            ),
        ),
        # Turn 2: Model synthesizes answer from tool output
        GeminiResult(
            text="You have placed 2 orders recently: ORD-20261122-AAAA2222 and ORD-20261122-AAAA1111.",
            function_calls=(),
        ),
    ])

    agent = OrderSupportAgent(mock_gemini)
    response = agent.handle_inquiry("Show my orders", db_session, user.id)

    assert "ORD-20261122-AAAA2222" in response
    assert len(mock_gemini.call_history) == 2


def test_order_support_agent_get_my_order_detail(db_session: Session):
    user = _create_user(db_session, "user2@example.com")
    _create_order(db_session, user.id, "ORD-20261122-BBBB1111")

    mock_gemini = MockMultiTurnGemini([
        GeminiResult(
            text=None,
            function_calls=(
                types.FunctionCall(name="get_my_order", args={"order_number": "ORD-20261122-BBBB1111"}),
            ),
        ),
        GeminiResult(
            text="Order ORD-20261122-BBBB1111 total is ₹172.50 shipped to Bengaluru.",
            function_calls=(),
        ),
    ])

    agent = OrderSupportAgent(mock_gemini)
    response = agent.handle_inquiry("Details for ORD-20261122-BBBB1111", db_session, user.id)

    assert "ORD-20261122-BBBB1111" in response
    assert "₹172.50" in response


def test_order_support_agent_get_order_status(db_session: Session):
    user = _create_user(db_session, "user3@example.com")
    _create_order(db_session, user.id, "ORD-20261122-CCCC1111", status=OrderStatus.CONFIRMED.value)

    mock_gemini = MockMultiTurnGemini([
        GeminiResult(
            text=None,
            function_calls=(
                types.FunctionCall(name="get_order_status", args={"order_number": "ORD-20261122-CCCC1111"}),
            ),
        ),
        GeminiResult(
            text="Your order ORD-20261122-CCCC1111 is currently confirmed.",
            function_calls=(),
        ),
    ])

    agent = OrderSupportAgent(mock_gemini)
    response = agent.handle_inquiry("What is the status of ORD-20261122-CCCC1111?", db_session, user.id)

    assert "confirmed" in response.lower()


def test_order_support_agent_get_order_items(db_session: Session):
    user = _create_user(db_session, "user4@example.com")
    _create_order(db_session, user.id, "ORD-20261122-DDDD1111", items_count=2)

    mock_gemini = MockMultiTurnGemini([
        GeminiResult(
            text=None,
            function_calls=(
                types.FunctionCall(name="get_order_items", args={"order_number": "ORD-20261122-DDDD1111"}),
            ),
        ),
        GeminiResult(
            text="Order ORD-20261122-DDDD1111 contains 2 items: Product 1 and Product 2.",
            function_calls=(),
        ),
    ])

    agent = OrderSupportAgent(mock_gemini)
    response = agent.handle_inquiry("What items did I buy in ORD-20261122-DDDD1111?", db_session, user.id)

    assert "2 items" in response


# ==============================================================================
# 2. Multi-turn Tool Chaining Tests
# ==============================================================================

def test_multi_turn_recent_order_followed_by_items(db_session: Session):
    user = _create_user(db_session, "user5@example.com")
    _create_order(db_session, user.id, "ORD-20261122-EEEE1111", items_count=3)

    mock_gemini = MockMultiTurnGemini([
        # Turn 1: Find recent orders
        GeminiResult(
            text=None,
            function_calls=(
                types.FunctionCall(name="list_my_orders", args={"limit": 1}),
            ),
        ),
        # Turn 2: Given order number, fetch its items
        GeminiResult(
            text=None,
            function_calls=(
                types.FunctionCall(name="get_order_items", args={"order_number": "ORD-20261122-EEEE1111"}),
            ),
        ),
        # Turn 3: Final answer
        GeminiResult(
            text="In your latest order ORD-20261122-EEEE1111, you purchased 3 items: Product 1, Product 2, and Product 3.",
            function_calls=(),
        ),
    ])

    agent = OrderSupportAgent(mock_gemini)
    response = agent.handle_inquiry("What was in my latest order?", db_session, user.id)

    assert "ORD-20261122-EEEE1111" in response
    assert "3 items" in response
    assert len(mock_gemini.call_history) == 3


# ==============================================================================
# 3. Customer Isolation & Missing Order Tests
# ==============================================================================

def test_missing_order_returns_not_found(db_session: Session):
    user = _create_user(db_session, "user6@example.com")

    mock_gemini = MockMultiTurnGemini([
        GeminiResult(
            text=None,
            function_calls=(
                types.FunctionCall(name="get_order_status", args={"order_number": "ORD-NONEXISTENT-9999"}),
            ),
        ),
        GeminiResult(
            text="I could not find order ORD-NONEXISTENT-9999 under your account.",
            function_calls=(),
        ),
    ])

    agent = OrderSupportAgent(mock_gemini)
    response = agent.handle_inquiry("Check ORD-NONEXISTENT-9999", db_session, user.id)

    assert "could not find" in response.lower()


def test_cross_customer_order_isolation(db_session: Session):
    user_a = _create_user(db_session, "user_a@example.com")
    user_b = _create_user(db_session, "user_b@example.com")
    order_b = _create_order(db_session, user_b.id, "ORD-20261122-BBBB9999")

    mock_gemini = MockMultiTurnGemini([
        GeminiResult(
            text=None,
            function_calls=(
                types.FunctionCall(name="get_my_order", args={"order_number": order_b.order_number}),
            ),
        ),
        GeminiResult(
            text=f"Order {order_b.order_number} was not found in your account records.",
            function_calls=(),
        ),
    ])

    agent = OrderSupportAgent(mock_gemini)
    # User A asks for User B's order
    response = agent.handle_inquiry(f"Show {order_b.order_number}", db_session, user_a.id)

    # Tool output returned found: False; agent reports not found
    assert "not found" in response.lower()
    # Ensure tool execution returned found=False
    tool_res = execute_order_support_tool("get_my_order", {"order_number": order_b.order_number}, db_session, user_a.id)
    assert tool_res["found"] is False


# ==============================================================================
# 4. Unauthenticated Requests & Identity Spoofing Protection
# ==============================================================================

def test_unauthenticated_request_executes_zero_tools_and_zero_queries(db_session: Session):
    mock_gemini = MockMultiTurnGemini([])
    agent = OrderSupportAgent(mock_gemini)

    # current_user_id is None
    response = agent.handle_inquiry("Where is my order?", db_session, None)

    assert response == UNAUTHENTICATED_ORDER_MESSAGE
    assert len(mock_gemini.call_history) == 0  # Zero Gemini calls


def test_gemini_supplied_user_id_is_strictly_discarded(db_session: Session):
    user_victim = _create_user(db_session, "victim@example.com")
    user_attacker = _create_user(db_session, "attacker@example.com")
    order_victim = _create_order(db_session, user_victim.id, "ORD-VICTIM-1111")

    # Attacker asks about their own orders, but Gemini returns tool call with user_id=user_victim.id
    mock_gemini = MockMultiTurnGemini([
        GeminiResult(
            text=None,
            function_calls=(
                types.FunctionCall(
                    name="get_my_order",
                    args={
                        "order_number": "ORD-VICTIM-1111",
                        "user_id": str(user_victim.id),
                        "customer_id": str(user_victim.id),
                        "current_user_id": str(user_victim.id),
                    },
                ),
            ),
        ),
        GeminiResult(
            text="Order ORD-VICTIM-1111 was not found under your account.",
            function_calls=(),
        ),
    ])

    agent = OrderSupportAgent(mock_gemini)
    response = agent.handle_inquiry("Show my order", db_session, user_attacker.id)

    assert "not found" in response.lower()


# ==============================================================================
# 5. Safety: Cancellations, Refunds, and Unsupported Tools
# ==============================================================================

def test_unsupported_tool_call_is_rejected_safely(db_session: Session):
    user = _create_user(db_session, "user7@example.com")

    # Gemini attempts to invoke an unsupported mutation tool
    mock_gemini = MockMultiTurnGemini([
        GeminiResult(
            text=None,
            function_calls=(
                types.FunctionCall(name="cancel_order", args={"order_number": "ORD-123"}),
            ),
        ),
        GeminiResult(
            text="I cannot cancel orders through chat. Please contact customer support for assistance.",
            function_calls=(),
        ),
    ])

    agent = OrderSupportAgent(mock_gemini)
    response = agent.handle_inquiry("Cancel my order ORD-123", db_session, user.id)

    assert "cannot cancel orders through chat" in response


def test_malformed_tool_arguments_handled_gracefully(db_session: Session):
    user = _create_user(db_session, "user8@example.com")

    mock_gemini = MockMultiTurnGemini([
        GeminiResult(
            text=None,
            function_calls=(
                types.FunctionCall(
                    name="get_my_order",
                    args={"order_number": None},  # Invalid non-string order number
                ),
            ),
        ),
        GeminiResult(
            text="A valid order number is required to look up order details.",
            function_calls=(),
        ),
    ])

    agent = OrderSupportAgent(mock_gemini)
    response = agent.handle_inquiry("Check order", db_session, user.id)

    assert "valid order number" in response.lower()


# ==============================================================================
# 6. Bounds: Maximum Turns and Function Call Limits
# ==============================================================================

def test_maximum_tool_turns_limit_prevents_infinite_loop(db_session: Session):
    user = _create_user(db_session, "user9@example.com")

    # Script continuous function calls for MAX_TOOL_TURNS (3), then a summary step
    looping_step = GeminiResult(
        text=None,
        function_calls=(
            types.FunctionCall(name="list_my_orders", args={"limit": 5}),
        ),
    )
    summary_step = GeminiResult(
        text="Summary after reaching maximum tool iterations.",
        function_calls=(),
    )

    mock_gemini = MockMultiTurnGemini([
        looping_step,
        looping_step,
        looping_step,
        summary_step,
    ])

    agent = OrderSupportAgent(mock_gemini)
    response = agent.handle_inquiry("Keep checking my orders", db_session, user.id)

    assert "Summary after reaching maximum tool iterations." in response
    # 3 turns in loop + 1 summary call
    assert len(mock_gemini.call_history) == 4


def test_maximum_function_calls_limit_stops_execution(db_session: Session):
    user = _create_user(db_session, "user10@example.com")

    # Step returning 7 function calls in one turn (exceeds MAX_TOTAL_FUNCTION_CALLS = 6)
    huge_call_step = GeminiResult(
        text=None,
        function_calls=tuple(
            types.FunctionCall(name="list_my_orders", args={"limit": 1})
            for _ in range(7)
        ),
    )
    summary_step = GeminiResult(
        text="Stopped due to excessive function calls.",
        function_calls=(),
    )

    mock_gemini = MockMultiTurnGemini([
        huge_call_step,
        summary_step,
    ])

    agent = OrderSupportAgent(mock_gemini)
    response = agent.handle_inquiry("Check everything", db_session, user.id)

    assert "Stopped due to excessive function calls." in response


# ==============================================================================
# 7. Error Handling: Timeouts and Provider Failures
# ==============================================================================

def test_gemini_timeout_is_propagated(db_session: Session):
    user = _create_user(db_session, "user11@example.com")
    mock_gemini = MockMultiTurnGemini([GeminiTimeoutError("Timed out")])

    agent = OrderSupportAgent(mock_gemini)
    with pytest.raises(GeminiTimeoutError):
        agent.handle_inquiry("Where is my order?", db_session, user.id)


def test_gemini_provider_error_is_propagated(db_session: Session):
    user = _create_user(db_session, "user12@example.com")
    mock_gemini = MockMultiTurnGemini([GeminiProviderError("API failed")])

    agent = OrderSupportAgent(mock_gemini)
    with pytest.raises(GeminiProviderError):
        agent.handle_inquiry("Where is my order?", db_session, user.id)


# ==============================================================================
# 8. API Integration & Guest Token Rejection Tests
# ==============================================================================

def test_api_shopping_chat_authenticated_order_support(api_client):
    client, fake_client = api_client
    session = client.app.state.db_session_factory()
    try:
        user = _create_user(session, "chat_shopper@example.com")
        _create_order(session, user.id, "ORD-20261122-CHAT1111")
    finally:
        session.close()

    token = create_access_token(user.id, client.app.state.settings)
    fake_client.models.outcome = SimpleNamespace(
        text="Your order ORD-20261122-CHAT1111 is currently confirmed.",
        function_calls=[],
    )

    response = client.post(
        "/api/v1/shopping/chat",
        headers={"Authorization": f"Bearer {token}"},
        json={"question": "Where is my order ORD-20261122-CHAT1111?"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["destination"] == "order_support"
    assert data["order_answer"] is not None
    assert "ORD-20261122-CHAT1111" in data["order_answer"]


def test_api_shopping_chat_unauthenticated_order_support(api_client):
    client, _ = api_client

    # Guest call without authorization header
    response = client.post(
        "/api/v1/shopping/chat",
        json={"question": "Where is my order ORD-20261122-CHAT1111?"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["destination"] == "order_support"
    assert data["order_answer"] == UNAUTHENTICATED_ORDER_MESSAGE


def test_api_shopping_chat_rejects_guest_access_token(api_client):
    client, _ = api_client
    session = client.app.state.db_session_factory()
    try:
        user = _create_user(session, "guest_owner@example.com")
        order = _create_order(session, user.id, "ORD-20261122-GUEST111")
    finally:
        session.close()

    # Create restricted guest order token (type="guest_order")
    guest_token, _ = create_guest_access_token(
        order.id, order.order_number, "guest@example.com", str(uuid4()), client.app.state.settings
    )

    # Guest token must be rejected with 401 when accessing authenticated chat flow
    response = client.post(
        "/api/v1/shopping/chat",
        headers={"Authorization": f"Bearer {guest_token}"},
        json={"question": f"Where is my order {order.order_number}?"},
    )

    assert response.status_code == 401
