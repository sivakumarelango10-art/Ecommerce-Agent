from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import jwt
import pytest
from sqlalchemy.exc import IntegrityError

from backend.app.db.models import Inventory, Product, ProductCategory, User
from backend.app.security.passwords import verify_password


PASSWORD = "safe-test-password-2026"


def register_and_login(client, email: str) -> tuple[str, dict[str, object]]:
    registration = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": PASSWORD,
            "display_name": "Test Customer",
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


def test_registration_login_and_responses_exclude_password_data(api_client):
    client, _ = api_client

    token, registered_user = register_and_login(
        client, "test-customer@example.com"
    )
    decoded = jwt.decode(
        token,
        client.app.state.settings.auth_secret_key.get_secret_value(),
        algorithms=["HS256"],
    )
    assert decoded["sub"] == registered_user["id"]
    assert registered_user["email"] == "test-customer@example.com"
    assert "password" not in registered_user
    assert "password_hash" not in registered_user

    profile = client.get("/api/v1/customers/me/profile", headers=auth_header(token))
    assert profile.status_code == 200
    assert profile.json()["display_name"] == "Test Customer"
    assert "password_hash" not in profile.json()


def test_password_is_argon2_hashed_and_login_errors_are_generic(api_client):
    client, _ = api_client
    _, registered_user = register_and_login(client, "hash-test@example.com")
    session = client.app.state.db_session_factory()
    try:
        user = session.get(User, UUID(registered_user["id"]))
        assert user is not None
        assert user.password_hash.startswith("$argon2id$")
        assert user.password_hash != PASSWORD
        assert verify_password(PASSWORD, user.password_hash)
    finally:
        session.close()

    response = client.post(
        "/api/v1/auth/login",
        json={"email": "hash-test@example.com", "password": "incorrect-password"},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Email or password is incorrect."


def test_duplicate_email_is_case_insensitive(api_client):
    client, _ = api_client
    register_and_login(client, "duplicate-test@example.com")

    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": "DUPLICATE-TEST@example.com",
            "password": PASSWORD,
            "display_name": "Another Test Customer",
        },
    )
    assert response.status_code == 409


def test_authentication_and_expiration_are_enforced(api_client):
    client, _ = api_client
    _, user = register_and_login(client, "expiry-test@example.com")
    missing_auth = client.get("/api/v1/customers/me/profile")
    assert missing_auth.status_code == 401

    expired_token = jwt.encode(
        {
            "sub": user["id"],
            "iat": datetime.now(timezone.utc) - timedelta(minutes=3),
            "exp": datetime.now(timezone.utc) - timedelta(minutes=1),
            "type": "access",
        },
        client.app.state.settings.auth_secret_key.get_secret_value(),
        algorithm="HS256",
    )
    expired_response = client.get(
        "/api/v1/customers/me/profile",
        headers=auth_header(expired_token),
    )
    assert expired_response.status_code == 401


def test_customers_cannot_read_update_or_delete_each_others_addresses(api_client):
    client, _ = api_client
    owner_token, _ = register_and_login(client, "owner@example.com")
    other_token, _ = register_and_login(client, "other@example.com")
    body = {
        "label": "home",
        "recipient_name": "Test Customer",
        "line1": "123 Test Street",
        "city": "Testville",
        "region": "Test Region",
        "postal_code": "00000",
        "country_code": "US",
    }
    created = client.post(
        "/api/v1/customers/me/addresses",
        headers=auth_header(owner_token),
        json=body,
    )
    assert created.status_code == 201
    address_id = created.json()["id"]

    other_headers = auth_header(other_token)
    assert (
        client.put(
            f"/api/v1/customers/me/addresses/{address_id}",
            headers=other_headers,
            json=body,
        ).status_code
        == 404
    )
    assert (
        client.get(
            f"/api/v1/customers/me/addresses/{address_id}",
            headers=other_headers,
        ).status_code
        == 404
    )
    assert (
        client.delete(
            f"/api/v1/customers/me/addresses/{address_id}",
            headers=other_headers,
        ).status_code
        == 404
    )
    owner_addresses = client.get(
        "/api/v1/customers/me/addresses",
        headers=auth_header(owner_token),
    )
    assert len(owner_addresses.json()) == 1


def test_catalogue_and_inventory_endpoints_return_only_public_fields(api_client):
    client, _ = api_client
    session = client.app.state.db_session_factory()
    try:
        category = ProductCategory(name="Test category", slug="test-category")
        product = Product(
            sku="TEST-SKU-001",
            name="Test product",
            description="Synthetic catalogue fixture",
            price=Decimal("12.50"),
            categories=[category],
            inventory=Inventory(available_quantity=3),
        )
        session.add(product)
        session.commit()
        product_id = product.id
    finally:
        session.close()

    products = client.get("/api/v1/products?category=test-category")
    assert products.status_code == 200
    assert products.json()[0]["sku"] == "TEST-SKU-001"
    assert "inventory" not in products.json()[0]

    categories = client.get("/api/v1/categories")
    assert categories.json()[0]["slug"] == "test-category"

    availability = client.get(f"/api/v1/products/{product_id}/availability")
    assert availability.json() == {
        "product_id": str(product_id),
        "available": True,
        "available_quantity": 3,
    }


def test_database_constraints_reject_negative_price_and_inventory(api_client):
    client, _ = api_client
    session = client.app.state.db_session_factory()
    try:
        product = Product(sku="NEGATIVE-PRICE", name="Invalid", price=Decimal("-1"))
        session.add(product)
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

        product = Product(sku="VALID-PRICE", name="Valid", price=Decimal("1"))
        product.inventory = Inventory(available_quantity=-1)
        session.add(product)
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
    finally:
        session.close()


def test_unowned_address_id_is_not_disclosed(api_client):
    client, _ = api_client
    token, _ = register_and_login(client, "no-address@example.com")
    response = client.get(
        f"/api/v1/customers/me/addresses/{uuid4()}",
        headers=auth_header(token),
    )
    assert response.status_code == 404
