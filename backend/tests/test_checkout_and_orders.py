from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.orm import Session, sessionmaker

from backend.app.core.config import Settings
from backend.app.db.base import Base
from backend.app.db.models import (
    Address,
    Cart,
    CartItem,
    Inventory,
    Order,
    OrderItem,
    OrderStatus,
    Product,
    User,
)
from backend.app.main import create_app
from backend.app.schemas.order import (
    CheckoutItemAcknowledgement,
    CheckoutRequest,
)
from backend.app.security.passwords import hash_password
from backend.app.security.tokens import create_access_token
from backend.app.services.checkout_service import (
    compute_request_fingerprint,
    is_order_number_collision,
)
from backend.app.services.shipping_tax_policy import (
    ConfigurableFlatShippingPolicy,
    ConfigurablePercentageTaxPolicy,
    set_shipping_policy,
    set_tax_policy,
)


@pytest.fixture(autouse=True)
def configure_default_policies():
    """Configure predictable shipping and tax policies for test runs."""
    set_shipping_policy(ConfigurableFlatShippingPolicy(Decimal("50.00")))
    set_tax_policy(ConfigurablePercentageTaxPolicy(Decimal("18.00")))
    yield
    set_shipping_policy(None)
    set_tax_policy(None)


@pytest.fixture
def test_env(tmp_path, monkeypatch):
    db_file = (tmp_path / "test_checkout.sqlite").as_posix()
    db_url = f"sqlite+pysqlite:///{db_file}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("AUTH_SECRET_KEY", "super-secret-key-at-least-32-bytes-long-123456")
    monkeypatch.setenv("CATALOGUE_CURRENCY", "INR")

    engine = create_engine(db_url, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    app = create_app(engine_factory=lambda url: engine)
    with TestClient(app) as client:
        yield client, session_factory, engine

    engine.dispose()


def _create_user(session: Session, email: str = "customer@example.com") -> tuple[User, str]:
    user = User(
        email=email,
        password_hash=hash_password("Password123!"),
        is_active=True,
    )
    session.add(user)
    session.commit()
    session.refresh(user)

    settings = Settings(
        GEMINI_API_KEY="test-key",
        DATABASE_URL="sqlite+pysqlite:///:memory:",
        AUTH_SECRET_KEY="super-secret-key-at-least-32-bytes-long-123456",
    )
    token = create_access_token(user.id, settings)
    return user, token


def _create_address(session: Session, user_id: UUID) -> Address:
    addr = Address(
        user_id=user_id,
        recipient_name="Jane Doe",
        line1="100 Tech Park",
        line2="Suite 4B",
        city="Bengaluru",
        region="Karnataka",
        postal_code="560001",
        country_code="IN",
    )
    session.add(addr)
    session.commit()
    session.refresh(addr)
    return addr


def _create_product(
    session: Session,
    sku: str = "PROD-01",
    name: str = "Noise Cancelling Headphones",
    price: Decimal = Decimal("100.00"),
    stock: int = 10,
    is_active: bool = True,
) -> Product:
    prod = Product(
        sku=sku,
        name=name,
        price=price,
        is_active=is_active,
    )
    session.add(prod)
    session.flush()

    inv = Inventory(product_id=prod.id, available_quantity=stock)
    session.add(inv)
    session.commit()
    session.refresh(prod)
    return prod


def _add_item_to_cart(session: Session, user_id: UUID, product_id: UUID, quantity: int = 1) -> Cart:
    cart = session.scalar(select(Cart).where(Cart.user_id == user_id))
    if cart is None:
        cart = Cart(user_id=user_id)
        session.add(cart)
        session.flush()

    item = CartItem(cart_id=cart.id, product_id=product_id, quantity=quantity)
    session.add(item)
    session.commit()
    session.refresh(cart)
    return cart


# --- 1. Authentication and Header Requirements ---

def test_checkout_requires_authentication(test_env):
    client, _, _ = test_env
    res = client.post(
        "/api/v1/checkout",
        json={"shipping_address_id": str(uuid4()), "items": []},
        headers={"Idempotency-Key": "key-123"},
    )
    assert res.status_code == 401


def test_checkout_requires_idempotency_key_header(test_env):
    client, session_factory, _ = test_env
    with session_factory() as session:
        user, token = _create_user(session)

    res = client.post(
        "/api/v1/checkout",
        json={"shipping_address_id": str(uuid4()), "items": []},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 422


# --- 2. Validation Rejections: Empty Cart, Address, and Snapshots ---

def test_checkout_rejects_empty_cart(test_env):
    client, session_factory, _ = test_env
    with session_factory() as session:
        user, token = _create_user(session)
        addr = _create_address(session, user.id)

    res = client.post(
        "/api/v1/checkout",
        json={
            "shipping_address_id": str(addr.id),
            "items": [
                {"product_id": str(uuid4()), "quantity": 1, "acknowledged_unit_price": "100.00"}
            ],
        },
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "key-empty"},
    )
    assert res.status_code == 400
    assert "empty" in res.json()["detail"].lower()


def test_checkout_rejects_missing_or_unowned_shipping_address(test_env):
    client, session_factory, _ = test_env
    with session_factory() as session:
        user1, token1 = _create_user(session, "user1@example.com")
        user2, token2 = _create_user(session, "user2@example.com")
        addr_user2 = _create_address(session, user2.id)
        prod = _create_product(session)
        _add_item_to_cart(session, user1.id, prod.id, 1)

    # User 1 tries to use User 2's address -> 404
    res = client.post(
        "/api/v1/checkout",
        json={
            "shipping_address_id": str(addr_user2.id),
            "items": [
                {"product_id": str(prod.id), "quantity": 1, "acknowledged_unit_price": "100.00"}
            ],
        },
        headers={"Authorization": f"Bearer {token1}", "Idempotency-Key": "key-addr"},
    )
    assert res.status_code == 404
    assert "shipping address not found" in res.json()["detail"].lower()


def test_checkout_rejects_cart_snapshot_mismatches(test_env):
    client, session_factory, _ = test_env
    with session_factory() as session:
        user, token = _create_user(session)
        addr = _create_address(session, user.id)
        prod1 = _create_product(session, sku="P1")
        prod2 = _create_product(session, sku="P2")
        _add_item_to_cart(session, user.id, prod1.id, 2)

    # Mismatch 1: Quantity differs (snapshot says 1, cart has 2)
    res = client.post(
        "/api/v1/checkout",
        json={
            "shipping_address_id": str(addr.id),
            "items": [
                {"product_id": str(prod1.id), "quantity": 1, "acknowledged_unit_price": "100.00"}
            ],
        },
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "key-snap-1"},
    )
    assert res.status_code == 409
    assert "cart snapshot does not match" in res.json()["detail"].lower()

    # Mismatch 2: Extra product in snapshot
    res = client.post(
        "/api/v1/checkout",
        json={
            "shipping_address_id": str(addr.id),
            "items": [
                {"product_id": str(prod1.id), "quantity": 2, "acknowledged_unit_price": "100.00"},
                {"product_id": str(prod2.id), "quantity": 1, "acknowledged_unit_price": "100.00"},
            ],
        },
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "key-snap-2"},
    )
    assert res.status_code == 409


def test_checkout_rejects_duplicate_product_ids_in_request(test_env):
    client, session_factory, _ = test_env
    with session_factory() as session:
        user, token = _create_user(session)
        addr = _create_address(session, user.id)
        prod = _create_product(session)

    res = client.post(
        "/api/v1/checkout",
        json={
            "shipping_address_id": str(addr.id),
            "items": [
                {"product_id": str(prod.id), "quantity": 1, "acknowledged_unit_price": "100.00"},
                {"product_id": str(prod.id), "quantity": 2, "acknowledged_unit_price": "100.00"},
            ],
        },
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "key-dup"},
    )
    assert res.status_code == 422


# --- 3. Inventory and Product Active Status Validations ---

def test_checkout_rejects_inactive_or_out_of_stock_product(test_env):
    client, session_factory, _ = test_env
    with session_factory() as session:
        user, token = _create_user(session)
        addr = _create_address(session, user.id)
        prod_inactive = _create_product(session, sku="INACTIVE", is_active=False)
        _add_item_to_cart(session, user.id, prod_inactive.id, 1)

    res = client.post(
        "/api/v1/checkout",
        json={
            "shipping_address_id": str(addr.id),
            "items": [
                {"product_id": str(prod_inactive.id), "quantity": 1, "acknowledged_unit_price": "100.00"}
            ],
        },
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "key-inact"},
    )
    assert res.status_code == 400
    assert "no longer available" in res.json()["detail"].lower()


def test_checkout_rejects_insufficient_stock(test_env):
    client, session_factory, _ = test_env
    with session_factory() as session:
        user, token = _create_user(session)
        addr = _create_address(session, user.id)
        prod = _create_product(session, sku="LOW-STOCK", stock=1)
        _add_item_to_cart(session, user.id, prod.id, 5)

    res = client.post(
        "/api/v1/checkout",
        json={
            "shipping_address_id": str(addr.id),
            "items": [
                {"product_id": str(prod.id), "quantity": 5, "acknowledged_unit_price": "100.00"}
            ],
        },
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "key-stock"},
    )
    assert res.status_code == 400
    assert "insufficient stock" in res.json()["detail"].lower()


# --- 4. Price-Change Warning and Explicit Acknowledgement ---

def test_checkout_price_change_warning_and_acknowledgement(test_env):
    client, session_factory, _ = test_env
    with session_factory() as session:
        user, token = _create_user(session)
        addr = _create_address(session, user.id)
        prod = _create_product(session, sku="PRICE-CHG", price=Decimal("120.00"))
        _add_item_to_cart(session, user.id, prod.id, 1)

    # 1. Attempt checkout with old acknowledged price (100.00) -> 409 PRICE_CHANGED
    res = client.post(
        "/api/v1/checkout",
        json={
            "shipping_address_id": str(addr.id),
            "items": [
                {"product_id": str(prod.id), "quantity": 1, "acknowledged_unit_price": "100.00"}
            ],
        },
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "key-price-1"},
    )
    assert res.status_code == 409
    body = res.json()["detail"]
    assert body["code"] == "PRICE_CHANGED"
    diff = body["price_changes"][0]
    assert diff["acknowledged_price"] == "100.00"
    assert diff["current_price"] == "120.00"

    # 2. Resubmit with acknowledged_unit_price = 120.00 -> Succeeds!
    res_ack = client.post(
        "/api/v1/checkout",
        json={
            "shipping_address_id": str(addr.id),
            "items": [
                {"product_id": str(prod.id), "quantity": 1, "acknowledged_unit_price": "120.00"}
            ],
        },
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "key-price-2"},
    )
    assert res_ack.status_code == 201
    assert res_ack.json()["subtotal"] == "120.00"


def test_checkout_price_changes_again_after_acknowledgement(test_env):
    client, session_factory, _ = test_env
    with session_factory() as session:
        user, token = _create_user(session)
        addr = _create_address(session, user.id)
        prod = _create_product(session, sku="PRICE-VOLATILE", price=Decimal("150.00"))
        _add_item_to_cart(session, user.id, prod.id, 1)

    # Client acknowledges 120.00, but product price is now 150.00 -> 409
    res = client.post(
        "/api/v1/checkout",
        json={
            "shipping_address_id": str(addr.id),
            "items": [
                {"product_id": str(prod.id), "quantity": 1, "acknowledged_unit_price": "120.00"}
            ],
        },
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "key-volatile"},
    )
    assert res.status_code == 409
    body = res.json()["detail"]
    assert body["code"] == "PRICE_CHANGED"
    assert body["price_changes"][0]["current_price"] == "150.00"


# --- 5. Full Successful Checkout & Atomic Mutations ---

def test_checkout_successful_order_placement(test_env):
    client, session_factory, _ = test_env
    with session_factory() as session:
        user, token = _create_user(session)
        addr = _create_address(session, user.id)
        prod1 = _create_product(session, sku="EARBUD", name="Earbuds", price=Decimal("100.00"), stock=5)
        prod2 = _create_product(session, sku="CASE", name="Protective Case", price=Decimal("25.00"), stock=10)
        _add_item_to_cart(session, user.id, prod1.id, 2)
        _add_item_to_cart(session, user.id, prod2.id, 1)

    # subtotal = 200 + 25 = 225.00
    # shipping = 50.00
    # tax = (225 + 50) * 18% = 275 * 0.18 = 49.50
    # total = 225 + 50 + 49.50 = 324.50
    res = client.post(
        "/api/v1/checkout",
        json={
            "shipping_address_id": str(addr.id),
            "items": [
                {"product_id": str(prod1.id), "quantity": 2, "acknowledged_unit_price": "100.00"},
                {"product_id": str(prod2.id), "quantity": 1, "acknowledged_unit_price": "25.00"},
            ],
        },
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "key-success-1"},
    )
    assert res.status_code == 201
    data = res.json()
    assert data["status"] == "confirmed"
    assert data["subtotal"] == "225.00"
    assert data["shipping_fee"] == "50.00"
    assert data["tax"] == "49.50"
    assert data["total_amount"] == "324.50"
    assert data["shipping_snapshot"]["recipient_name"] == "Jane Doe"
    assert data["shipping_snapshot"]["city"] == "Bengaluru"
    assert len(data["items"]) == 2

    # Verify database state
    with session_factory() as session:
        # Stock deducted
        inv1 = session.scalar(select(Inventory).where(Inventory.product_id == prod1.id))
        inv2 = session.scalar(select(Inventory).where(Inventory.product_id == prod2.id))
        assert inv1.available_quantity == 3  # 5 - 2
        assert inv2.available_quantity == 9  # 10 - 1

        # Cart cleared
        cart = session.scalar(select(Cart).where(Cart.user_id == user.id))
        assert len(cart.items) == 0


# --- 6. Missing or Invalid Store Policy Configuration ---

def test_checkout_missing_policy_returns_503(test_env):
    client, session_factory, _ = test_env
    set_shipping_policy(None)  # unconfigure shipping policy

    with session_factory() as session:
        user, token = _create_user(session)
        addr = _create_address(session, user.id)
        prod = _create_product(session)
        _add_item_to_cart(session, user.id, prod.id, 1)

    res = client.post(
        "/api/v1/checkout",
        json={
            "shipping_address_id": str(addr.id),
            "items": [
                {"product_id": str(prod.id), "quantity": 1, "acknowledged_unit_price": "100.00"}
            ],
        },
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "key-unconfig"},
    )
    assert res.status_code == 503
    assert "temporarily unavailable" in res.json()["detail"].lower()


# --- 7. Idempotency Fingerprint & Replay Behavior ---

def test_fingerprint_quantity_sensitivity():
    addr_id = uuid4()
    prod_id = uuid4()

    fp1 = compute_request_fingerprint(
        addr_id,
        [CheckoutItemAcknowledgement(product_id=prod_id, quantity=1, acknowledged_unit_price=Decimal("10.00"))],
    )
    fp2 = compute_request_fingerprint(
        addr_id,
        [CheckoutItemAcknowledgement(product_id=prod_id, quantity=2, acknowledged_unit_price=Decimal("10.00"))],
    )
    assert fp1 != fp2
    assert len(fp1) == 64
    assert len(fp2) == 64


def test_checkout_idempotency_replay_and_conflict(test_env):
    client, session_factory, _ = test_env
    with session_factory() as session:
        user, token = _create_user(session)
        addr = _create_address(session, user.id)
        prod = _create_product(session, price=Decimal("50.00"), stock=10)
        _add_item_to_cart(session, user.id, prod.id, 1)

    payload = {
        "shipping_address_id": str(addr.id),
        "items": [
            {"product_id": str(prod.id), "quantity": 1, "acknowledged_unit_price": "50.00"}
        ],
    }

    # 1. First checkout
    res1 = client.post(
        "/api/v1/checkout",
        json=payload,
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "idemp-key-01"},
    )
    assert res1.status_code == 201
    order_id = res1.json()["id"]

    # 2. Replay with identical payload and same key -> returns same order (200 OK)
    res2 = client.post(
        "/api/v1/checkout",
        json=payload,
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "idemp-key-01"},
    )
    assert res2.status_code == 200
    assert res2.headers["Idempotent-Replay"] == "true"
    assert res2.json()["id"] == order_id

    # 3. Same key with different address -> 409 Conflict
    diff_payload = {
        "shipping_address_id": str(uuid4()),
        "items": [
            {"product_id": str(prod.id), "quantity": 1, "acknowledged_unit_price": "50.00"}
        ],
    }
    res3 = client.post(
        "/api/v1/checkout",
        json=diff_payload,
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "idemp-key-01"},
    )
    assert res3.status_code == 409


# --- 8. Order Number Collision Retry Discrimination ---

def test_is_order_number_collision_discrimination():
    from sqlalchemy.exc import IntegrityError

    exc_collision = IntegrityError("statement", "params", Exception("UNIQUE constraint failed: orders.order_number (ix_orders_order_number)"))
    assert is_order_number_collision(exc_collision) is True

    exc_idemp = IntegrityError("statement", "params", Exception("UNIQUE constraint failed: uq_orders_user_idempotency"))
    assert is_order_number_collision(exc_idemp) is False


# --- 9. Customer Order History and Ownership APIs ---

def test_customer_order_history_and_details_ownership(test_env):
    client, session_factory, _ = test_env
    with session_factory() as session:
        user1, token1 = _create_user(session, "user1@domain.com")
        user2, token2 = _create_user(session, "user2@domain.com")
        addr = _create_address(session, user1.id)
        prod = _create_product(session)
        _add_item_to_cart(session, user1.id, prod.id, 1)

    # Place order as user 1
    res = client.post(
        "/api/v1/checkout",
        json={
            "shipping_address_id": str(addr.id),
            "items": [
                {"product_id": str(prod.id), "quantity": 1, "acknowledged_unit_price": "100.00"}
            ],
        },
        headers={"Authorization": f"Bearer {token1}", "Idempotency-Key": "key-own-01"},
    )
    assert res.status_code == 201
    order_id = res.json()["id"]

    # 1. User 1 can list orders
    list_res = client.get("/api/v1/orders", headers={"Authorization": f"Bearer {token1}"})
    assert list_res.status_code == 200
    assert len(list_res.json()) == 1
    assert list_res.json()[0]["id"] == order_id

    # 2. User 1 can view order details
    detail_res = client.get(f"/api/v1/orders/{order_id}", headers={"Authorization": f"Bearer {token1}"})
    assert detail_res.status_code == 200
    assert detail_res.json()["id"] == order_id

    # 3. User 1 can view order items
    items_res = client.get(f"/api/v1/orders/{order_id}/items", headers={"Authorization": f"Bearer {token1}"})
    assert items_res.status_code == 200
    assert len(items_res.json()) == 1

    # 4. User 2 cannot see User 1's orders in list
    u2_list = client.get("/api/v1/orders", headers={"Authorization": f"Bearer {token2}"})
    assert u2_list.status_code == 200
    assert len(u2_list.json()) == 0

    # 5. User 2 cannot access User 1's order details -> 404
    u2_detail = client.get(f"/api/v1/orders/{order_id}", headers={"Authorization": f"Bearer {token2}"})
    assert u2_detail.status_code == 404

    # 6. User 2 cannot access User 1's order items -> 404
    u2_items = client.get(f"/api/v1/orders/{order_id}/items", headers={"Authorization": f"Bearer {token2}"})
    assert u2_items.status_code == 404


# --- 10. Address Deletion Preserves Immutable Snapshot ---

def test_address_deletion_preserves_order_snapshot(test_env):
    client, session_factory, _ = test_env
    with session_factory() as session:
        user, token = _create_user(session)
        addr = _create_address(session, user.id)
        prod = _create_product(session)
        _add_item_to_cart(session, user.id, prod.id, 1)

    res = client.post(
        "/api/v1/checkout",
        json={
            "shipping_address_id": str(addr.id),
            "items": [
                {"product_id": str(prod.id), "quantity": 1, "acknowledged_unit_price": "100.00"}
            ],
        },
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "key-addr-del"},
    )
    assert res.status_code == 201
    order_id = res.json()["id"]

    # Delete the address from address book
    del_res = client.delete(f"/api/v1/customers/me/addresses/{addr.id}", headers={"Authorization": f"Bearer {token}"})
    assert del_res.status_code == 204

    # Order details still retain snapshot and shipping_address_id becomes null
    order_res = client.get(f"/api/v1/orders/{order_id}", headers={"Authorization": f"Bearer {token}"})
    assert order_res.status_code == 200
    data = order_res.json()
    assert data["shipping_address_id"] is None
    assert data["shipping_snapshot"]["recipient_name"] == "Jane Doe"
    assert data["shipping_snapshot"]["line1"] == "100 Tech Park"
    assert data["shipping_snapshot"]["city"] == "Bengaluru"


# --- 11. Policy Amount Validation (Negative, NaN, Non-Decimal) ---

class MockInvalidShippingPolicy:
    def __init__(self, return_val: object):
        self.return_val = return_val

    def calculate_shipping_fee(self, subtotal, items, shipping_address, settings):
        return self.return_val


def test_checkout_policy_returning_negative_or_nan_returns_503(test_env):
    client, session_factory, _ = test_env
    with session_factory() as session:
        user, token = _create_user(session, "policy_val@domain.com")
        addr = _create_address(session, user.id)
        prod = _create_product(session)
        _add_item_to_cart(session, user.id, prod.id, 1)

    payload = {
        "shipping_address_id": str(addr.id),
        "items": [
            {"product_id": str(prod.id), "quantity": 1, "acknowledged_unit_price": "100.00"}
        ],
    }

    # 1. Negative shipping fee -> 503
    set_shipping_policy(MockInvalidShippingPolicy(Decimal("-10.00")))
    res_neg = client.post(
        "/api/v1/checkout",
        json=payload,
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "key-neg-policy"},
    )
    assert res_neg.status_code == 503
    assert "temporarily unavailable" in res_neg.json()["detail"].lower()

    # 2. NaN shipping fee -> 503
    set_shipping_policy(MockInvalidShippingPolicy(Decimal("NaN")))
    res_nan = client.post(
        "/api/v1/checkout",
        json=payload,
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "key-nan-policy"},
    )
    assert res_nan.status_code == 503
    assert "temporarily unavailable" in res_nan.json()["detail"].lower()

    # 3. Non-decimal (e.g. float or str) -> 503
    set_shipping_policy(MockInvalidShippingPolicy(50.0))  # type: ignore[arg-type]
    res_non_dec = client.post(
        "/api/v1/checkout",
        json=payload,
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "key-float-policy"},
    )
    assert res_non_dec.status_code == 503
    assert "temporarily unavailable" in res_non_dec.json()["detail"].lower()


# --- 12. Actual Database Order-Number Collision Retry & Session Usability ---

def test_actual_database_order_number_collision_retry(test_env, monkeypatch):
    client, session_factory, _ = test_env
    with session_factory() as session:
        user, token = _create_user(session, "collision_user@domain.com")
        addr = _create_address(session, user.id)
        prod = _create_product(session)
        _add_item_to_cart(session, user.id, prod.id, 1)

        # Pre-seed an existing order with a specific order_number
        existing_order = Order(
            order_number="ORD-COLLISION-TARGET",
            user_id=user.id,
            shipping_recipient_name=addr.recipient_name,
            shipping_line1=addr.line1,
            shipping_city=addr.city,
            shipping_region=addr.region,
            shipping_postal_code=addr.postal_code,
            shipping_country_code=addr.country_code,
            status=OrderStatus.CONFIRMED.value,
            currency="INR",
            subtotal=Decimal("100.00"),
            shipping_fee=Decimal("50.00"),
            tax=Decimal("18.00"),
            total_amount=Decimal("168.00"),
        )
        session.add(existing_order)
        session.commit()

    # Sequence of order numbers: first collides with existing order, second is unique
    generated_numbers = iter(["ORD-COLLISION-TARGET", "ORD-UNIQUE-SUCCESS"])
    monkeypatch.setattr(
        "backend.app.services.checkout_service.generate_order_number",
        lambda: next(generated_numbers),
    )

    res = client.post(
        "/api/v1/checkout",
        json={
            "shipping_address_id": str(addr.id),
            "items": [
                {"product_id": str(prod.id), "quantity": 1, "acknowledged_unit_price": "100.00"}
            ],
        },
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "key-collision-retry"},
    )
    assert res.status_code == 201
    data = res.json()
    assert data["order_number"] == "ORD-UNIQUE-SUCCESS"

    # Verify session and database state
    with session_factory() as session:
        orders = session.scalars(select(Order).order_by(Order.created_at)).all()
        order_numbers = [o.order_number for o in orders]
        assert "ORD-COLLISION-TARGET" in order_numbers
        assert "ORD-UNIQUE-SUCCESS" in order_numbers


def test_order_number_collision_retry_exhaustion_raises(test_env, monkeypatch):
    client, session_factory, _ = test_env
    with session_factory() as session:
        user, token = _create_user(session, "exhaust_user@domain.com")
        addr = _create_address(session, user.id)
        prod = _create_product(session)
        _add_item_to_cart(session, user.id, prod.id, 1)

        # Pre-seed an existing order with a fixed order number
        existing_order = Order(
            order_number="ORD-ALWAYS-COLLIDE",
            user_id=user.id,
            shipping_recipient_name=addr.recipient_name,
            shipping_line1=addr.line1,
            shipping_city=addr.city,
            shipping_region=addr.region,
            shipping_postal_code=addr.postal_code,
            shipping_country_code=addr.country_code,
            status=OrderStatus.CONFIRMED.value,
            currency="INR",
            subtotal=Decimal("100.00"),
            shipping_fee=Decimal("50.00"),
            tax=Decimal("18.00"),
            total_amount=Decimal("168.00"),
        )
        session.add(existing_order)
        session.commit()

    # Always return the colliding order number
    monkeypatch.setattr(
        "backend.app.services.checkout_service.generate_order_number",
        lambda: "ORD-ALWAYS-COLLIDE",
    )

    with pytest.raises(RuntimeError) as exc_info:
        client.post(
            "/api/v1/checkout",
            json={
                "shipping_address_id": str(addr.id),
                "items": [
                    {"product_id": str(prod.id), "quantity": 1, "acknowledged_unit_price": "100.00"}
                ],
            },
            headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "key-exhaust"},
        )
    assert "Failed to allocate a unique order number" in str(exc_info.value)


# --- 13. Cart Lock & Recovery Discrimination ---

def test_is_cart_user_collision_discrimination():
    from backend.app.services.cart_service import is_cart_user_collision
    from sqlalchemy.exc import IntegrityError

    exc_cart_user = IntegrityError(
        "statement", "params", Exception("UNIQUE constraint failed: carts.user_id (uq_carts_user_id)")
    )
    assert is_cart_user_collision(exc_cart_user) is True

    exc_other = IntegrityError(
        "statement", "params", Exception("FOREIGN KEY constraint failed")
    )
    assert is_cart_user_collision(exc_other) is False


def test_get_or_create_cart_for_update_propagates_unrelated_integrity_error(test_env):
    from backend.app.services.cart_service import get_or_create_cart_for_update
    from sqlalchemy.exc import IntegrityError

    _, session_factory, _ = test_env
    with session_factory() as session:
        # Non-existent user_id violates foreign key constraint on carts.user_id -> users.id
        invalid_user_id = uuid4()
        with pytest.raises(IntegrityError) as exc_info:
            get_or_create_cart_for_update(session, invalid_user_id)
        # Foreign key violation was NOT swallowed or recovered as a cart collision
        assert "foreign key" in str(exc_info.value).lower()

