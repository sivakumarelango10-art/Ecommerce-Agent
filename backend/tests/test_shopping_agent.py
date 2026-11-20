from datetime import datetime, timezone
from decimal import Decimal
import json
from uuid import uuid4

import httpx
import pytest
from google.genai import errors, types
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.app.db.base import Base
from backend.app.db.models import Inventory, Product, ProductCategory, User
from backend.app.schemas.shopping import (
    ShoppingIntent,
    ShoppingSearchRequest,
    SortOrder,
)
from backend.app.security.passwords import hash_password
from backend.app.security.tokens import create_access_token
from backend.app.services.gemini_service import (
    GeminiProviderError,
    GeminiResponseError,
    GeminiTimeoutError,
)
from backend.app.services.product_discovery_service import search_catalogue_products
from backend.app.services.shopping_agent import ShoppingAgent
from backend.app.services.supervisor_agent import SupervisorAgent


@pytest.fixture
def isolated_db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    session = session_factory()

    # Seed test categories
    cat_audio = ProductCategory(id=uuid4(), name="Audio & Headphones", slug="audio")
    cat_footwear = ProductCategory(id=uuid4(), name="Footwear", slug="footwear")
    cat_electronics = ProductCategory(id=uuid4(), name="Electronics", slug="electronics")
    session.add_all([cat_audio, cat_footwear, cat_electronics])
    session.flush()

    # Seed test products
    # 1. Active, Audio & Electronics, In Stock, $349.99
    prod_sony = Product(
        id=uuid4(),
        sku="SONY-WH1000XM5",
        name="Sony WH-1000XM5 Wireless Headphones",
        description="Premium noise cancelling wireless over-ear headphones.",
        price=Decimal("349.99"),
        is_active=True,
        categories=[cat_audio, cat_electronics],
    )
    inv_sony = Inventory(product_id=prod_sony.id, available_quantity=10)

    # 2. Active, Audio, In Stock, $49.99
    prod_anker = Product(
        id=uuid4(),
        sku="ANK-SND-01",
        name="Anker Soundcore Budget Earbuds",
        description="Affordable wireless earbuds with great battery life.",
        price=Decimal("49.99"),
        is_active=True,
        categories=[cat_audio],
    )
    inv_anker = Inventory(product_id=prod_anker.id, available_quantity=25)

    # 3. Active, Footwear, In Stock, $120.00
    prod_nike = Product(
        id=uuid4(),
        sku="NIKE-AZ-01",
        name="Nike Air Zoom Running Shoes",
        description="Comfortable cushioned road running shoes.",
        price=Decimal("120.00"),
        is_active=True,
        categories=[cat_footwear],
    )
    inv_nike = Inventory(product_id=prod_nike.id, available_quantity=5)

    # 4. Active, Footwear, OUT OF STOCK (qty=0), $180.00
    prod_adidas = Product(
        id=uuid4(),
        sku="ADI-UB-01",
        name="Adidas Ultraboost Running Shoes",
        description="High performance responsive running sneakers.",
        price=Decimal("180.00"),
        is_active=True,
        categories=[cat_footwear],
    )
    inv_adidas = Inventory(product_id=prod_adidas.id, available_quantity=0)

    # 5. INACTIVE, Audio, $9.99
    prod_cable = Product(
        id=uuid4(),
        sku="DISC-CBL-01",
        name="Discontinued 3.5mm Audio Cable",
        description="Legacy auxiliary audio cable.",
        price=Decimal("9.99"),
        is_active=False,
        categories=[cat_audio],
    )
    inv_cable = Inventory(product_id=prod_cable.id, available_quantity=100)

    session.add_all([
        prod_sony, inv_sony,
        prod_anker, inv_anker,
        prod_nike, inv_nike,
        prod_adidas, inv_adidas,
        prod_cable, inv_cable,
    ])
    session.commit()

    yield session

    session.close()
    engine.dispose()


class MockGeminiText:
    def __init__(self, answer_text: str | Exception):
        self.answer_text = answer_text
        self.calls: list[str] = []

    def answer(self, question: str) -> str:
        self.calls.append(question)
        if isinstance(self.answer_text, Exception):
            raise self.answer_text
        return self.answer_text


# --- 1. Intent Extraction & Schema Validation ---

def test_intent_schema_validation():
    intent = ShoppingIntent(
        keywords=["wireless", "headphones"],
        category="audio",
        min_budget=Decimal("50.00"),
        max_budget=Decimal("200.00"),
        brand="Sony",
        in_stock_only=True,
        sort_by=SortOrder.PRICE_ASC,
    )
    assert intent.keywords == ["wireless", "headphones"]
    assert intent.max_budget == Decimal("200.00")
    assert intent.is_shopping_intent is True
    assert intent.is_ambiguous_or_insufficient is False


def test_intent_extraction_parses_gemini_json():
    mock_json = json.dumps({
        "keywords": ["running", "shoes"],
        "category": "footwear",
        "max_budget": 150.0,
        "brand": "Nike",
        "in_stock_only": True,
        "sort_by": "price_asc",
        "is_shopping_intent": True,
        "is_ambiguous_or_insufficient": False,
    })
    gemini = MockGeminiText(f"```json\n{mock_json}\n```")
    agent = ShoppingAgent(gemini)

    intent = agent.extract_intent("Find Nike running shoes under $150")

    assert intent.keywords == ["running", "shoes"]
    assert intent.category == "footwear"
    assert intent.max_budget == Decimal("150.0")
    assert intent.brand == "Nike"
    assert intent.is_ambiguous_or_insufficient is False


def test_intent_extraction_handles_ambiguous_and_insufficient_query():
    gemini = MockGeminiText(json.dumps({
        "keywords": [],
        "is_shopping_intent": True,
        "is_ambiguous_or_insufficient": True,
        "clarification_message": "What kind of product or budget are you looking for?",
    }))
    agent = ShoppingAgent(gemini)

    intent = agent.extract_intent("I want to buy something")

    assert intent.is_ambiguous_or_insufficient is True
    assert "What kind" in (intent.clarification_message or "")


def test_intent_extraction_fallback_on_unparseable_response():
    gemini = MockGeminiText("Here are some great options for you to buy!")
    agent = ShoppingAgent(gemini)

    intent = agent.extract_intent("Buy running shoes under $100")

    assert intent.is_shopping_intent is True
    assert intent.max_budget == Decimal("100")
    assert "running" in intent.keywords or "shoes" in intent.keywords


# --- 2. Price Filters ---

def test_price_filters(isolated_db: Session):
    # Under $100: should match Anker ($49.99), exclude Nike ($120) and Sony ($349.99)
    products, count = search_catalogue_products(
        isolated_db,
        max_price=Decimal("100.00"),
        in_stock_only=True,
    )
    assert count == 1
    assert products[0].sku == "ANK-SND-01"

    # Between $100 and $200: should match Nike ($120), exclude out-of-stock Adidas ($180) when in_stock_only=True
    products, count = search_catalogue_products(
        isolated_db,
        min_price=Decimal("100.00"),
        max_price=Decimal("200.00"),
        in_stock_only=True,
    )
    assert count == 1
    assert products[0].sku == "NIKE-AZ-01"

    # Over $300: should match Sony ($349.99)
    products, count = search_catalogue_products(
        isolated_db,
        min_price=Decimal("300.00"),
        in_stock_only=True,
    )
    assert count == 1
    assert products[0].sku == "SONY-WH1000XM5"


# --- 3. Category Filters ---

def test_category_filters(isolated_db: Session):
    # Filter by category slug "audio"
    products, count = search_catalogue_products(
        isolated_db,
        category="audio",
        in_stock_only=True,
    )
    assert count == 2
    skus = {p.sku for p in products}
    assert skus == {"SONY-WH1000XM5", "ANK-SND-01"}

    # Filter by category name "Footwear"
    products, count = search_catalogue_products(
        isolated_db,
        category="Footwear",
        in_stock_only=True,
    )
    assert count == 1
    assert products[0].sku == "NIKE-AZ-01"

    # Filter by non-existent category
    products, count = search_catalogue_products(
        isolated_db,
        category="NonExistentCategory",
    )
    assert count == 0
    assert products == []


# --- 4. Inventory Filters ---

def test_inventory_filters(isolated_db: Session):
    # in_stock_only=True excludes Adidas (stock=0)
    products, count = search_catalogue_products(
        isolated_db,
        category="footwear",
        in_stock_only=True,
    )
    assert count == 1
    assert products[0].sku == "NIKE-AZ-01"

    # in_stock_only=False includes Adidas (stock=0)
    products, count = search_catalogue_products(
        isolated_db,
        category="footwear",
        in_stock_only=False,
    )
    assert count == 2
    skus = {p.sku for p in products}
    assert skus == {"NIKE-AZ-01", "ADI-UB-01"}

    # min_quantity filter: min_quantity=15 (Anker has 25, Sony has 10, Nike has 5)
    products, count = search_catalogue_products(
        isolated_db,
        in_stock_only=True,
        min_quantity=15,
    )
    assert count == 1
    assert products[0].sku == "ANK-SND-01"


# --- 5. Inactive Products ---

def test_inactive_products_are_never_returned(isolated_db: Session):
    # Discontinued cable has is_active=False
    # Search specifically with keywords that match it
    products, count = search_catalogue_products(
        isolated_db,
        keywords=["cable"],
        in_stock_only=False,
    )
    assert count == 0
    assert products == []

    # Even querying with its exact SKU
    products, count = search_catalogue_products(
        isolated_db,
        keywords=["DISC-CBL-01"],
        in_stock_only=False,
    )
    assert count == 0


# --- 6. Empty Catalogue & No Matches ---

def test_empty_catalogue():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    with session_factory() as empty_session:
        products, count = search_catalogue_products(empty_session)
        assert count == 0
        assert products == []

        agent = ShoppingAgent()
        resp = agent.search(ShoppingSearchRequest(query="headphones"), empty_session)
        assert resp.status == "no_results"
        assert resp.total_count == 0
        assert resp.products == []


def test_no_matching_products(isolated_db: Session):
    agent = ShoppingAgent()
    resp = agent.search(
        ShoppingSearchRequest(query="supercomputer yacht", max_price=Decimal("5.00")),
        isolated_db,
    )
    assert resp.status == "no_results"
    assert resp.total_count == 0
    assert resp.products == []


# --- 7. Pagination ---

def test_pagination(isolated_db: Session):
    # Total active in-stock: Sony ($349.99), Anker ($49.99), Nike ($120) = 3
    # Page 1, size 1, price_asc -> Anker
    p1, c1 = search_catalogue_products(
        isolated_db,
        sort_by=SortOrder.PRICE_ASC,
        limit=1,
        offset=0,
    )
    assert c1 == 3
    assert len(p1) == 1
    assert p1[0].sku == "ANK-SND-01"

    # Page 2, size 1, price_asc -> Nike
    p2, c2 = search_catalogue_products(
        isolated_db,
        sort_by=SortOrder.PRICE_ASC,
        limit=1,
        offset=1,
    )
    assert c2 == 3
    assert len(p2) == 1
    assert p2[0].sku == "NIKE-AZ-01"

    # Offset beyond total
    p3, c3 = search_catalogue_products(
        isolated_db,
        limit=10,
        offset=10,
    )
    assert c3 == 3
    assert len(p3) == 0


# --- 8. Sorting ---

def test_sorting(isolated_db: Session):
    # Price Ascending: Anker ($49.99), Nike ($120.00), Sony ($349.99)
    asc_prods, _ = search_catalogue_products(
        isolated_db,
        sort_by=SortOrder.PRICE_ASC,
    )
    assert [p.sku for p in asc_prods] == ["ANK-SND-01", "NIKE-AZ-01", "SONY-WH1000XM5"]

    # Price Descending: Sony ($349.99), Nike ($120.00), Anker ($49.99)
    desc_prods, _ = search_catalogue_products(
        isolated_db,
        sort_by=SortOrder.PRICE_DESC,
    )
    assert [p.sku for p in desc_prods] == ["SONY-WH1000XM5", "NIKE-AZ-01", "ANK-SND-01"]

    # Name Ascending: Anker..., Nike..., Sony...
    name_asc, _ = search_catalogue_products(
        isolated_db,
        sort_by=SortOrder.NAME_ASC,
    )
    assert [p.sku for p in name_asc] == ["ANK-SND-01", "NIKE-AZ-01", "SONY-WH1000XM5"]


# --- 9. Grounded Explanations ---

def test_grounded_explanations(isolated_db: Session):
    agent = ShoppingAgent()
    intent = ShoppingIntent(max_budget=Decimal("50.00"), in_stock_only=True)
    resp = agent.search(
        ShoppingSearchRequest(category="audio", max_price=Decimal("50.00")),
        isolated_db,
    )
    assert resp.status == "success"
    assert len(resp.products) == 1
    item = resp.products[0]
    assert item.sku == "ANK-SND-01"
    assert item.recommendation_reason is not None
    assert "$49.99" in item.recommendation_reason
    assert "in stock" in item.recommendation_reason
    assert "25 available" in item.recommendation_reason


# --- 10. Supervisor Routing ---

def test_supervisor_routing_deterministic(isolated_db: Session):
    gemini = MockGeminiText("General advice.")
    supervisor = SupervisorAgent(gemini)

    # Shopping query routes to shopping_agent
    dest, conf, _ = supervisor.route("I want to buy running shoes")
    assert dest == "shopping_agent"
    assert conf == 1.0

    # General question routes to general_assistant
    dest, conf, _ = supervisor.route("What should I compare when choosing headphones?")
    assert dest == "general_assistant"
    assert conf == 1.0


def test_supervisor_process_shopping_vs_general(isolated_db: Session):
    gemini = MockGeminiText("General advice on products.")
    supervisor = SupervisorAgent(gemini)

    # Process shopping request
    resp = supervisor.process("Buy wireless headphones", isolated_db)
    assert resp.destination == "shopping_agent"
    assert resp.shopping_response is not None
    assert resp.shopping_response.status == "success"

    # Process general request
    resp_gen = supervisor.process("What is your return policy?", isolated_db)
    assert resp_gen.destination == "general_assistant"
    assert resp_gen.general_answer == "General advice on products."


# --- 11. API Integration Tests (Endpoint /api/v1/shopping/search) ---

def test_api_shopping_search_guest_success(api_client):
    client, fake_client = api_client

    # Seed product in the app's test database
    session = client.app.state.db_session_factory()
    try:
        cat = ProductCategory(id=uuid4(), name="Smartphones", slug="smartphones")
        prod = Product(
            id=uuid4(),
            sku="PHONE-001",
            name="Apex Smartphone 12",
            description="Flagship smartphone with high-res camera.",
            price=Decimal("799.00"),
            is_active=True,
            categories=[cat],
        )
        inv = Inventory(product_id=prod.id, available_quantity=8)
        session.add_all([cat, prod, inv])
        session.commit()
    finally:
        session.close()

    # Guest call without authorization header
    response = client.post(
        "/api/v1/shopping/search",
        json={"category": "smartphones", "max_price": 1000.0},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["total_count"] == 1
    assert data["products"][0]["sku"] == "PHONE-001"
    assert data["products"][0]["price"] == "799.00"
    assert data["products"][0]["in_stock"] is True
    assert data["products"][0]["available_quantity"] == 8


def test_api_shopping_search_authenticated_success(api_client):
    client, _ = api_client

    # Register user to get token
    reg = client.post(
        "/api/v1/auth/register",
        json={
            "email": "shopper@example.com",
            "password": "valid-secure-password-2026",
            "display_name": "Active Shopper",
        },
    )
    assert reg.status_code == 201
    login = client.post(
        "/api/v1/auth/login",
        json={"email": "shopper@example.com", "password": "valid-secure-password-2026"},
    )
    assert login.status_code == 200
    token = login.json()["access_token"]

    response = client.post(
        "/api/v1/shopping/search",
        headers={"Authorization": f"Bearer {token}"},
        json={"limit": 5},
    )
    assert response.status_code == 200
    # Customer private fields are NOT exposed
    assert "password_hash" not in response.text
    assert "shopper@example.com" not in response.text


def test_api_shopping_search_invalid_token_returns_401(api_client):
    client, _ = api_client

    response = client.post(
        "/api/v1/shopping/search",
        headers={"Authorization": "Bearer invalid-garbage-token"},
        json={"category": "audio"},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Authentication is required."


def test_api_shopping_search_invalid_request_returns_422(api_client):
    client, _ = api_client

    # min_price > max_price
    resp1 = client.post(
        "/api/v1/shopping/search",
        json={"min_price": 200.0, "max_price": 50.0},
    )
    assert resp1.status_code == 422

    # Negative price
    resp2 = client.post(
        "/api/v1/shopping/search",
        json={"min_price": -10.0},
    )
    assert resp2.status_code == 422

    # Limit > 100
    resp3 = client.post(
        "/api/v1/shopping/search",
        json={"limit": 500},
    )
    assert resp3.status_code == 422

    # Forbidden unexpected field
    resp4 = client.post(
        "/api/v1/shopping/search",
        json={"unexpected_field": "disallowed"},
    )
    assert resp4.status_code == 422


def test_api_shopping_search_gemini_timeout_returns_504(api_client):
    client, fake_client = api_client
    fake_client.models.outcome = httpx.ReadTimeout("mocked read timeout")

    response = client.post(
        "/api/v1/shopping/search",
        json={"query": "Find running shoes"},
    )
    assert response.status_code == 504
    assert response.json()["detail"] == "The Gemini request timed out."


def test_api_shopping_search_gemini_error_returns_502(api_client):
    client, fake_client = api_client
    fake_client.models.outcome = errors.APIError(500, {"error": "service down"})

    response = client.post(
        "/api/v1/shopping/search",
        json={"query": "Find running shoes"},
    )
    assert response.status_code == 502
    assert response.json()["detail"] == "The Gemini service could not complete the request."


# --- 12. SQL Injection Safety ---

def test_sql_injection_safety(isolated_db: Session):
    # Attempt SQL injection strings in category, brand, keywords
    malicious_strings = [
        "' OR '1'='1",
        "'; DROP TABLE products; --",
        "1; SELECT * FROM users;",
    ]
    for s in malicious_strings:
        products, count = search_catalogue_products(
            isolated_db,
            keywords=[s],
            category=s,
            brand=s,
        )
        assert count == 0
        assert products == []

    # Verify tables still exist
    all_products, total = search_catalogue_products(isolated_db, in_stock_only=False)
    assert total > 0
