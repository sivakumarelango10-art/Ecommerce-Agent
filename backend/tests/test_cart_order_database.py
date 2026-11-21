from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from backend.app.core.constants import DEFAULT_CATALOGUE_CURRENCY
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
    ProductCategory,
    User,
)


@pytest.fixture
def isolated_db():
    engine = create_engine("sqlite+pysqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _create_user(session: Session, email_prefix: str = "test") -> User:
    user = User(
        id=uuid4(),
        email=f"{email_prefix}_{uuid4().hex[:8]}@example.com",
        password_hash="argon2id$hashedpassword",
    )
    session.add(user)
    session.commit()
    return user


def _create_product(
    session: Session,
    sku: str = "PROD-01",
    name: str = "Wireless Earbuds",
    price: Decimal = Decimal("99.99"),
) -> Product:
    product = Product(
        id=uuid4(),
        sku=sku,
        name=name,
        price=price,
        is_active=True,
    )
    inv = Inventory(product_id=product.id, available_quantity=20)
    session.add_all([product, inv])
    session.commit()
    return product


def _create_address(session: Session, user: User) -> Address:
    addr = Address(
        id=uuid4(),
        user_id=user.id,
        recipient_name="John Doe",
        line1="123 Tech Park",
        city="Bengaluru",
        region="Karnataka",
        postal_code="560001",
        country_code="IN",
    )
    session.add(addr)
    session.commit()
    return addr


# --- 1. Cart Creation and User Uniqueness ---

def test_cart_creation_and_user_uniqueness(isolated_db: Session):
    user = _create_user(isolated_db, "cart_user")

    # Create first cart for user
    cart1 = Cart(user_id=user.id)
    isolated_db.add(cart1)
    isolated_db.commit()

    assert cart1.id is not None
    assert cart1.user_id == user.id
    assert cart1.user.id == user.id
    assert user.cart.id == cart1.id

    # Attempt to create a second cart for the same user -> violates unique constraint
    cart2 = Cart(user_id=user.id)
    isolated_db.add(cart2)
    with pytest.raises(IntegrityError):
        isolated_db.commit()
    isolated_db.rollback()


# --- 2. Multiple Cart Items ---

def test_multiple_cart_items(isolated_db: Session):
    user = _create_user(isolated_db, "multi_item_user")
    cart = Cart(user_id=user.id)
    isolated_db.add(cart)
    isolated_db.commit()

    p1 = _create_product(isolated_db, sku="SKU-1", name="Product 1")
    p2 = _create_product(isolated_db, sku="SKU-2", name="Product 2")
    p3 = _create_product(isolated_db, sku="SKU-3", name="Product 3")

    item1 = CartItem(cart_id=cart.id, product_id=p1.id, quantity=2)
    item2 = CartItem(cart_id=cart.id, product_id=p2.id, quantity=1)
    item3 = CartItem(cart_id=cart.id, product_id=p3.id, quantity=5)
    isolated_db.add_all([item1, item2, item3])
    isolated_db.commit()

    isolated_db.refresh(cart)
    assert len(cart.items) == 3
    assert {it.product.sku for it in cart.items} == {"SKU-1", "SKU-2", "SKU-3"}


# --- 3. Duplicate Product Prevention Within One Cart ---

def test_duplicate_product_prevention_within_one_cart(isolated_db: Session):
    u1 = _create_user(isolated_db, "u1")
    u2 = _create_user(isolated_db, "u2")
    cart1 = Cart(user_id=u1.id)
    cart2 = Cart(user_id=u2.id)
    isolated_db.add_all([cart1, cart2])
    isolated_db.commit()

    prod = _create_product(isolated_db, sku="DUP-SKU")

    # Add to cart1
    item1 = CartItem(cart_id=cart1.id, product_id=prod.id, quantity=1)
    isolated_db.add(item1)
    isolated_db.commit()

    # Attempt to add same product to cart1 again -> violates uq_cart_items_cart_product
    item1_dup = CartItem(cart_id=cart1.id, product_id=prod.id, quantity=2)
    isolated_db.add(item1_dup)
    with pytest.raises(IntegrityError):
        isolated_db.commit()
    isolated_db.rollback()

    # Adding same product to cart2 (different cart/user) -> succeeds
    item2 = CartItem(cart_id=cart2.id, product_id=prod.id, quantity=3)
    isolated_db.add(item2)
    isolated_db.commit()
    assert item2.id is not None


# --- 4. Positive Quantity Constraints ---

def test_positive_quantity_constraints(isolated_db: Session):
    user = _create_user(isolated_db, "qty_user")
    cart = Cart(user_id=user.id)
    prod = _create_product(isolated_db, sku="QTY-PROD")
    isolated_db.add(cart)
    isolated_db.commit()

    # Cart item quantity = 0 -> violates check constraint
    item_zero = CartItem(cart_id=cart.id, product_id=prod.id, quantity=0)
    isolated_db.add(item_zero)
    with pytest.raises(IntegrityError):
        isolated_db.commit()
    isolated_db.rollback()

    # Cart item quantity = -5 -> violates check constraint
    item_neg = CartItem(cart_id=cart.id, product_id=prod.id, quantity=-5)
    isolated_db.add(item_neg)
    with pytest.raises(IntegrityError):
        isolated_db.commit()
    isolated_db.rollback()

    # Order item quantity = 0 -> violates check constraint
    order = Order(
        order_number="ORD-QTY-01",
        user_id=user.id,
        shipping_recipient_name="John",
        shipping_line1="123 St",
        shipping_city="City",
        shipping_region="Region",
        shipping_postal_code="560001",
        shipping_country_code="IN",
        subtotal=Decimal("100.00"),
        total_amount=Decimal("100.00"),
    )
    isolated_db.add(order)
    isolated_db.commit()

    order_item_zero = OrderItem(
        order_id=order.id,
        product_id=prod.id,
        product_sku=prod.sku,
        product_name=prod.name,
        unit_price=prod.price,
        quantity=0,
        line_total=Decimal("0.00"),
    )
    isolated_db.add(order_item_zero)
    with pytest.raises(IntegrityError):
        isolated_db.commit()
    isolated_db.rollback()


# --- 5. Order Creation and User Ownership Relationship ---

def test_order_creation_and_user_ownership(isolated_db: Session):
    user = _create_user(isolated_db, "order_user")
    addr = _create_address(isolated_db, user)

    order = Order(
        order_number="ORD-20261121-0001",
        user_id=user.id,
        shipping_address_id=addr.id,
        shipping_recipient_name=addr.recipient_name,
        shipping_line1=addr.line1,
        shipping_city=addr.city,
        shipping_region=addr.region,
        shipping_postal_code=addr.postal_code,
        shipping_country_code=addr.country_code,
        status=OrderStatus.PENDING.value,
        currency=DEFAULT_CATALOGUE_CURRENCY,
        subtotal=Decimal("199.98"),
        shipping_fee=Decimal("0.00"),
        tax=Decimal("18.00"),
        total_amount=Decimal("217.98"),
    )
    isolated_db.add(order)
    isolated_db.commit()

    assert order.id is not None
    assert order.user.id == user.id
    assert order.currency == "INR"
    assert len(user.orders) == 1
    assert user.orders[0].id == order.id


# --- 6. Unique Order Numbers ---

def test_unique_order_numbers(isolated_db: Session):
    u1 = _create_user(isolated_db, "u_ord1")
    u2 = _create_user(isolated_db, "u_ord2")

    ord1 = Order(
        order_number="ORD-DUP-001",
        user_id=u1.id,
        shipping_recipient_name="User 1",
        shipping_line1="123 St",
        shipping_city="City",
        shipping_region="Region",
        shipping_postal_code="560001",
        shipping_country_code="IN",
        subtotal=Decimal("100.00"),
        total_amount=Decimal("100.00"),
    )
    isolated_db.add(ord1)
    isolated_db.commit()

    ord2 = Order(
        order_number="ORD-DUP-001",  # Duplicate order_number
        user_id=u2.id,
        shipping_recipient_name="User 2",
        shipping_line1="456 Ave",
        shipping_city="City",
        shipping_region="Region",
        shipping_postal_code="560001",
        shipping_country_code="IN",
        subtotal=Decimal("200.00"),
        total_amount=Decimal("200.00"),
    )
    isolated_db.add(ord2)
    with pytest.raises(IntegrityError):
        isolated_db.commit()
    isolated_db.rollback()


# --- 7. User-Scoped Idempotency Uniqueness ---

def test_user_scoped_idempotency_uniqueness(isolated_db: Session):
    u1 = _create_user(isolated_db, "idemp_u1")
    u2 = _create_user(isolated_db, "idemp_u2")

    ord1 = Order(
        order_number="ORD-IDEMP-01",
        user_id=u1.id,
        shipping_recipient_name="User 1",
        shipping_line1="123 St",
        shipping_city="City",
        shipping_region="Region",
        shipping_postal_code="560001",
        shipping_country_code="IN",
        subtotal=Decimal("100.00"),
        total_amount=Decimal("100.00"),
        idempotency_key="checkout-token-xyz",
        request_fingerprint="a" * 64,
    )
    isolated_db.add(ord1)
    isolated_db.commit()

    # Same user with duplicate idempotency key -> violates unique constraint
    ord1_dup = Order(
        order_number="ORD-IDEMP-02",
        user_id=u1.id,
        shipping_recipient_name="User 1",
        shipping_line1="123 St",
        shipping_city="City",
        shipping_region="Region",
        shipping_postal_code="560001",
        shipping_country_code="IN",
        subtotal=Decimal("150.00"),
        total_amount=Decimal("150.00"),
        idempotency_key="checkout-token-xyz",
        request_fingerprint="a" * 64,
    )
    isolated_db.add(ord1_dup)
    with pytest.raises(IntegrityError):
        isolated_db.commit()
    isolated_db.rollback()

    # Different user with the same idempotency key -> allowed
    ord2 = Order(
        order_number="ORD-IDEMP-03",
        user_id=u2.id,
        shipping_recipient_name="User 2",
        shipping_line1="456 Ave",
        shipping_city="City",
        shipping_region="Region",
        shipping_postal_code="560001",
        shipping_country_code="IN",
        subtotal=Decimal("200.00"),
        total_amount=Decimal("200.00"),
        idempotency_key="checkout-token-xyz",
        request_fingerprint="b" * 64,
    )
    isolated_db.add(ord2)
    isolated_db.commit()
    assert ord2.id is not None


# --- 8. Multiple NULL Idempotency Keys ---

def test_multiple_orders_with_null_idempotency_keys(isolated_db: Session):
    user = _create_user(isolated_db, "null_idemp_user")

    orders = [
        Order(
            order_number=f"ORD-NULL-{i}",
            user_id=user.id,
            shipping_recipient_name="User",
            shipping_line1="123 St",
            shipping_city="City",
            shipping_region="Region",
            shipping_postal_code="560001",
            shipping_country_code="IN",
            subtotal=Decimal("100.00"),
            total_amount=Decimal("100.00"),
            idempotency_key=None,
        )
        for i in range(4)
    ]
    isolated_db.add_all(orders)
    isolated_db.commit()

    assert len(user.orders) == 4
    assert all(o.idempotency_key is None for o in user.orders)


# --- 9. Order Item Price Snapshots ---

def test_order_item_price_snapshots(isolated_db: Session):
    user = _create_user(isolated_db, "snapshot_user")
    prod = _create_product(
        isolated_db,
        sku="SNAP-01",
        name="Original Gaming Headset",
        price=Decimal("120.00"),
    )

    order = Order(
        order_number="ORD-SNAP-001",
        user_id=user.id,
        shipping_recipient_name="User",
        shipping_line1="123 St",
        shipping_city="City",
        shipping_region="Region",
        shipping_postal_code="560001",
        shipping_country_code="IN",
        subtotal=Decimal("240.00"),
        total_amount=Decimal("240.00"),
    )
    isolated_db.add(order)
    isolated_db.commit()

    item = OrderItem(
        order_id=order.id,
        product_id=prod.id,
        product_sku=prod.sku,
        product_name=prod.name,
        unit_price=prod.price,
        quantity=2,
        line_total=Decimal("240.00"),
    )
    isolated_db.add(item)
    isolated_db.commit()

    # Now simulate product catalogue changes: price increase and name change
    prod.price = Decimal("199.99")
    prod.name = "Renamed Gaming Headset Pro"
    isolated_db.commit()

    # Verify historical OrderItem record remains unchanged
    isolated_db.refresh(item)
    assert item.product_name == "Original Gaming Headset"
    assert item.product_sku == "SNAP-01"
    assert item.unit_price == Decimal("120.00")
    assert item.line_total == Decimal("240.00")


# --- 10. Shipping Address Reference Integrity ---

def test_shipping_address_reference_integrity(isolated_db: Session):
    user = _create_user(isolated_db, "addr_user")
    addr = _create_address(isolated_db, user)

    order = Order(
        order_number="ORD-ADDR-001",
        user_id=user.id,
        shipping_address_id=addr.id,
        shipping_recipient_name=addr.recipient_name,
        shipping_line1=addr.line1,
        shipping_city=addr.city,
        shipping_region=addr.region,
        shipping_postal_code=addr.postal_code,
        shipping_country_code=addr.country_code,
        subtotal=Decimal("50.00"),
        total_amount=Decimal("50.00"),
    )
    isolated_db.add(order)
    isolated_db.commit()

    assert order.shipping_address.id == addr.id
    assert order.shipping_recipient_name == "John Doe"
    assert order.shipping_line1 == "123 Tech Park"


# --- 11. Foreign-Key Deletion Behavior ---

def test_foreign_key_deletion_behavior(isolated_db: Session):
    user = _create_user(isolated_db, "fk_del_user")
    addr = _create_address(isolated_db, user)
    prod = _create_product(isolated_db, sku="FK-PROD-01")

    # 1. Cart CASCADE deletion on user deletion
    cart = Cart(user_id=user.id)
    isolated_db.add(cart)
    isolated_db.commit()
    c_item = CartItem(cart_id=cart.id, product_id=prod.id, quantity=1)
    isolated_db.add(c_item)
    isolated_db.commit()

    order = Order(
        order_number="ORD-FK-001",
        user_id=user.id,
        shipping_address_id=addr.id,
        shipping_recipient_name=addr.recipient_name,
        shipping_line1=addr.line1,
        shipping_city=addr.city,
        shipping_region=addr.region,
        shipping_postal_code=addr.postal_code,
        shipping_country_code=addr.country_code,
        subtotal=Decimal("99.99"),
        total_amount=Decimal("99.99"),
    )
    isolated_db.add(order)
    isolated_db.commit()

    o_item = OrderItem(
        order_id=order.id,
        product_id=prod.id,
        product_sku=prod.sku,
        product_name=prod.name,
        unit_price=prod.price,
        quantity=1,
        line_total=Decimal("99.99"),
    )
    isolated_db.add(o_item)
    isolated_db.commit()

    # 2. SET NULL: Deleting referenced shipping address sets shipping_address_id to NULL
    isolated_db.delete(addr)
    isolated_db.commit()
    isolated_db.refresh(order)
    assert order.shipping_address_id is None
    assert order.shipping_recipient_name == "John Doe"
    assert order.shipping_line1 == "123 Tech Park"

    # 3. RESTRICT: Deleting product referenced by OrderItem is blocked
    prod_ref = isolated_db.get(Product, prod.id)
    isolated_db.delete(prod_ref)
    with pytest.raises(IntegrityError):
        isolated_db.commit()
    isolated_db.rollback()

    # 4. CASCADE: Deleting cart deletes cart_items
    cart_ref = isolated_db.get(Cart, cart.id)
    isolated_db.delete(cart_ref)
    isolated_db.commit()
    assert isolated_db.get(CartItem, c_item.id) is None

    # 5. RESTRICT: Deleting user with historical orders is blocked
    user_ref = isolated_db.get(User, user.id)
    isolated_db.delete(user_ref)
    with pytest.raises(IntegrityError):
        isolated_db.commit()
    isolated_db.rollback()

    # Order and order-items are preserved
    assert isolated_db.get(Order, order.id) is not None
    assert isolated_db.get(OrderItem, o_item.id) is not None


# --- 12. Order Status and Monetary Constraints ---

def test_order_status_and_monetary_constraints(isolated_db: Session):
    user = _create_user(isolated_db, "constraints_user")

    # Invalid status -> check constraint violation
    ord_bad_status = Order(
        order_number="ORD-BAD-STATUS",
        user_id=user.id,
        shipping_recipient_name="User",
        shipping_line1="123 St",
        shipping_city="City",
        shipping_region="Region",
        shipping_postal_code="560001",
        shipping_country_code="IN",
        status="invalid_status",
        subtotal=Decimal("100.00"),
        total_amount=Decimal("100.00"),
    )
    isolated_db.add(ord_bad_status)
    with pytest.raises(IntegrityError):
        isolated_db.commit()
    isolated_db.rollback()

    # Negative subtotal -> check constraint violation
    ord_neg_subtotal = Order(
        order_number="ORD-NEG-SUB",
        user_id=user.id,
        shipping_recipient_name="User",
        shipping_line1="123 St",
        shipping_city="City",
        shipping_region="Region",
        shipping_postal_code="560001",
        shipping_country_code="IN",
        subtotal=Decimal("-10.00"),
        total_amount=Decimal("100.00"),
    )
    isolated_db.add(ord_neg_subtotal)
    with pytest.raises(IntegrityError):
        isolated_db.commit()
    isolated_db.rollback()

    # Negative total amount -> check constraint violation
    ord_neg_total = Order(
        order_number="ORD-NEG-TOT",
        user_id=user.id,
        shipping_recipient_name="User",
        shipping_line1="123 St",
        shipping_city="City",
        shipping_region="Region",
        shipping_postal_code="560001",
        shipping_country_code="IN",
        subtotal=Decimal("100.00"),
        total_amount=Decimal("-50.00"),
    )
    isolated_db.add(ord_neg_total)
    with pytest.raises(IntegrityError):
        isolated_db.commit()
    isolated_db.rollback()

    # Invalid currency length != 3 -> check constraint violation
    ord_bad_curr = Order(
        order_number="ORD-BAD-CURR",
        user_id=user.id,
        shipping_recipient_name="User",
        shipping_line1="123 St",
        shipping_city="City",
        shipping_region="Region",
        shipping_postal_code="560001",
        shipping_country_code="IN",
        currency="TOOLONG",
        subtotal=Decimal("100.00"),
        total_amount=Decimal("100.00"),
    )
    isolated_db.add(ord_bad_curr)
    with pytest.raises(IntegrityError):
        isolated_db.commit()
    isolated_db.rollback()

    # All valid statuses succeed
    for i, st in enumerate(OrderStatus):
        valid_ord = Order(
            order_number=f"ORD-VALID-{i}",
            user_id=user.id,
            shipping_recipient_name="User",
            shipping_line1="123 St",
            shipping_city="City",
            shipping_region="Region",
            shipping_postal_code="560001",
            shipping_country_code="IN",
            status=st.value,
            subtotal=Decimal("100.00"),
            total_amount=Decimal("100.00"),
        )
        isolated_db.add(valid_ord)
        isolated_db.commit()
        assert valid_ord.status == st.value


# --- 13. Historical Order Preservation on User Deletion ---

def test_user_deletion_restricted_with_historical_orders(isolated_db: Session):
    user = _create_user(isolated_db, "hist_user")
    order = Order(
        order_number="ORD-HIST-001",
        user_id=user.id,
        shipping_recipient_name="Historical Customer",
        shipping_line1="100 History Way",
        shipping_city="Bengaluru",
        shipping_region="Karnataka",
        shipping_postal_code="560001",
        shipping_country_code="IN",
        subtotal=Decimal("500.00"),
        shipping_fee=Decimal("50.00"),
        tax=Decimal("90.00"),
        total_amount=Decimal("640.00"),
    )
    isolated_db.add(order)
    isolated_db.commit()

    # Attempting to delete the user must fail due to RESTRICT policy
    isolated_db.delete(user)
    with pytest.raises(IntegrityError):
        isolated_db.commit()
    isolated_db.rollback()

    # Historical order record remains completely preserved
    saved_order = isolated_db.get(Order, order.id)
    assert saved_order is not None
    assert saved_order.order_number == "ORD-HIST-001"
    assert saved_order.user_id == user.id


# --- 14. Shipping Address Deletion Preserves Immutable Snapshot ---

def test_shipping_address_deletion_sets_null_and_preserves_snapshot(isolated_db: Session):
    user = _create_user(isolated_db, "snap_addr_user")
    addr = _create_address(isolated_db, user)

    order = Order(
        order_number="ORD-SNAP-ADDR",
        user_id=user.id,
        shipping_address_id=addr.id,
        shipping_recipient_name="Recipient Person",
        shipping_line1="456 Prime Avenue",
        shipping_line2="Apt 2B",
        shipping_city="Bengaluru",
        shipping_region="Karnataka",
        shipping_postal_code="560002",
        shipping_country_code="IN",
        subtotal=Decimal("150.00"),
        shipping_fee=Decimal("0.00"),
        tax=Decimal("27.00"),
        total_amount=Decimal("177.00"),
    )
    isolated_db.add(order)
    isolated_db.commit()

    assert order.shipping_address_id == addr.id

    # Customer deletes address from address book
    isolated_db.delete(addr)
    isolated_db.commit()

    # Foreign key was SET NULL, but the full snapshot remains intact
    isolated_db.refresh(order)
    assert order.shipping_address_id is None
    assert order.shipping_recipient_name == "Recipient Person"
    assert order.shipping_line1 == "456 Prime Avenue"
    assert order.shipping_line2 == "Apt 2B"
    assert order.shipping_city == "Bengaluru"
    assert order.shipping_region == "Karnataka"
    assert order.shipping_postal_code == "560002"
    assert order.shipping_country_code == "IN"


# --- 15. Monetary Integrity: Order Total Calculation Constraint ---

def test_order_total_amount_calculation_constraint(isolated_db: Session):
    user = _create_user(isolated_db, "calc_user")

    # Invalid: total_amount != subtotal + shipping_fee + tax
    # 100.00 + 10.00 + 5.00 = 115.00, but total_amount is 120.00
    bad_order = Order(
        order_number="ORD-BAD-TOTAL",
        user_id=user.id,
        shipping_recipient_name="User",
        shipping_line1="123 St",
        shipping_city="City",
        shipping_region="Region",
        shipping_postal_code="560001",
        shipping_country_code="IN",
        subtotal=Decimal("100.00"),
        shipping_fee=Decimal("10.00"),
        tax=Decimal("5.00"),
        total_amount=Decimal("120.00"),
    )
    isolated_db.add(bad_order)
    with pytest.raises(IntegrityError):
        isolated_db.commit()
    isolated_db.rollback()

    # Valid: total_amount == subtotal + shipping_fee + tax
    good_order = Order(
        order_number="ORD-GOOD-TOTAL",
        user_id=user.id,
        shipping_recipient_name="User",
        shipping_line1="123 St",
        shipping_city="City",
        shipping_region="Region",
        shipping_postal_code="560001",
        shipping_country_code="IN",
        subtotal=Decimal("100.00"),
        shipping_fee=Decimal("10.00"),
        tax=Decimal("5.00"),
        total_amount=Decimal("115.00"),
    )
    isolated_db.add(good_order)
    isolated_db.commit()
    assert good_order.total_amount == Decimal("115.00")


# --- 16. Monetary Integrity: OrderItem Line Total Calculation Constraint ---

def test_order_item_line_total_calculation_constraint(isolated_db: Session):
    user = _create_user(isolated_db, "item_calc_user")
    prod = _create_product(isolated_db, sku="ITEM-CALC-01", price=Decimal("25.50"))

    order = Order(
        order_number="ORD-ITEM-CALC",
        user_id=user.id,
        shipping_recipient_name="User",
        shipping_line1="123 St",
        shipping_city="City",
        shipping_region="Region",
        shipping_postal_code="560001",
        shipping_country_code="IN",
        subtotal=Decimal("51.00"),
        shipping_fee=Decimal("0.00"),
        tax=Decimal("0.00"),
        total_amount=Decimal("51.00"),
    )
    isolated_db.add(order)
    isolated_db.commit()

    # Invalid: line_total != unit_price * quantity
    # 25.50 * 2 = 51.00, but line_total is 50.00
    bad_item = OrderItem(
        order_id=order.id,
        product_id=prod.id,
        product_sku=prod.sku,
        product_name=prod.name,
        unit_price=Decimal("25.50"),
        quantity=2,
        line_total=Decimal("50.00"),
    )
    isolated_db.add(bad_item)
    with pytest.raises(IntegrityError):
        isolated_db.commit()
    isolated_db.rollback()

    # Valid: line_total == unit_price * quantity
    good_item = OrderItem(
        order_id=order.id,
        product_id=prod.id,
        product_sku=prod.sku,
        product_name=prod.name,
        unit_price=Decimal("25.50"),
        quantity=2,
        line_total=Decimal("51.00"),
    )
    isolated_db.add(good_item)
    isolated_db.commit()
    assert good_item.line_total == Decimal("51.00")


# --- 17. Schema Indexes and Constraint Verification ---

def test_schema_indexes_and_foreign_keys(isolated_db: Session):
    from sqlalchemy import inspect

    bind = isolated_db.get_bind()
    inspector = inspect(bind)

    # 1. Carts: no redundant ix_carts_user_id
    cart_indexes = {idx["name"] for idx in inspector.get_indexes("carts")}
    assert "ix_carts_user_id" not in cart_indexes

    # 2. Cart items: no redundant ix_cart_items_cart_id, retains ix_cart_items_product_id
    cart_item_indexes = {idx["name"] for idx in inspector.get_indexes("cart_items")}
    assert "ix_cart_items_cart_id" not in cart_item_indexes
    assert "ix_cart_items_product_id" in cart_item_indexes

    # 3. Orders: no redundant ix_orders_user_id, retains order_number and user_created
    order_indexes = {idx["name"] for idx in inspector.get_indexes("orders")}
    assert "ix_orders_user_id" not in order_indexes
    assert "ix_orders_order_number" in order_indexes
    assert "ix_orders_user_created" in order_indexes

    # 4. Foreign key policies
    orders_fks = {fk["referred_table"]: fk for fk in inspector.get_foreign_keys("orders")}
    assert orders_fks["users"]["options"].get("ondelete") == "RESTRICT"
    assert orders_fks["addresses"]["options"].get("ondelete") == "SET NULL"


# --- 18. Isolated PostgreSQL Migration Verification (Optional) ---

def _get_pg_test_db_url() -> str | None:
    import os
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
        url = os.getenv("PHASE_5_TEST_DATABASE_URL")
    if not url:
        return None
    from urllib.parse import urlparse
    parsed = urlparse(url)
    db_name = parsed.path.lstrip("/")
    if db_name != "ecommerce_agent_test":
        raise RuntimeError(f"Safety violation: Expected ecommerce_agent_test, got {db_name}")
    if "dev" in db_name.lower():
        raise RuntimeError("Safety violation: Cannot target dev database")
    return url


@pytest.mark.skipif(
    not _get_pg_test_db_url(),
    reason="Set POSTGRES_TEST_DATABASE_URL to an isolated local PostgreSQL test database.",
)
def test_postgresql_migration_0003_upgrade_downgrade(monkeypatch):
    from pathlib import Path
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect

    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "alembic.ini"))
    url = _get_pg_test_db_url()
    assert url is not None
    monkeypatch.setenv("DATABASE_URL", url)
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))

    # Upgrade to head
    command.upgrade(config, "head")

    pg_engine = create_engine(url)
    try:
        insp = inspect(pg_engine)
        tables = set(insp.get_table_names(schema="public"))
        assert {"carts", "cart_items", "orders", "order_items"}.issubset(tables)
    finally:
        pg_engine.dispose()

    # Downgrade back to 0002_constraint_names
    command.downgrade(config, "0002_constraint_names")
    pg_engine2 = create_engine(url)
    try:
        insp2 = inspect(pg_engine2)
        tables2 = set(insp2.get_table_names(schema="public"))
        assert "carts" not in tables2
        assert "orders" not in tables2
    finally:
        pg_engine2.dispose()

    # Re-upgrade to head so database is ready for subsequent operations
    command.upgrade(config, "head")


# --- 19. Request Fingerprint Check Constraint Validations ---

def test_request_fingerprint_check_constraint_validations(isolated_db: Session):
    user = _create_user(isolated_db, "fp_constraint_user")

    # 1. Both fields NULL is allowed
    ord_both_null = Order(
        order_number="ORD-FP-01",
        user_id=user.id,
        shipping_recipient_name="User",
        shipping_line1="123 St",
        shipping_city="City",
        shipping_region="Region",
        shipping_postal_code="560001",
        shipping_country_code="IN",
        subtotal=Decimal("100.00"),
        total_amount=Decimal("100.00"),
        idempotency_key=None,
        request_fingerprint=None,
    )
    isolated_db.add(ord_both_null)
    isolated_db.commit()
    assert ord_both_null.id is not None

    # 2. Both fields populated with a 64-character fingerprint is allowed
    ord_both_valid = Order(
        order_number="ORD-FP-02",
        user_id=user.id,
        shipping_recipient_name="User",
        shipping_line1="123 St",
        shipping_city="City",
        shipping_region="Region",
        shipping_postal_code="560001",
        shipping_country_code="IN",
        subtotal=Decimal("100.00"),
        total_amount=Decimal("100.00"),
        idempotency_key="valid-key-002",
        request_fingerprint="f" * 64,
    )
    isolated_db.add(ord_both_valid)
    isolated_db.commit()
    assert ord_both_valid.id is not None

    # 3. Key populated + fingerprint NULL is rejected
    ord_key_only = Order(
        order_number="ORD-FP-03",
        user_id=user.id,
        shipping_recipient_name="User",
        shipping_line1="123 St",
        shipping_city="City",
        shipping_region="Region",
        shipping_postal_code="560001",
        shipping_country_code="IN",
        subtotal=Decimal("100.00"),
        total_amount=Decimal("100.00"),
        idempotency_key="key-without-fp",
        request_fingerprint=None,
    )
    isolated_db.add(ord_key_only)
    with pytest.raises(IntegrityError):
        isolated_db.commit()
    isolated_db.rollback()

    # 4. Key NULL + fingerprint populated is rejected
    ord_fp_only = Order(
        order_number="ORD-FP-04",
        user_id=user.id,
        shipping_recipient_name="User",
        shipping_line1="123 St",
        shipping_city="City",
        shipping_region="Region",
        shipping_postal_code="560001",
        shipping_country_code="IN",
        subtotal=Decimal("100.00"),
        total_amount=Decimal("100.00"),
        idempotency_key=None,
        request_fingerprint="f" * 64,
    )
    isolated_db.add(ord_fp_only)
    with pytest.raises(IntegrityError):
        isolated_db.commit()
    isolated_db.rollback()

    # 5. Invalid fingerprint length (< 64 or > 64) is rejected
    ord_invalid_len = Order(
        order_number="ORD-FP-05",
        user_id=user.id,
        shipping_recipient_name="User",
        shipping_line1="123 St",
        shipping_city="City",
        shipping_region="Region",
        shipping_postal_code="560001",
        shipping_country_code="IN",
        subtotal=Decimal("100.00"),
        total_amount=Decimal("100.00"),
        idempotency_key="key-with-short-fp",
        request_fingerprint="short_fingerprint_less_than_64_chars",
    )
    isolated_db.add(ord_invalid_len)
    with pytest.raises(IntegrityError):
        isolated_db.commit()
    isolated_db.rollback()
