from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import hashlib
import os
from pathlib import Path
import threading
from urllib.parse import urlparse
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi import HTTPException
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.exc import IntegrityError
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
from backend.app.schemas.order import (
    CheckoutItemAcknowledgement,
    CheckoutRequest,
)
from backend.app.security.passwords import hash_password
from backend.app.services.cart_service import add_to_cart, get_or_create_cart_for_update
from backend.app.services.checkout_service import process_checkout
from backend.app.services.shipping_tax_policy import (
    ConfigurableFlatShippingPolicy,
    ConfigurablePercentageTaxPolicy,
)


ROOT = Path(__file__).resolve().parents[2]


def _get_postgres_test_url() -> str | None:
    """Retrieve and strictly validate the isolated PostgreSQL test database URL."""
    url = os.getenv("POSTGRES_TEST_DATABASE_URL")
    if not url:
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment")
            val, _ = winreg.QueryValueEx(key, "POSTGRES_TEST_DATABASE_URL")
            url = val
            os.environ["POSTGRES_TEST_DATABASE_URL"] = val
        except Exception:
            pass

    if not url:
        url = os.getenv("TEST_DATABASE_URL") or os.getenv("POSTGRES_TEST_URL")

    if not url or not url.startswith("postgresql"):
        return None

    # CRITICAL SAFETY ENFORCEMENT
    parsed = urlparse(url)
    db_name = parsed.path.lstrip("/")
    if db_name != "ecommerce_agent_test":
        raise RuntimeError(
            f"CRITICAL SAFETY VIOLATION: Target database must be 'ecommerce_agent_test', got '{db_name}'"
        )
    if "dev" in db_name.lower():
        raise RuntimeError(
            f"CRITICAL SAFETY VIOLATION: Target database cannot be development database: '{db_name}'"
        )
    if parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise RuntimeError(
            f"CRITICAL SAFETY VIOLATION: Target host must be localhost, got '{parsed.hostname}'"
        )

    return url


def _is_postgres_reachable() -> bool:
    url = _get_postgres_test_url()
    if not url:
        return False
    try:
        engine = create_engine(url, connect_args={"connect_timeout": 3})
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
        return True
    except Exception:
        return False


POSTGRES_AVAILABLE = _is_postgres_reachable()
pytestmark = pytest.mark.skipif(
    not POSTGRES_AVAILABLE,
    reason="Live PostgreSQL test database 'ecommerce_agent_test' not reachable. Set POSTGRES_TEST_DATABASE_URL to enable.",
)


@pytest.fixture(scope="module")
def pg_engine():
    url = _get_postgres_test_url()
    assert url is not None
    engine = create_engine(url, pool_size=15, max_overflow=5)
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def pg_session_factory(pg_engine):
    return sessionmaker(bind=pg_engine, autoflush=False, expire_on_commit=False)


@pytest.fixture
def test_settings():
    return Settings(
        GEMINI_API_KEY="test-key",
        DATABASE_URL="sqlite+pysqlite:///:memory:",
        AUTH_SECRET_KEY="super-secret-key-at-least-32-bytes-long-123456",
        CATALOGUE_CURRENCY="INR",
    )


def _setup_user_and_address(session: Session, prefix: str = "pg") -> tuple[User, Address]:
    uid = uuid4().hex[:8]
    user = User(
        email=f"{prefix}_user_{uid}@example.com",
        password_hash=hash_password("Pass123!"),
        is_active=True,
    )
    session.add(user)
    session.flush()

    addr = Address(
        user_id=user.id,
        recipient_name="PG Test User",
        line1="123 Postgres Way",
        city="Bengaluru",
        region="Karnataka",
        postal_code="560001",
        country_code="IN",
    )
    session.add(addr)
    session.commit()
    return user, addr


# ==============================================================================
# 1. Concurrent Last-Inventory-Unit Checkout
# ==============================================================================
def test_postgres_concurrent_last_unit_inventory_checkout(pg_session_factory, test_settings):
    """Verify row-level locking (SELECT FOR UPDATE) prevents overselling when 2 customers race for 1 unit."""
    shipping_policy = ConfigurableFlatShippingPolicy(Decimal("0.00"))
    tax_policy = ConfigurablePercentageTaxPolicy(Decimal("0.00"))

    with pg_session_factory() as setup_session:
        user1, addr1 = _setup_user_and_address(setup_session, "u1")
        user2, addr2 = _setup_user_and_address(setup_session, "u2")

        prod = Product(
            sku=f"PG-CONC-1-{uuid4().hex[:6]}",
            name="Contested Single Unit Item",
            price=Decimal("100.00"),
            is_active=True,
        )
        setup_session.add(prod)
        setup_session.flush()

        inv = Inventory(product_id=prod.id, available_quantity=1)
        setup_session.add(inv)
        setup_session.commit()

        cart1 = get_or_create_cart_for_update(setup_session, user1.id)
        setup_session.add(CartItem(cart_id=cart1.id, product_id=prod.id, quantity=1))
        cart2 = get_or_create_cart_for_update(setup_session, user2.id)
        setup_session.add(CartItem(cart_id=cart2.id, product_id=prod.id, quantity=1))
        setup_session.commit()

        prod_id = prod.id
        addr1_id = addr1.id
        addr2_id = addr2.id
        user1_id = user1.id
        user2_id = user2.id

    req1 = CheckoutRequest(
        shipping_address_id=addr1_id,
        items=[CheckoutItemAcknowledgement(product_id=prod_id, quantity=1, acknowledged_unit_price=Decimal("100.00"))],
    )
    req2 = CheckoutRequest(
        shipping_address_id=addr2_id,
        items=[CheckoutItemAcknowledgement(product_id=prod_id, quantity=1, acknowledged_unit_price=Decimal("100.00"))],
    )

    barrier = threading.Barrier(2)

    def run_checkout(u_id, req, key):
        with pg_session_factory() as session:
            try:
                barrier.wait(timeout=5)
                order, is_replay = process_checkout(
                    session=session,
                    user_id=u_id,
                    request=req,
                    idempotency_key=key,
                    settings=test_settings,
                    shipping_policy=shipping_policy,
                    tax_policy=tax_policy,
                )
                session.commit()
                return ("SUCCESS", order.id)
            except Exception as exc:
                session.rollback()
                return ("FAILED", str(exc))

    with ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(run_checkout, user1_id, req1, f"pg-key-u1-{uuid4().hex[:6]}")
        f2 = executor.submit(run_checkout, user2_id, req2, f"pg-key-u2-{uuid4().hex[:6]}")
        results = [f1.result(), f2.result()]

    statuses = [r[0] for r in results]
    assert "SUCCESS" in statuses
    assert "FAILED" in statuses

    # Confirm exactly 0 units left in inventory (NEVER negative)
    with pg_session_factory() as verify_session:
        final_inv = verify_session.scalar(select(Inventory).where(Inventory.product_id == prod_id))
        assert final_inv.available_quantity == 0

        # Exactly 1 order created in database
        orders = verify_session.scalars(
            select(Order).where(Order.user_id.in_([user1_id, user2_id]))
        ).all()
        assert len(orders) == 1


# ==============================================================================
# 2. Simultaneous Identical Idempotent Checkout Requests
# ==============================================================================
def test_postgres_concurrent_identical_idempotent_checkout(pg_session_factory, test_settings):
    """Verify simultaneous checkout requests with the same idempotency key result in exactly 1 order."""
    shipping_policy = ConfigurableFlatShippingPolicy(Decimal("10.00"))
    tax_policy = ConfigurablePercentageTaxPolicy(Decimal("5.00"))

    with pg_session_factory() as setup_session:
        user, addr = _setup_user_and_address(setup_session, "idemp")
        prod = Product(
            sku=f"PG-IDEMP-{uuid4().hex[:6]}",
            name="Idempotency Item",
            price=Decimal("50.00"),
            is_active=True,
        )
        setup_session.add(prod)
        setup_session.flush()

        inv = Inventory(product_id=prod.id, available_quantity=10)
        setup_session.add(inv)
        setup_session.commit()

        cart = get_or_create_cart_for_update(setup_session, user.id)
        setup_session.add(CartItem(cart_id=cart.id, product_id=prod.id, quantity=1))
        setup_session.commit()

        user_id = user.id
        addr_id = addr.id
        prod_id = prod.id

    shared_key = f"pg-shared-key-{uuid4().hex}"
    req = CheckoutRequest(
        shipping_address_id=addr_id,
        items=[CheckoutItemAcknowledgement(product_id=prod_id, quantity=1, acknowledged_unit_price=Decimal("50.00"))],
    )

    barrier = threading.Barrier(2)

    def run_checkout():
        with pg_session_factory() as session:
            try:
                barrier.wait(timeout=5)
                order, is_replay = process_checkout(
                    session=session,
                    user_id=user_id,
                    request=req,
                    idempotency_key=shared_key,
                    settings=test_settings,
                    shipping_policy=shipping_policy,
                    tax_policy=tax_policy,
                )
                session.commit()
                return ("SUCCESS", order.id, is_replay)
            except Exception as exc:
                session.rollback()
                return ("FAILED", str(exc), False)

    with ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(run_checkout)
        f2 = executor.submit(run_checkout)
        r1 = f1.result()
        r2 = f2.result()

    assert r1[0] == "SUCCESS"
    assert r2[0] == "SUCCESS"
    # Both must reference the exact same order
    assert r1[1] == r2[1]

    # Verify inventory was deducted only once (10 - 1 = 9)
    with pg_session_factory() as verify_session:
        final_inv = verify_session.scalar(select(Inventory).where(Inventory.product_id == prod_id))
        assert final_inv.available_quantity == 9
        # Exactly 1 order exists
        orders = verify_session.scalars(select(Order).where(Order.user_id == user_id)).all()
        assert len(orders) == 1


# ==============================================================================
# 3. Same Idempotency Key with Different Request Fingerprints
# ==============================================================================
def test_postgres_same_idempotency_key_different_request_conflict(pg_session_factory, test_settings):
    """Verify same idempotency key with conflicting parameters returns 409 Conflict."""
    shipping_policy = ConfigurableFlatShippingPolicy(Decimal("0.00"))
    tax_policy = ConfigurablePercentageTaxPolicy(Decimal("0.00"))

    with pg_session_factory() as setup_session:
        user, addr1 = _setup_user_and_address(setup_session, "conflict")
        addr2 = Address(
            user_id=user.id,
            recipient_name="Different Recipient",
            line1="456 Other St",
            city="Bengaluru",
            region="Karnataka",
            postal_code="560002",
            country_code="IN",
        )
        setup_session.add(addr2)

        prod = Product(
            sku=f"PG-CONF-{uuid4().hex[:6]}",
            name="Conflict Item",
            price=Decimal("40.00"),
            is_active=True,
        )
        setup_session.add(prod)
        setup_session.flush()

        inv = Inventory(product_id=prod.id, available_quantity=5)
        setup_session.add(inv)
        setup_session.commit()

        cart = get_or_create_cart_for_update(setup_session, user.id)
        setup_session.add(CartItem(cart_id=cart.id, product_id=prod.id, quantity=1))
        setup_session.commit()

        user_id = user.id
        prod_id = prod.id
        addr1_id = addr1.id
        addr2_id = addr2.id

    shared_key = f"pg-conflict-key-{uuid4().hex}"
    req1 = CheckoutRequest(
        shipping_address_id=addr1_id,
        items=[CheckoutItemAcknowledgement(product_id=prod_id, quantity=1, acknowledged_unit_price=Decimal("40.00"))],
    )
    req2 = CheckoutRequest(
        shipping_address_id=addr2_id,
        items=[CheckoutItemAcknowledgement(product_id=prod_id, quantity=1, acknowledged_unit_price=Decimal("40.00"))],
    )

    # 1. First checkout succeeds
    with pg_session_factory() as session1:
        ord1, is_replay1 = process_checkout(
            session=session1,
            user_id=user_id,
            request=req1,
            idempotency_key=shared_key,
            settings=test_settings,
            shipping_policy=shipping_policy,
            tax_policy=tax_policy,
        )
        session1.commit()
        assert ord1 is not None

    # 2. Conflicting checkout with same key but different address -> 409 Conflict
    with pg_session_factory() as session2:
        with pytest.raises(HTTPException) as exc_info:
            process_checkout(
                session=session2,
                user_id=user_id,
                request=req2,
                idempotency_key=shared_key,
                settings=test_settings,
                shipping_policy=shipping_policy,
                tax_policy=tax_policy,
            )
        assert exc_info.value.status_code == 409
        detail_msg = str(exc_info.value.detail).lower()
        assert "different request" in detail_msg or "reused" in detail_msg or "conflict" in detail_msg


# ==============================================================================
# 4. Cart Mutation Racing with Checkout
# ==============================================================================
def test_postgres_cart_mutation_racing_with_checkout(pg_session_factory, test_settings):
    """Verify parent Cart row locking prevents concurrent item additions from being lost or corrupting checkout."""
    shipping_policy = ConfigurableFlatShippingPolicy(Decimal("0.00"))
    tax_policy = ConfigurablePercentageTaxPolicy(Decimal("0.00"))

    with pg_session_factory() as setup_session:
        user, addr = _setup_user_and_address(setup_session, "race_cart")
        p1 = Product(sku=f"P1-{uuid4().hex[:6]}", name="Item 1", price=Decimal("10.00"), is_active=True)
        p2 = Product(sku=f"P2-{uuid4().hex[:6]}", name="Item 2", price=Decimal("20.00"), is_active=True)
        setup_session.add_all([p1, p2])
        setup_session.flush()

        setup_session.add(Inventory(product_id=p1.id, available_quantity=10))
        setup_session.add(Inventory(product_id=p2.id, available_quantity=10))
        setup_session.commit()

        cart = get_or_create_cart_for_update(setup_session, user.id)
        setup_session.add(CartItem(cart_id=cart.id, product_id=p1.id, quantity=1))
        setup_session.commit()

        user_id = user.id
        addr_id = addr.id
        p1_id = p1.id
        p2_id = p2.id

    req = CheckoutRequest(
        shipping_address_id=addr_id,
        items=[CheckoutItemAcknowledgement(product_id=p1_id, quantity=1, acknowledged_unit_price=Decimal("10.00"))],
    )

    barrier = threading.Barrier(2)

    def do_checkout():
        with pg_session_factory() as session:
            barrier.wait(timeout=5)
            order, _ = process_checkout(
                session=session,
                user_id=user_id,
                request=req,
                idempotency_key=f"pg-race-key-{uuid4().hex[:6]}",
                settings=test_settings,
                shipping_policy=shipping_policy,
                tax_policy=tax_policy,
            )
            session.commit()
            return order.id

    def do_cart_mutation():
        with pg_session_factory() as session:
            barrier.wait(timeout=5)
            # Will wait on Cart row lock until checkout commits, then add to the cleared cart
            add_to_cart(session, user_id, p2_id, 2, auto_commit=True)
            return True

    with ThreadPoolExecutor(max_workers=2) as executor:
        f_checkout = executor.submit(do_checkout)
        f_mutation = executor.submit(do_cart_mutation)

    checkout_result = None
    checkout_exc = None
    try:
        checkout_result = f_checkout.result()
    except Exception as exc:
        checkout_exc = exc

    mutation_result = None
    mutation_exc = None
    try:
        mutation_result = f_mutation.result()
    except Exception as exc:
        mutation_exc = exc

    # One of two strictly safe serialized outcomes occurred due to parent cart row locking:
    # Outcome A (Checkout won lock): checkout succeeded, cart cleared, mutation added p2 afterwards
    # Outcome B (Mutation won lock): mutation added p2, checkout detected snapshot mismatch and aborted with 409
    if checkout_result is not None:
        assert mutation_result is True
        with pg_session_factory() as verify_session:
            ord_items = verify_session.scalars(select(OrderItem).where(OrderItem.order_id == checkout_result)).all()
            assert len(ord_items) == 1
            assert ord_items[0].product_id == p1_id
            cart = verify_session.scalar(select(Cart).where(Cart.user_id == user_id))
            assert len(cart.items) == 1
            assert cart.items[0].product_id == p2_id
    else:
        assert isinstance(checkout_exc, HTTPException)
        assert checkout_exc.status_code == 409
        assert "snapshot" in str(checkout_exc.detail).lower()
        assert mutation_result is True
        with pg_session_factory() as verify_session:
            cart = verify_session.scalar(select(Cart).where(Cart.user_id == user_id))
            assert len(cart.items) == 2
            orders = verify_session.scalars(select(Order).where(Order.user_id == user_id)).all()
            assert len(orders) == 0


# ==============================================================================
# 5. First-Time Cart Creation Race
# ==============================================================================
def test_postgres_first_time_cart_creation_race(pg_session_factory):
    """Verify get_or_create_cart_for_update safely handles uq_carts_user_id race on PostgreSQL."""
    with pg_session_factory() as setup_session:
        user, _ = _setup_user_and_address(setup_session, "new_user")
        user_id = user.id

    barrier = threading.Barrier(2)

    def create_cart():
        with pg_session_factory() as session:
            barrier.wait(timeout=5)
            cart = get_or_create_cart_for_update(session, user_id)
            session.commit()
            return cart.id

    with ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(create_cart)
        f2 = executor.submit(create_cart)
        c1 = f1.result()
        c2 = f2.result()

    # Both sessions resolve to the exact same cart
    assert c1 == c2

    # Exactly 1 cart exists in the database
    with pg_session_factory() as verify_session:
        carts = verify_session.scalars(select(Cart).where(Cart.user_id == user_id)).all()
        assert len(carts) == 1


# ==============================================================================
# 6. Product Price Update Racing with Checkout
# ==============================================================================
def test_postgres_price_update_racing_with_checkout(pg_session_factory, test_settings):
    """Verify price changes trigger 409 PRICE_CHANGED and fully roll back checkout mutations."""
    shipping_policy = ConfigurableFlatShippingPolicy(Decimal("0.00"))
    tax_policy = ConfigurablePercentageTaxPolicy(Decimal("0.00"))

    with pg_session_factory() as setup_session:
        user, addr = _setup_user_and_address(setup_session, "price_race")
        prod = Product(sku=f"PRC-{uuid4().hex[:6]}", name="Price Changing Item", price=Decimal("100.00"), is_active=True)
        setup_session.add(prod)
        setup_session.flush()

        setup_session.add(Inventory(product_id=prod.id, available_quantity=5))
        setup_session.commit()

        cart = get_or_create_cart_for_update(setup_session, user.id)
        setup_session.add(CartItem(cart_id=cart.id, product_id=prod.id, quantity=1))
        setup_session.commit()

        user_id = user.id
        addr_id = addr.id
        prod_id = prod.id

    # Admin updates price to 125.00
    with pg_session_factory() as admin_session:
        p = admin_session.get(Product, prod_id)
        p.price = Decimal("125.00")
        admin_session.commit()

    # Checkout request acknowledged old price of 100.00
    req = CheckoutRequest(
        shipping_address_id=addr_id,
        items=[CheckoutItemAcknowledgement(product_id=prod_id, quantity=1, acknowledged_unit_price=Decimal("100.00"))],
    )

    with pg_session_factory() as checkout_session:
        with pytest.raises(HTTPException) as exc_info:
            process_checkout(
                session=checkout_session,
                user_id=user_id,
                request=req,
                idempotency_key=f"pg-price-key-{uuid4().hex[:6]}",
                settings=test_settings,
                shipping_policy=shipping_policy,
                tax_policy=tax_policy,
            )
        assert exc_info.value.status_code == 409
        assert exc_info.value.detail["code"] == "PRICE_CHANGED"

    # Verify complete rollback: zero orders, stock untouched at 5, cart item still present
    with pg_session_factory() as verify_session:
        orders = verify_session.scalars(select(Order).where(Order.user_id == user_id)).all()
        assert len(orders) == 0

        inv = verify_session.scalar(select(Inventory).where(Inventory.product_id == prod_id))
        assert inv.available_quantity == 5

        cart = verify_session.scalar(select(Cart).where(Cart.user_id == user_id))
        assert len(cart.items) == 1


# ==============================================================================
# 7. Address Update/Delete Preserves Immutable Snapshot
# ==============================================================================
def test_postgres_address_mutation_preserves_snapshot(pg_session_factory, test_settings):
    """Verify deleting a customer's address sets shipping_address_id to NULL but preserves all snapshot fields."""
    shipping_policy = ConfigurableFlatShippingPolicy(Decimal("50.00"))
    tax_policy = ConfigurablePercentageTaxPolicy(Decimal("18.00"))

    with pg_session_factory() as setup_session:
        user, addr = _setup_user_and_address(setup_session, "snap")
        prod = Product(sku=f"SNAP-{uuid4().hex[:6]}", name="Snapshot Product", price=Decimal("100.00"), is_active=True)
        setup_session.add(prod)
        setup_session.flush()

        setup_session.add(Inventory(product_id=prod.id, available_quantity=5))
        setup_session.commit()

        cart = get_or_create_cart_for_update(setup_session, user.id)
        setup_session.add(CartItem(cart_id=cart.id, product_id=prod.id, quantity=1))
        setup_session.commit()

        user_id = user.id
        addr_id = addr.id
        prod_id = prod.id

    req = CheckoutRequest(
        shipping_address_id=addr_id,
        items=[CheckoutItemAcknowledgement(product_id=prod_id, quantity=1, acknowledged_unit_price=Decimal("100.00"))],
    )

    with pg_session_factory() as session:
        order, _ = process_checkout(
            session=session,
            user_id=user_id,
            request=req,
            idempotency_key=f"pg-snap-key-{uuid4().hex[:6]}",
            settings=test_settings,
            shipping_policy=shipping_policy,
            tax_policy=tax_policy,
        )
        session.commit()
        order_id = order.id

    # Independent session deletes the address
    with pg_session_factory() as del_session:
        a = del_session.get(Address, addr_id)
        del_session.delete(a)
        del_session.commit()

    # Verify order state: shipping_address_id is NULL, snapshot fields completely intact
    with pg_session_factory() as verify_session:
        ord_rec = verify_session.get(Order, order_id)
        assert ord_rec.shipping_address_id is None
        assert ord_rec.shipping_recipient_name == "PG Test User"
        assert ord_rec.shipping_line1 == "123 Postgres Way"
        assert ord_rec.shipping_city == "Bengaluru"
        assert ord_rec.shipping_region == "Karnataka"
        assert ord_rec.shipping_postal_code == "560001"
        assert ord_rec.shipping_country_code == "IN"


# ==============================================================================
# 8. Order-Number Uniqueness Collision Retry on PostgreSQL
# ==============================================================================
def test_postgres_order_number_collision_retry(pg_session_factory, test_settings, monkeypatch):
    """Verify ix_orders_order_number unique constraint collision is caught, rolled back to savepoint, and retried."""
    shipping_policy = ConfigurableFlatShippingPolicy(Decimal("0.00"))
    tax_policy = ConfigurablePercentageTaxPolicy(Decimal("0.00"))

    collision_target = f"ORD-COLLISION-PG-{uuid4().hex[:6]}"
    retry_success = f"ORD-RETRY-SUCCESS-PG-{uuid4().hex[:6]}"

    with pg_session_factory() as setup_session:
        user, addr = _setup_user_and_address(setup_session, "coll_ord")
        prod = Product(sku=f"ORD-C-{uuid4().hex[:6]}", name="Collision Item", price=Decimal("10.00"), is_active=True)
        setup_session.add(prod)
        setup_session.flush()

        setup_session.add(Inventory(product_id=prod.id, available_quantity=5))
        setup_session.commit()

        cart = get_or_create_cart_for_update(setup_session, user.id)
        setup_session.add(CartItem(cart_id=cart.id, product_id=prod.id, quantity=1))
        setup_session.commit()

        # Pre-seed an existing order with a collision target number
        existing_order = Order(
            order_number=collision_target,
            user_id=user.id,
            shipping_recipient_name=addr.recipient_name,
            shipping_line1=addr.line1,
            shipping_city=addr.city,
            shipping_region=addr.region,
            shipping_postal_code=addr.postal_code,
            shipping_country_code=addr.country_code,
            status=OrderStatus.CONFIRMED.value,
            currency="INR",
            subtotal=Decimal("10.00"),
            shipping_fee=Decimal("0.00"),
            tax=Decimal("0.00"),
            total_amount=Decimal("10.00"),
        )
        setup_session.add(existing_order)
        setup_session.commit()

        user_id = user.id
        addr_id = addr.id
        prod_id = prod.id

    numbers = iter([collision_target, retry_success])
    monkeypatch.setattr(
        "backend.app.services.checkout_service.generate_order_number",
        lambda: next(numbers),
    )

    req = CheckoutRequest(
        shipping_address_id=addr_id,
        items=[CheckoutItemAcknowledgement(product_id=prod_id, quantity=1, acknowledged_unit_price=Decimal("10.00"))],
    )

    with pg_session_factory() as session:
        order, _ = process_checkout(
            session=session,
            user_id=user_id,
            request=req,
            idempotency_key=f"pg-ord-retry-{uuid4().hex[:6]}",
            settings=test_settings,
            shipping_policy=shipping_policy,
            tax_policy=tax_policy,
        )
        session.commit()
        assert order.order_number == retry_success

    with pg_session_factory() as verify_session:
        saved_orders = verify_session.scalars(select(Order).where(Order.user_id == user_id)).all()
        order_numbers = [o.order_number for o in saved_orders]
        assert collision_target in order_numbers
        assert retry_success in order_numbers


# ==============================================================================
# 9. Full Transaction Rollback After Simulated Failure
# ==============================================================================
def test_postgres_full_transaction_rollback_after_simulated_failure(pg_session_factory, test_settings):
    """Verify that if an error occurs during checkout, all changes (stock, order, cart) roll back cleanly."""
    shipping_policy = ConfigurableFlatShippingPolicy(Decimal("0.00"))
    tax_policy = ConfigurablePercentageTaxPolicy(Decimal("0.00"))

    with pg_session_factory() as setup_session:
        user, addr = _setup_user_and_address(setup_session, "rollback")
        prod = Product(sku=f"RB-{uuid4().hex[:6]}", name="Rollback Item", price=Decimal("10.00"), is_active=True)
        setup_session.add(prod)
        setup_session.flush()

        setup_session.add(Inventory(product_id=prod.id, available_quantity=10))
        setup_session.commit()

        cart = get_or_create_cart_for_update(setup_session, user.id)
        setup_session.add(CartItem(cart_id=cart.id, product_id=prod.id, quantity=3))
        setup_session.commit()

        user_id = user.id
        addr_id = addr.id
        prod_id = prod.id

    req = CheckoutRequest(
        shipping_address_id=addr_id,
        items=[CheckoutItemAcknowledgement(product_id=prod_id, quantity=3, acknowledged_unit_price=Decimal("10.00"))],
    )

    with pg_session_factory() as session:
        try:
            # Execute checkout steps inside uncommitted transaction
            order, _ = process_checkout(
                session=session,
                user_id=user_id,
                request=req,
                idempotency_key=f"pg-fail-key-{uuid4().hex[:6]}",
                settings=test_settings,
                shipping_policy=shipping_policy,
                tax_policy=tax_policy,
            )
            # Simulate unexpected failure right before commit
            raise RuntimeError("Simulated transient failure before commit!")
        except Exception:
            session.rollback()

    # Verify database state in an independent session: NO changes persisted
    with pg_session_factory() as verify_session:
        # Zero orders created for this user
        orders = verify_session.scalars(select(Order).where(Order.user_id == user_id)).all()
        assert len(orders) == 0

        # Stock remains at original 10
        inv = verify_session.scalar(select(Inventory).where(Inventory.product_id == prod_id))
        assert inv.available_quantity == 10

        # Cart still has all 3 units
        cart = verify_session.scalar(select(Cart).where(Cart.user_id == user_id))
        assert len(cart.items) == 1
        assert cart.items[0].quantity == 3


# ==============================================================================
# 10. Migration 0003 -> 0004 Upgrade and 0004 -> 0003 Downgrade Roundtrip
# ==============================================================================
def test_postgres_migration_0003_to_0004_roundtrip(monkeypatch):
    """Verify Alembic migration 0003 -> 0004 upgrade, SHA-256 backfill, check constraints, and 0004 -> 0003 downgrade."""
    url = _get_postgres_test_url()
    assert url is not None

    monkeypatch.setenv("DATABASE_URL", url)
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))

    # 0. Stamp current head to ensure alembic_version table is initialized
    try:
        command.stamp(config, "0004_order_fingerprint")
    except Exception:
        pass

    # 1. Downgrade to 0003_cart_orders
    command.downgrade(config, "0003_cart_orders")

    engine = create_engine(url)
    user_id = uuid4()
    order_id = uuid4()
    legacy_key = f"legacy-pg-key-{uuid4().hex[:6]}"

    try:
        insp = inspect(engine)
        orders_cols = {c["name"] for c in insp.get_columns("orders")}
        assert "request_fingerprint" not in orders_cols

        # Insert legacy order with idempotency_key and NULL request_fingerprint (0003 schema)
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO users (id, email, password_hash, is_active) "
                    "VALUES (:id, :email, 'hash', true)"
                ),
                {"id": user_id, "email": f"legacy_pg_{uuid4().hex[:6]}@example.com"},
            )
            conn.execute(
                text(
                    "INSERT INTO orders (id, order_number, user_id, shipping_recipient_name, "
                    "shipping_line1, shipping_city, shipping_region, shipping_postal_code, "
                    "shipping_country_code, status, currency, subtotal, shipping_fee, tax, "
                    "total_amount, idempotency_key) "
                    "VALUES (:id, :ord_num, :user_id, 'Recipient', 'Line 1', 'City', 'KA', '560001', "
                    "'IN', 'confirmed', 'INR', 100.00, 0.00, 0.00, 100.00, :key)"
                ),
                {"id": order_id, "ord_num": f"ORD-LEGACY-PG-{uuid4().hex[:6]}", "user_id": user_id, "key": legacy_key},
            )

        # 2. Upgrade to 0004_order_fingerprint
        command.upgrade(config, "0004_order_fingerprint")

        # Verify request_fingerprint column added and legacy order backfilled
        insp2 = inspect(engine)
        orders_cols2 = {c["name"] for c in insp2.get_columns("orders")}
        assert "request_fingerprint" in orders_cols2

        with engine.connect() as conn:
            backfilled_fp = conn.execute(
                text("SELECT request_fingerprint FROM orders WHERE id = :id"),
                {"id": order_id},
            ).scalar()
            expected_fp = hashlib.sha256(f"legacy_backfill:{str(order_id)}:{legacy_key}".encode("utf-8")).hexdigest()
            assert backfilled_fp == expected_fp
            assert len(backfilled_fp) == 64

            # 3. Test check constraint enforcement on PostgreSQL
            # Case A: Key populated + fingerprint NULL -> must be rejected
            with pytest.raises(IntegrityError):
                with conn.begin_nested():
                    conn.execute(
                        text(
                            "INSERT INTO orders (id, order_number, user_id, shipping_recipient_name, "
                            "shipping_line1, shipping_city, shipping_region, shipping_postal_code, "
                            "shipping_country_code, status, currency, subtotal, shipping_fee, tax, "
                            "total_amount, idempotency_key, request_fingerprint) "
                            "VALUES (:id, :ord_num, :user_id, 'R', 'L', 'C', 'KA', '560001', "
                            "'IN', 'confirmed', 'INR', 10.0, 0.0, 0.0, 10.0, 'key-no-fp', NULL)"
                        ),
                        {"id": uuid4(), "ord_num": f"ORD-FAIL-1-{uuid4().hex[:6]}", "user_id": user_id},
                    )

            # Case B: Key populated + 63-char fingerprint -> must be rejected
            with pytest.raises(IntegrityError):
                with conn.begin_nested():
                    conn.execute(
                        text(
                            "INSERT INTO orders (id, order_number, user_id, shipping_recipient_name, "
                            "shipping_line1, shipping_city, shipping_region, shipping_postal_code, "
                            "shipping_country_code, status, currency, subtotal, shipping_fee, tax, "
                            "total_amount, idempotency_key, request_fingerprint) "
                            "VALUES (:id, :ord_num, :user_id, 'R', 'L', 'C', 'KA', '560001', "
                            "'IN', 'confirmed', 'INR', 10.0, 0.0, 0.0, 10.0, 'key-bad-len', :bad_fp)"
                        ),
                        {"id": uuid4(), "ord_num": f"ORD-FAIL-2-{uuid4().hex[:6]}", "user_id": user_id, "bad_fp": "a" * 63},
                    )

        # 4. Downgrade back to 0003_cart_orders
        command.downgrade(config, "0003_cart_orders")
        insp3 = inspect(engine)
        orders_cols3 = {c["name"] for c in insp3.get_columns("orders")}
        assert "request_fingerprint" not in orders_cols3

        # 5. Re-upgrade to head (0004_order_fingerprint) so database is ready for all operations
        command.upgrade(config, "head")

    finally:
        engine.dispose()
