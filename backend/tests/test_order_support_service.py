from datetime import datetime, timezone
from decimal import Decimal
import os
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from backend.app.db.base import Base
from backend.app.db.models import (
    Address,
    Order,
    OrderItem,
    OrderStatus,
    Product,
    User,
)
from backend.app.schemas.order import (
    CustomerOrderDetailSummary,
    CustomerOrderItemSummary,
    CustomerOrderStatusSummary,
)
from backend.app.security.passwords import hash_password
from backend.app.services.order_service import (
    format_customer_order_detail,
    format_customer_order_items,
    format_customer_order_status,
    get_customer_order_by_number,
    get_customer_order_items,
    get_customer_order_status,
    list_customer_orders,
)
from backend.app.services.order_support_tools import (
    ORDER_SUPPORT_TOOL,
    ORDER_SUPPORT_TOOL_DECLARATIONS,
    OrderSupportTools,
    execute_order_support_tool,
    get_my_order,
    get_order_items,
    get_order_status,
    list_my_orders,
)


@pytest.fixture
def db_session(tmp_path):
    """Create an isolated in-memory/temporary SQLite database session with foreign keys."""
    db_file = (tmp_path / "test_order_support.sqlite").as_posix()
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


def _create_test_user(session: Session, email: str = "customer@example.com") -> User:
    user = User(
        id=uuid4(),
        email=email,
        password_hash=hash_password("Password123!"),
        is_active=True,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def _create_test_order(
    session: Session,
    user_id: UUID,
    order_number: str,
    status: str = OrderStatus.CONFIRMED.value,
    subtotal: Decimal = Decimal("100.00"),
    shipping_fee: Decimal = Decimal("10.00"),
    tax: Decimal = Decimal("5.00"),
    items_count: int = 1,
) -> Order:
    total_amount = subtotal + shipping_fee + tax
    order = Order(
        id=uuid4(),
        order_number=order_number,
        user_id=user_id,
        shipping_recipient_name="Test Customer",
        shipping_line1="123 Test St",
        shipping_city="Bengaluru",
        shipping_region="KA",
        shipping_postal_code="560001",
        shipping_country_code="IN",
        status=status,
        currency="INR",
        subtotal=subtotal,
        shipping_fee=shipping_fee,
        tax=tax,
        total_amount=total_amount,
    )
    session.add(order)
    session.flush()

    for i in range(items_count):
        unit_price = (subtotal / Decimal(items_count)).quantize(Decimal("0.01"))
        item = OrderItem(
            id=uuid4(),
            order_id=order.id,
            product_sku=f"SKU-TEST-{order_number}-{i + 1}",
            product_name=f"Test Product {i + 1} for {order_number}",
            unit_price=unit_price,
            quantity=1,
            line_total=unit_price,
        )
        session.add(item)

    session.commit()
    session.refresh(order)
    return order


# ==============================================================================
# 1. Customer Isolation & Authorization Tests
# ==============================================================================

def test_customer_a_can_access_own_order_by_number(db_session: Session):
    user_a = _create_test_user(db_session, "user_a@example.com")
    order_a = _create_test_order(db_session, user_a.id, "ORD-20261121-AAAA1111", items_count=2)

    order = get_customer_order_by_number(db_session, user_a.id, "ORD-20261121-AAAA1111")
    assert order is not None
    assert order.id == order_a.id
    assert order.order_number == "ORD-20261121-AAAA1111"
    assert len(order.items) == 2


def test_customer_a_cannot_access_customer_b_order(db_session: Session):
    user_a = _create_test_user(db_session, "user_a@example.com")
    user_b = _create_test_user(db_session, "user_b@example.com")

    order_b = _create_test_order(db_session, user_b.id, "ORD-20261121-BBBB2222")

    # Service layer queries by user_a must return None for user_b's order
    assert get_customer_order_by_number(db_session, user_a.id, "ORD-20261121-BBBB2222") is None
    assert get_customer_order_status(db_session, user_a.id, "ORD-20261121-BBBB2222") is None
    assert get_customer_order_items(db_session, user_a.id, "ORD-20261121-BBBB2222") is None

    # Tool functions invoked under user_a context must return found=False
    tool_order = get_my_order(db_session, user_a.id, "ORD-20261121-BBBB2222")
    assert tool_order["success"] is True
    assert tool_order["found"] is False
    assert "not found" in tool_order["message"].lower()

    tool_status = get_order_status(db_session, user_a.id, "ORD-20261121-BBBB2222")
    assert tool_status["success"] is True
    assert tool_status["found"] is False

    tool_items = get_order_items(db_session, user_a.id, "ORD-20261121-BBBB2222")
    assert tool_items["success"] is True
    assert tool_items["found"] is False


def test_customer_a_listing_orders_never_includes_customer_b(db_session: Session):
    user_a = _create_test_user(db_session, "user_a@example.com")
    user_b = _create_test_user(db_session, "user_b@example.com")

    _create_test_order(db_session, user_a.id, "ORD-A-001")
    _create_test_order(db_session, user_a.id, "ORD-A-002")
    _create_test_order(db_session, user_b.id, "ORD-B-001")

    # Service query
    orders_a = list_customer_orders(db_session, user_a.id)
    assert len(orders_a) == 2
    order_nums_a = {o.order_number for o in orders_a}
    assert order_nums_a == {"ORD-A-001", "ORD-A-002"}
    assert "ORD-B-001" not in order_nums_a

    orders_b = list_customer_orders(db_session, user_b.id)
    assert len(orders_b) == 1
    assert orders_b[0].order_number == "ORD-B-001"

    # Tool query
    tool_res = list_my_orders(db_session, user_a.id)
    assert tool_res["success"] is True
    assert tool_res["total_returned"] == 2
    tool_nums = {o["order_number"] for o in tool_res["orders"]}
    assert tool_nums == {"ORD-A-001", "ORD-A-002"}
    assert "ORD-B-001" not in tool_nums


# ==============================================================================
# 2. Unknown and Invalid Order Number Handling
# ==============================================================================

def test_unknown_order_number_returns_not_found(db_session: Session):
    user = _create_test_user(db_session, "customer@example.com")

    assert get_customer_order_by_number(db_session, user.id, "ORD-NONEXISTENT") is None
    assert get_customer_order_status(db_session, user.id, "ORD-NONEXISTENT") is None
    assert get_customer_order_items(db_session, user.id, "ORD-NONEXISTENT") is None

    res = get_my_order(db_session, user.id, "ORD-NONEXISTENT")
    assert res == {"success": True, "found": False, "message": "Order 'ORD-NONEXISTENT' not found."}


@pytest.mark.parametrize("invalid_input", ["", "   ", "A" * 65, None, 12345])
def test_invalid_order_number_handling(db_session: Session, invalid_input):
    user = _create_test_user(db_session, "customer@example.com")

    # Service returns None for invalid string inputs
    if isinstance(invalid_input, str):
        assert get_customer_order_by_number(db_session, user.id, invalid_input) is None

    # Tools reject invalid order numbers with structured error
    res_order = get_my_order(db_session, user.id, invalid_input)
    assert res_order["success"] is False
    assert res_order["found"] is False
    assert res_order["error"] == "INVALID_ORDER_NUMBER"

    res_status = get_order_status(db_session, user.id, invalid_input)
    assert res_status["success"] is False
    assert res_status["found"] is False
    assert res_status["error"] == "INVALID_ORDER_NUMBER"

    res_items = get_order_items(db_session, user.id, invalid_input)
    assert res_items["success"] is False
    assert res_items["found"] is False
    assert res_items["error"] == "INVALID_ORDER_NUMBER"


# ==============================================================================
# 3. Pagination Boundaries
# ==============================================================================

def test_pagination_boundaries(db_session: Session):
    user = _create_test_user(db_session, "paginated@example.com")
    for i in range(5):
        _create_test_order(db_session, user.id, f"ORD-PAG-{i + 1:03d}")

    # Limit clamped to minimum 1
    orders_min = list_customer_orders(db_session, user.id, limit=0)
    assert len(orders_min) == 1

    orders_neg = list_customer_orders(db_session, user.id, limit=-10)
    assert len(orders_neg) == 1

    # Limit clamped to max 100
    orders_all = list_customer_orders(db_session, user.id, limit=200)
    assert len(orders_all) == 5

    # Negative offset clamped to 0
    orders_neg_offset = list_customer_orders(db_session, user.id, offset=-5)
    assert len(orders_neg_offset) == 5

    # Tool pagination clamping
    tool_page = list_my_orders(db_session, user.id, limit=2, offset=1)
    assert tool_page["success"] is True
    assert tool_page["total_returned"] == 2
    assert tool_page["limit"] == 2
    assert tool_page["offset"] == 1

    # Large offset returning empty list
    tool_empty = list_my_orders(db_session, user.id, limit=10, offset=100)
    assert tool_empty["success"] is True
    assert tool_empty["total_returned"] == 0
    assert tool_empty["orders"] == []


# ==============================================================================
# 4. Status Variety Tests
# ==============================================================================

@pytest.mark.parametrize(
    "status_val",
    [
        OrderStatus.PENDING.value,
        OrderStatus.CONFIRMED.value,
        OrderStatus.PROCESSING.value,
        OrderStatus.SHIPPED.value,
        OrderStatus.DELIVERED.value,
        OrderStatus.CANCELLED.value,
    ],
)
def test_orders_with_different_statuses(db_session: Session, status_val: str):
    user = _create_test_user(db_session, f"status_{status_val}@example.com")
    order_num = f"ORD-STAT-{status_val.upper()[:4]}"
    _create_test_order(db_session, user.id, order_num, status=status_val)

    # Verify service returns correct status
    status_summary = get_customer_order_status(db_session, user.id, order_num)
    assert status_summary is not None
    assert status_summary.status == status_val
    assert status_summary.order_number == order_num

    # Verify tool returns correct status
    res = get_order_status(db_session, user.id, order_num)
    assert res["success"] is True
    assert res["found"] is True
    assert res["status"] == status_val


# ==============================================================================
# 5. Missing or Malformed Authentication Context
# ==============================================================================

def test_missing_or_malformed_auth_context(db_session: Session):
    order_num = "ORD-TEST-AUTH"

    # None context
    assert list_my_orders(db_session, None)["error"] == "UNAUTHORIZED"
    assert get_my_order(db_session, None, order_num)["error"] == "UNAUTHORIZED"
    assert get_order_status(db_session, None, order_num)["error"] == "UNAUTHORIZED"
    assert get_order_items(db_session, None, order_num)["error"] == "UNAUTHORIZED"
    assert execute_order_support_tool("get_my_order", {"order_number": order_num}, db_session, None)["error"] == "UNAUTHORIZED"

    # Non-UUID string context
    assert list_my_orders(db_session, "invalid-uuid")["error"] == "UNAUTHORIZED"
    assert get_my_order(db_session, "invalid-uuid", order_num)["error"] == "UNAUTHORIZED"

    # OrderSupportTools constructor rejects invalid UUID
    with pytest.raises(ValueError, match="current_user_id must be a valid UUID"):
        OrderSupportTools(db_session, "not-a-uuid")


# ==============================================================================
# 6. Tool Parameter Injection Defenses
# ==============================================================================

def test_tool_dispatcher_ignores_injected_user_id(db_session: Session):
    user_a = _create_test_user(db_session, "user_a@example.com")
    user_b = _create_test_user(db_session, "user_b@example.com")

    order_b = _create_test_order(db_session, user_b.id, "ORD-SECRET-B")

    # Adversary attempts prompt injection by supplying user_b's user_id in tool arguments
    malicious_args = {
        "order_number": order_b.order_number,
        "user_id": str(user_b.id),
        "customer_id": str(user_b.id),
    }

    # Dispatcher must ignore injected user_id and execute exclusively under user_a.id
    res_order = execute_order_support_tool("get_my_order", malicious_args, db_session, user_a.id)
    assert res_order["success"] is True
    assert res_order["found"] is False
    assert "not found" in res_order["message"].lower()

    res_status = execute_order_support_tool("get_order_status", malicious_args, db_session, user_a.id)
    assert res_status["success"] is True
    assert res_status["found"] is False

    res_items = execute_order_support_tool("get_order_items", malicious_args, db_session, user_a.id)
    assert res_items["success"] is True
    assert res_items["found"] is False

    # Dispatcher with list_my_orders must not return user_b's orders
    res_list = execute_order_support_tool("list_my_orders", malicious_args, db_session, user_a.id)
    assert res_list["success"] is True
    assert res_list["orders"] == []


def test_tool_dispatcher_unknown_tool(db_session: Session):
    user = _create_test_user(db_session, "user@example.com")
    res = execute_order_support_tool("unsupported_tool", {}, db_session, user.id)
    assert res["success"] is False
    assert res["error"] == "UNKNOWN_TOOL"


# ==============================================================================
# 7. Case Insensitivity & Whitespace Trimming
# ==============================================================================

def test_case_insensitivity_and_whitespace_trimming(db_session: Session):
    user = _create_test_user(db_session, "whitespace@example.com")
    order = _create_test_order(db_session, user.id, "ORD-20261121-MIXEDCASE")

    # Lowercase with leading and trailing whitespace
    query_num = "  ord-20261121-mixedcase  "

    res = get_my_order(db_session, user.id, query_num)
    assert res["success"] is True
    assert res["found"] is True
    assert res["order"]["order_number"] == "ORD-20261121-MIXEDCASE"

    status_res = get_order_status(db_session, user.id, query_num)
    assert status_res["success"] is True
    assert status_res["found"] is True
    assert status_res["order_number"] == "ORD-20261121-MIXEDCASE"

    items_res = get_order_items(db_session, user.id, query_num)
    assert items_res["success"] is True
    assert items_res["found"] is True
    assert items_res["order_number"] == "ORD-20261121-MIXEDCASE"


# ==============================================================================
# 8. Information Hiding & Internal Identifier Sanitization
# ==============================================================================

def test_internal_identifiers_are_excluded_from_tool_responses(db_session: Session):
    user = _create_test_user(db_session, "privacy@example.com")
    order = _create_test_order(db_session, user.id, "ORD-PRIVACY-001", items_count=2)

    # 1. get_my_order response inspection
    order_data = get_my_order(db_session, user.id, order.order_number)["order"]
    sensitive_keys = {"id", "user_id", "shipping_address_id", "idempotency_key", "request_fingerprint"}
    for key in sensitive_keys:
        assert key not in order_data, f"Key '{key}' leaked in get_my_order"

    for item in order_data["items"]:
        assert "id" not in item
        assert "product_id" not in item
        assert "order_id" not in item

    # 2. list_my_orders response inspection
    list_data = list_my_orders(db_session, user.id)["orders"]
    for summary in list_data:
        for key in sensitive_keys:
            assert key not in summary, f"Key '{key}' leaked in list_my_orders"

    # 3. get_order_status response inspection
    status_data = get_order_status(db_session, user.id, order.order_number)
    for key in sensitive_keys:
        assert key not in status_data, f"Key '{key}' leaked in get_order_status"

    # 4. get_order_items response inspection
    items_data = get_order_items(db_session, user.id, order.order_number)["items"]
    for item in items_data:
        assert "id" not in item
        assert "product_id" not in item
        assert "order_id" not in item


# ==============================================================================
# 9. Explicit Signature Enforcement in Service Functions
# ==============================================================================

def test_explicit_function_signatures_in_order_service(db_session: Session):
    user = _create_test_user(db_session, "explicit_args@example.com")
    order = _create_test_order(db_session, user.id, "ORD-EXP-001")

    # Explicit signature: (session, user_id, order_number)
    res = get_customer_order_by_number(db_session, user.id, "ORD-EXP-001")
    assert res is not None
    assert res.id == order.id

    stat = get_customer_order_status(db_session, user.id, "ORD-EXP-001")
    assert stat is not None
    assert stat.order_number == "ORD-EXP-001"

    items = get_customer_order_items(db_session, user.id, "ORD-EXP-001")
    assert items is not None
    assert len(items) == 1


# ==============================================================================
# 10. Class Wrapper (OrderSupportTools) Tests
# ==============================================================================

def test_order_support_tools_class_wrapper(db_session: Session):
    user = _create_test_user(db_session, "class_wrapper@example.com")
    order = _create_test_order(db_session, user.id, "ORD-CLASS-001")

    tools = OrderSupportTools(db_session, user.id)

    list_res = tools.list_my_orders()
    assert list_res["success"] is True
    assert list_res["total_returned"] == 1

    detail_res = tools.get_my_order(order.order_number)
    assert detail_res["success"] is True
    assert detail_res["found"] is True

    status_res = tools.get_order_status(order.order_number)
    assert status_res["success"] is True
    assert status_res["found"] is True

    items_res = tools.get_order_items(order.order_number)
    assert items_res["success"] is True
    assert items_res["found"] is True


# ==============================================================================
# 11. Gemini Tool Declaration Integrity
# ==============================================================================

def test_gemini_tool_declarations_integrity():
    assert len(ORDER_SUPPORT_TOOL_DECLARATIONS) == 4
    decl_names = {d.name for d in ORDER_SUPPORT_TOOL_DECLARATIONS}
    assert decl_names == {"list_my_orders", "get_my_order", "get_order_status", "get_order_items"}

    # Confirm none of the declarations expose user_id or customer_id
    for decl in ORDER_SUPPORT_TOOL_DECLARATIONS:
        if decl.parameters and decl.parameters.properties:
            props = decl.parameters.properties
            assert "user_id" not in props, f"Declaration {decl.name} exposed user_id parameter"
            assert "customer_id" not in props, f"Declaration {decl.name} exposed customer_id parameter"
            assert "current_user_id" not in props, f"Declaration {decl.name} exposed current_user_id parameter"


# ==============================================================================
# 12. PostgreSQL Isolation & Customer Scoping Integration
# ==============================================================================

def _get_postgres_test_url() -> str | None:
    url = os.getenv("POSTGRES_TEST_DATABASE_URL")
    if not url:
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment")
            val, _ = winreg.QueryValueEx(key, "POSTGRES_TEST_DATABASE_URL")
            winreg.CloseKey(key)
            if val:
                url = val
                os.environ["POSTGRES_TEST_DATABASE_URL"] = val
        except Exception:
            pass

    if not url or not url.startswith("postgresql"):
        return None

    from urllib.parse import urlparse
    parsed = urlparse(url)
    db_name = parsed.path.lstrip("/")
    if db_name != "ecommerce_agent_test":
        raise RuntimeError(
            f"CRITICAL SAFETY VIOLATION: Target database must be 'ecommerce_agent_test', got '{db_name}'"
        )
    return url


def test_order_support_postgres_integration():
    pg_url = _get_postgres_test_url()
    if not pg_url:
        pytest.skip("POSTGRES_TEST_DATABASE_URL not configured; skipping live PostgreSQL test")

    engine = create_engine(pg_url)
    try:
        with engine.connect() as conn:
            db_name = conn.execute(text("SELECT current_database()")).scalar()
            if db_name != "ecommerce_agent_test":
                pytest.skip("POSTGRES_TEST_DATABASE_URL does not target ecommerce_agent_test")

        session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
        with session_factory() as session:
            user_a = _create_test_user(session, f"pg_user_a_{uuid4().hex[:6]}@example.com")
            user_b = _create_test_user(session, f"pg_user_b_{uuid4().hex[:6]}@example.com")
            order_num_a = f"ORD-PG-{uuid4().hex[:8].upper()}"
            order_num_b = f"ORD-PG-{uuid4().hex[:8].upper()}"

            _create_test_order(session, user_a.id, order_num_a)
            _create_test_order(session, user_b.id, order_num_b)

            # PostgreSQL cross-customer isolation
            assert get_customer_order_by_number(session, user_a.id, order_num_b) is None
            assert get_customer_order_by_number(session, user_b.id, order_num_a) is None

            # User A listing orders on PostgreSQL never sees User B
            user_a_orders = list_customer_orders(session, user_a.id)
            assert any(o.order_number == order_num_a for o in user_a_orders)
            assert not any(o.order_number == order_num_b for o in user_a_orders)
    finally:
        engine.dispose()
