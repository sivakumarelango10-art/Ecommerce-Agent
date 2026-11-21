from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.constants import DEFAULT_CATALOGUE_CURRENCY
from backend.app.db.models import Cart, CartItem, Inventory, Product, User
from backend.app.services.cart_service import (
    add_to_cart,
    clear_cart,
    get_or_create_cart,
    remove_cart_item,
    update_cart_item_quantity,
)

PASSWORD = "safe-test-password-2026"


def register_and_login(client, email: str) -> tuple[str, dict[str, object]]:
    registration = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": PASSWORD,
            "display_name": "Cart Customer",
        },
    )
    assert registration.status_code == 201

    login = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": PASSWORD},
    )
    assert login.status_code == 200
    return login.json()["access_token"], registration.json()


def auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def create_test_product(
    client,
    sku: str = "CART-SKU-1",
    name: str = "Noise Cancelling Headphones",
    price: Decimal = Decimal("199.99"),
    available_quantity: int = 10,
    is_active: bool = True,
) -> Product:
    session = client.app.state.db_session_factory()
    try:
        product = Product(
            id=uuid4(),
            sku=sku,
            name=name,
            price=price,
            is_active=is_active,
        )
        inv = Inventory(
            product_id=product.id,
            available_quantity=available_quantity,
        )
        session.add_all([product, inv])
        session.commit()
        session.refresh(product)
        return product
    finally:
        session.close()


# --- 1. Unauthenticated Cart Requests are Rejected ---

def test_cart_unauthenticated_requests_rejected(api_client):
    client, _ = api_client
    random_id = uuid4()

    assert client.get("/api/v1/cart").status_code == 401
    assert client.post("/api/v1/cart/items", json={"product_id": str(random_id), "quantity": 1}).status_code == 401
    assert client.patch(f"/api/v1/cart/items/{random_id}", json={"quantity": 2}).status_code == 401
    assert client.delete(f"/api/v1/cart/items/{random_id}").status_code == 401
    assert client.delete("/api/v1/cart").status_code == 401


# --- 2. Authenticated Cart Retrieval and Automatic Creation ---

def test_authenticated_cart_retrieval_and_automatic_creation(api_client):
    client, _ = api_client
    token, user = register_and_login(client, "auto_cart@example.com")

    response = client.get("/api/v1/cart", headers=auth_header(token))
    assert response.status_code == 200

    data = response.json()
    assert data["user_id"] == user["id"]
    assert data["items"] == []
    assert data["subtotal"] == "0.00"
    assert data["currency"] == DEFAULT_CATALOGUE_CURRENCY
    assert data["item_count"] == 0
    assert data["total_quantity"] == 0

    # Verify Cart record exists in database
    session = client.app.state.db_session_factory()
    try:
        cart_in_db = session.get(Cart, UUID(data["id"]))
        assert cart_in_db is not None
        assert cart_in_db.user_id == UUID(user["id"])
    finally:
        session.close()


# --- 3. Adding a Product to Cart & Decimal Price Calculations ---

def test_add_product_to_cart_and_decimal_calculations(api_client):
    client, _ = api_client
    token, _ = register_and_login(client, "add_item@example.com")
    prod = create_test_product(
        client,
        sku="CALC-PROD-1",
        name="Premium Keyboard",
        price=Decimal("149.50"),
        available_quantity=8,
    )

    response = client.post(
        "/api/v1/cart/items",
        json={"product_id": str(prod.id), "quantity": 2},
        headers=auth_header(token),
    )
    assert response.status_code == 200

    data = response.json()
    assert data["item_count"] == 1
    assert data["total_quantity"] == 2
    assert data["currency"] == "INR"
    assert data["subtotal"] == "299.00"  # 149.50 * 2 = 299.00

    item = data["items"][0]
    assert item["product_id"] == str(prod.id)
    assert item["product_sku"] == "CALC-PROD-1"
    assert item["product_name"] == "Premium Keyboard"
    assert item["unit_price"] == "149.50"
    assert item["quantity"] == 2
    assert item["subtotal"] == "299.00"
    assert item["line_total"] == "299.00"


# --- 4. Adding the Same Product Twice Increments Quantity ---

def test_add_same_product_twice_increments_quantity(api_client):
    client, _ = api_client
    token, _ = register_and_login(client, "twice_user@example.com")
    prod = create_test_product(
        client,
        sku="INC-PROD-1",
        price=Decimal("49.99"),
        available_quantity=10,
    )

    # First add: 2 units
    res1 = client.post(
        "/api/v1/cart/items",
        json={"product_id": str(prod.id), "quantity": 2},
        headers=auth_header(token),
    )
    assert res1.status_code == 200
    assert res1.json()["items"][0]["quantity"] == 2
    assert res1.json()["subtotal"] == "99.98"

    # Second add: 3 units -> Total 5 units
    res2 = client.post(
        "/api/v1/cart/items",
        json={"product_id": str(prod.id), "quantity": 3},
        headers=auth_header(token),
    )
    assert res2.status_code == 200
    data = res2.json()
    # No duplicate item entries
    assert len(data["items"]) == 1
    assert data["items"][0]["quantity"] == 5
    assert data["items"][0]["subtotal"] == "249.95"  # 49.99 * 5
    assert data["subtotal"] == "249.95"
    assert data["total_quantity"] == 5


# --- 5. Updating Cart Item Quantity ---

def test_update_cart_item_quantity(api_client):
    client, _ = api_client
    token, _ = register_and_login(client, "update_qty@example.com")
    prod = create_test_product(
        client,
        sku="UPD-PROD-1",
        price=Decimal("25.00"),
        available_quantity=10,
    )

    add_res = client.post(
        "/api/v1/cart/items",
        json={"product_id": str(prod.id), "quantity": 2},
        headers=auth_header(token),
    )
    item_id = add_res.json()["items"][0]["id"]

    # Update quantity from 2 to 6
    patch_res = client.patch(
        f"/api/v1/cart/items/{item_id}",
        json={"quantity": 6},
        headers=auth_header(token),
    )
    assert patch_res.status_code == 200
    data = patch_res.json()
    assert data["items"][0]["quantity"] == 6
    assert data["items"][0]["subtotal"] == "150.00"
    assert data["subtotal"] == "150.00"
    assert data["total_quantity"] == 6


# --- 6. Invalid Quantities are Rejected (<= 0 or Malformed) ---

def test_invalid_quantities_rejected(api_client):
    client, _ = api_client
    token, _ = register_and_login(client, "bad_qty@example.com")
    prod = create_test_product(client, sku="QTY-TEST-1", available_quantity=10)

    # Quantity 0
    res_zero = client.post(
        "/api/v1/cart/items",
        json={"product_id": str(prod.id), "quantity": 0},
        headers=auth_header(token),
    )
    assert res_zero.status_code == 422

    # Negative quantity
    res_neg = client.post(
        "/api/v1/cart/items",
        json={"product_id": str(prod.id), "quantity": -3},
        headers=auth_header(token),
    )
    assert res_neg.status_code == 422

    # Non-integer quantity
    res_str = client.post(
        "/api/v1/cart/items",
        json={"product_id": str(prod.id), "quantity": "invalid"},
        headers=auth_header(token),
    )
    assert res_str.status_code == 422

    # Valid add first
    valid_add = client.post(
        "/api/v1/cart/items",
        json={"product_id": str(prod.id), "quantity": 1},
        headers=auth_header(token),
    )
    item_id = valid_add.json()["items"][0]["id"]

    # Update with quantity 0
    res_patch_zero = client.patch(
        f"/api/v1/cart/items/{item_id}",
        json={"quantity": 0},
        headers=auth_header(token),
    )
    assert res_patch_zero.status_code == 422


# --- 7. Missing Product is Rejected with 404 ---

def test_missing_product_rejected(api_client):
    client, _ = api_client
    token, _ = register_and_login(client, "missing_prod@example.com")
    non_existent_id = uuid4()

    response = client.post(
        "/api/v1/cart/items",
        json={"product_id": str(non_existent_id), "quantity": 1},
        headers=auth_header(token),
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "Product not found."


# --- 8. Inactive and Unavailable Products Rejected ---

def test_inactive_and_out_of_stock_products_rejected(api_client):
    client, _ = api_client
    token, _ = register_and_login(client, "inactive_prod@example.com")

    # Inactive product
    inactive_prod = create_test_product(
        client,
        sku="INACTIVE-1",
        is_active=False,
        available_quantity=10,
    )
    res_inactive = client.post(
        "/api/v1/cart/items",
        json={"product_id": str(inactive_prod.id), "quantity": 1},
        headers=auth_header(token),
    )
    assert res_inactive.status_code == 400
    assert "not available" in res_inactive.json()["detail"].lower()

    # Out-of-stock product (available_quantity = 0)
    oos_prod = create_test_product(
        client,
        sku="OOS-1",
        is_active=True,
        available_quantity=0,
    )
    res_oos = client.post(
        "/api/v1/cart/items",
        json={"product_id": str(oos_prod.id), "quantity": 1},
        headers=auth_header(token),
    )
    assert res_oos.status_code == 400
    assert "out of stock" in res_oos.json()["detail"].lower()

    # Requested quantity exceeds stock
    low_stock_prod = create_test_product(
        client,
        sku="LOW-1",
        is_active=True,
        available_quantity=3,
    )
    res_exceed = client.post(
        "/api/v1/cart/items",
        json={"product_id": str(low_stock_prod.id), "quantity": 5},
        headers=auth_header(token),
    )
    assert res_exceed.status_code == 400
    assert "exceeds" in res_exceed.json()["detail"].lower()


# --- 9. Removing Individual Cart Items ---

def test_remove_individual_cart_item(api_client):
    client, _ = api_client
    token, _ = register_and_login(client, "remove_item@example.com")
    p1 = create_test_product(client, sku="REM-1", price=Decimal("10.00"))
    p2 = create_test_product(client, sku="REM-2", price=Decimal("20.00"))

    res1 = client.post("/api/v1/cart/items", json={"product_id": str(p1.id), "quantity": 1}, headers=auth_header(token))
    res2 = client.post("/api/v1/cart/items", json={"product_id": str(p2.id), "quantity": 2}, headers=auth_header(token))
    assert res2.status_code == 200
    assert res2.json()["item_count"] == 2

    item1_id = res1.json()["items"][0]["id"]

    # Delete item1
    del_res = client.delete(f"/api/v1/cart/items/{item1_id}", headers=auth_header(token))
    assert del_res.status_code == 204

    # Verify cart has only item2
    cart_res = client.get("/api/v1/cart", headers=auth_header(token))
    data = cart_res.json()
    assert data["item_count"] == 1
    assert data["items"][0]["product_sku"] == "REM-2"
    assert data["subtotal"] == "40.00"

    # Attempt to delete already deleted item returns 404
    del_again = client.delete(f"/api/v1/cart/items/{item1_id}", headers=auth_header(token))
    assert del_again.status_code == 404


# --- 10. Clearing the Entire Cart ---

def test_clear_entire_cart(api_client):
    client, _ = api_client
    token, _ = register_and_login(client, "clear_cart@example.com")
    p1 = create_test_product(client, sku="CLR-1", price=Decimal("15.00"))
    p2 = create_test_product(client, sku="CLR-2", price=Decimal("35.00"))

    client.post("/api/v1/cart/items", json={"product_id": str(p1.id), "quantity": 1}, headers=auth_header(token))
    client.post("/api/v1/cart/items", json={"product_id": str(p2.id), "quantity": 1}, headers=auth_header(token))

    clear_res = client.delete("/api/v1/cart", headers=auth_header(token))
    assert clear_res.status_code == 204

    # Cart is now empty
    get_res = client.get("/api/v1/cart", headers=auth_header(token))
    data = get_res.json()
    assert data["items"] == []
    assert data["subtotal"] == "0.00"
    assert data["item_count"] == 0
    assert data["total_quantity"] == 0


# --- 11. Cross-User Access Prevention ---

def test_cross_user_access_prevention(api_client):
    client, _ = api_client
    token_a, _ = register_and_login(client, "user_a@example.com")
    token_b, _ = register_and_login(client, "user_b@example.com")

    prod = create_test_product(client, sku="ISOLATE-1", price=Decimal("100.00"))

    # User A adds item to cart
    res_a = client.post(
        "/api/v1/cart/items",
        json={"product_id": str(prod.id), "quantity": 2},
        headers=auth_header(token_a),
    )
    item_a_id = res_a.json()["items"][0]["id"]

    # User B attempts to PATCH User A's cart item -> 404 Not Found
    patch_b = client.patch(
        f"/api/v1/cart/items/{item_a_id}",
        json={"quantity": 5},
        headers=auth_header(token_b),
    )
    assert patch_b.status_code == 404
    assert patch_b.json()["detail"] == "Cart item not found."

    # User B attempts to DELETE User A's cart item -> 404 Not Found
    del_b = client.delete(
        f"/api/v1/cart/items/{item_a_id}",
        headers=auth_header(token_b),
    )
    assert del_b.status_code == 404

    # User A's cart item is unchanged
    cart_a = client.get("/api/v1/cart", headers=auth_header(token_a)).json()
    assert cart_a["items"][0]["quantity"] == 2

    # User B's cart remains completely empty
    cart_b = client.get("/api/v1/cart", headers=auth_header(token_b)).json()
    assert cart_b["items"] == []


# --- 12. Dynamic Trusted Price Derivation (Catalogue Price Change) ---

def test_dynamic_trusted_price_derivation(api_client):
    client, _ = api_client
    token, _ = register_and_login(client, "dynamic_price@example.com")
    prod = create_test_product(
        client,
        sku="PRICE-CHANGE-1",
        name="Adaptive Headset",
        price=Decimal("100.00"),
        available_quantity=10,
    )

    client.post(
        "/api/v1/cart/items",
        json={"product_id": str(prod.id), "quantity": 3},
        headers=auth_header(token),
    )

    # Initial check: 100.00 * 3 = 300.00
    cart_before = client.get("/api/v1/cart", headers=auth_header(token)).json()
    assert cart_before["items"][0]["unit_price"] == "100.00"
    assert cart_before["subtotal"] == "300.00"

    # Simulate catalogue price change by store admin to 125.50
    session = client.app.state.db_session_factory()
    try:
        db_prod = session.get(Product, prod.id)
        db_prod.price = Decimal("125.50")
        session.commit()
    finally:
        session.close()

    # Cart automatically derives updated price from trusted catalogue without user intervention
    cart_after = client.get("/api/v1/cart", headers=auth_header(token)).json()
    assert cart_after["items"][0]["unit_price"] == "125.50"
    assert cart_after["items"][0]["subtotal"] == "376.50"  # 125.50 * 3
    assert cart_after["subtotal"] == "376.50"


# --- 13. Client-Supplied User ID or Price is Safely Ignored ---

def test_client_supplied_user_id_or_price_ignored(api_client):
    client, _ = api_client
    token, user = register_and_login(client, "spoof_user@example.com")
    prod = create_test_product(client, sku="SPOOF-1", price=Decimal("50.00"))

    fake_user_id = uuid4()
    # Client maliciously sends fake user_id and fake price=0.01
    payload = {
        "product_id": str(prod.id),
        "quantity": 1,
        "user_id": str(fake_user_id),
        "price": "0.01",
        "subtotal": "0.01",
    }
    response = client.post("/api/v1/cart/items", json=payload, headers=auth_header(token))
    assert response.status_code == 200

    data = response.json()
    # Scoped strictly to authenticated user
    assert data["user_id"] == user["id"]
    assert data["user_id"] != str(fake_user_id)
    # Price is derived from trusted product model, not client payload
    assert data["items"][0]["unit_price"] == "50.00"
    assert data["subtotal"] == "50.00"


# --- 14. Safe Concurrent Cart Creation (Service Level) ---

def test_safe_concurrent_cart_creation(api_client):
    client, _ = api_client
    session = client.app.state.db_session_factory()
    try:
        user = User(
            id=uuid4(),
            email="concurrent_test@example.com",
            password_hash="argon2id$mocked",
        )
        session.add(user)
        session.commit()

        # First call creates the cart
        c1 = get_or_create_cart(session, user.id)
        assert c1 is not None

        # Second call returns the exact same cart cleanly
        c2 = get_or_create_cart(session, user.id)
        assert c2.id == c1.id
    finally:
        session.close()


# --- 15. Update Quantity of Inactive Product is Rejected ---

def test_update_cart_item_inactive_product_rejected(api_client):
    client, _ = api_client
    token, _ = register_and_login(client, "deact_item@example.com")
    prod = create_test_product(client, sku="DEACT-1", price=Decimal("75.00"), available_quantity=5)

    add_res = client.post(
        "/api/v1/cart/items",
        json={"product_id": str(prod.id), "quantity": 1},
        headers=auth_header(token),
    )
    assert add_res.status_code == 200
    item_id = add_res.json()["items"][0]["id"]

    # Deactivate the product in catalog
    session = client.app.state.db_session_factory()
    try:
        db_prod = session.get(Product, prod.id)
        db_prod.is_active = False
        session.commit()
    finally:
        session.close()

    # Attempting to update quantity is rejected
    patch_res = client.patch(
        f"/api/v1/cart/items/{item_id}",
        json={"quantity": 2},
        headers=auth_header(token),
    )
    assert patch_res.status_code == 400
    assert "not available" in patch_res.json()["detail"].lower()


# --- 16. Non-Existent Cart Item Update or Delete Returns 404 ---

def test_non_existent_cart_item_returns_404(api_client):
    client, _ = api_client
    token, _ = register_and_login(client, "empty_cart_user@example.com")
    missing_item_id = uuid4()

    patch_res = client.patch(
        f"/api/v1/cart/items/{missing_item_id}",
        json={"quantity": 2},
        headers=auth_header(token),
    )
    assert patch_res.status_code == 404
    assert patch_res.json()["detail"] == "Cart item not found."

    del_res = client.delete(
        f"/api/v1/cart/items/{missing_item_id}",
        headers=auth_header(token),
    )
    assert del_res.status_code == 404
    assert del_res.json()["detail"] == "Cart item not found."


# --- 17. Transaction Ownership: Service Does Not Commit Caller Session ---

def test_cart_service_does_not_commit_caller_transaction(api_client):
    client, _ = api_client
    factory = client.app.state.db_session_factory
    prod = create_test_product(client, sku="TX-PROD-1", price=Decimal("30.00"), available_quantity=10)

    session = factory()
    try:
        user = User(
            id=uuid4(),
            email="tx_owner@example.com",
            password_hash="argon2id$mocked",
        )
        unrelated_user = User(
            id=uuid4(),
            email="unrelated_work@example.com",
            password_hash="argon2id$mocked",
        )
        session.add_all([user, unrelated_user])
        session.commit()

        # In a new caller-owned transaction, add unrelated work
        another_user = User(
            id=uuid4(),
            email="another_unrelated@example.com",
            password_hash="argon2id$mocked",
        )
        session.add(another_user)
        session.flush()

        # Call service method without auto_commit
        cart = add_to_cart(session, user.id, prod.id, 2, auto_commit=False)
        assert cart is not None

        # Verify caller's session has NOT been committed
        # Caller explicitly rolls back the outer transaction
        session.rollback()

        # Verify neither the cart item nor another_user was committed
        s2 = factory()
        try:
            assert s2.get(User, another_user.id) is None
            user_cart = s2.scalar(select(Cart).where(Cart.user_id == user.id))
            assert user_cart is None or len(user_cart.items) == 0
        finally:
            s2.close()
    finally:
        session.close()


# --- 18. Savepoint Isolation: IntegrityError Does Not Roll Back Outer Work ---

def test_savepoint_failure_preserves_unrelated_outer_work_and_session_usability(api_client):
    client, _ = api_client
    factory = client.app.state.db_session_factory
    user = User(
        id=uuid4(),
        email="sp_isolation@example.com",
        password_hash="argon2id$mocked",
    )
    s_init = factory()
    s_init.add(user)
    s_init.commit()
    s_init.close()

    session1 = factory()
    try:
        # Caller adds unrelated work in session 1
        unrelated_entity = User(
            id=uuid4(),
            email="must_survive@example.com",
            password_hash="argon2id$mocked",
        )
        session1.add(unrelated_entity)
        session1.flush()

        # Simulate concurrent transaction creating cart in session 2
        session2 = factory()
        try:
            cart2 = Cart(user_id=user.id)
            session2.add(cart2)
            session2.commit()
        finally:
            session2.close()

        # In session 1, call get_or_create_cart which hits IntegrityError on uq_carts_user_id
        cart1 = get_or_create_cart(session1, user.id, auto_commit=False)
        assert cart1.id == cart2.id

        # Verify unrelated entity in session 1 was NOT rolled back by the savepoint recovery
        assert unrelated_entity in session1
        assert session1.is_active

        # Verify session 1 is completely usable for further queries and commit
        session1.commit()

        # Verify unrelated entity was cleanly committed
        s_verify = factory()
        try:
            assert s_verify.get(User, unrelated_entity.id) is not None
        finally:
            s_verify.close()
    finally:
        session1.close()


# --- 19. Concurrent Insert Conflict Recovery & Atomic Quantity Increments ---

def test_concurrent_insert_conflict_recovers_and_increments_safely(api_client):
    client, _ = api_client
    factory = client.app.state.db_session_factory
    prod = create_test_product(client, sku="RACE-PROD-1", price=Decimal("20.00"), available_quantity=10)

    user = User(
        id=uuid4(),
        email="race_user@example.com",
        password_hash="argon2id$mocked",
    )
    s_init = factory()
    s_init.add(user)
    s_init.commit()
    c = get_or_create_cart(s_init, user.id, auto_commit=True)
    s_init.close()

    # Session 2 concurrently inserts item with quantity 2
    s2 = factory()
    try:
        item2 = CartItem(cart_id=c.id, product_id=prod.id, quantity=2)
        s2.add(item2)
        s2.commit()
    finally:
        s2.close()

    # Session 1 concurrently calls add_to_cart with quantity 3
    s1 = factory()
    try:
        cart_res = add_to_cart(s1, user.id, prod.id, 3, auto_commit=True)
        assert cart_res is not None
        assert len(cart_res.items) == 1
        assert cart_res.items[0].quantity == 5  # 2 + 3 = 5, no lost update!
    finally:
        s1.close()


# --- 20. Concurrent Increment Rejects When Combined Quantity Exceeds Stock ---

def test_concurrent_increment_respects_stock_limit(api_client):
    client, _ = api_client
    factory = client.app.state.db_session_factory
    # Total available inventory is 5
    prod = create_test_product(client, sku="STOCK-RACE-1", price=Decimal("15.00"), available_quantity=5)

    user = User(
        id=uuid4(),
        email="stock_race_user@example.com",
        password_hash="argon2id$mocked",
    )
    s_init = factory()
    s_init.add(user)
    s_init.commit()
    c = get_or_create_cart(s_init, user.id, auto_commit=True)
    # Existing item in cart already has 4 units
    item_init = CartItem(cart_id=c.id, product_id=prod.id, quantity=4)
    s_init.add(item_init)
    s_init.commit()
    s_init.close()

    # Concurrent request attempts to add 2 units (4 + 2 = 6 > 5 available)
    s1 = factory()
    try:
        with pytest.raises(Exception) as exc_info:
            add_to_cart(s1, user.id, prod.id, 2, auto_commit=False)
        assert "exceeds available inventory" in str(exc_info.value.detail).lower()
    finally:
        s1.close()

    # Verify database still has 4 units
    s_verify = factory()
    try:
        c_verify = s_verify.get(Cart, c.id)
        assert c_verify.items[0].quantity == 4
    finally:
        s_verify.close()


