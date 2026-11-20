from datetime import datetime, timezone
from decimal import Decimal
import json
from uuid import uuid4

import httpx
import pytest
from google.genai import errors, types
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.app.core.config import Settings
from backend.app.core.constants import (
    CATALOGUE_CURRENCY,
    POLICY_UNAVAILABLE_MESSAGE,
)
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
from backend.app.services.shopping_agent import (
    ShoppingAgent,
    _fallback_extract_intent,
)
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

    # Process general advice request
    resp_gen = supervisor.process("What should I compare when choosing headphones?", isolated_db)
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


def test_api_shopping_search_currency_mismatch_returns_unsupported(api_client):
    client, _ = api_client

    response = client.post(
        "/api/v1/shopping/search",
        json={"currency": "INR", "max_price": 5000.0},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "unsupported_currency"
    assert data["currency"] == "USD"
    assert data["products"] == []
    assert data["total_count"] == 0
    assert "INR" in data["message"]
    assert "USD" in data["message"]


def test_api_shopping_search_min_quantity_support(api_client):
    client, _ = api_client

    # Seed product with quantity 8
    session = client.app.state.db_session_factory()
    try:
        cat = ProductCategory(id=uuid4(), name="Laptops", slug="laptops")
        prod = Product(
            id=uuid4(),
            sku="LAPTOP-01",
            name="Pro Laptop 15",
            description="High performance developer laptop.",
            price=Decimal("1299.00"),
            is_active=True,
            categories=[cat],
        )
        inv = Inventory(product_id=prod.id, available_quantity=8)
        session.add_all([cat, prod, inv])
        session.commit()
    finally:
        session.close()

    # min_quantity=5 -> matches (qty is 8)
    resp1 = client.post(
        "/api/v1/shopping/search",
        json={"category": "laptops", "min_quantity": 5},
    )
    assert resp1.status_code == 200
    assert resp1.json()["total_count"] == 1

    # min_quantity=10 -> 0 matches (qty is 8)
    resp2 = client.post(
        "/api/v1/shopping/search",
        json={"category": "laptops", "min_quantity": 10},
    )
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "no_results"
    assert resp2.json()["total_count"] == 0


def test_api_supervisor_chat_missing_policy_information(api_client):
    client, _ = api_client

    response = client.post(
        "/api/v1/shopping/chat",
        json={"question": "What is your return policy?"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["destination"] == "general_assistant"
    assert data["general_answer"] == POLICY_UNAVAILABLE_MESSAGE
    assert "not currently available" in data["general_answer"]



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


# --- 13. Currency Handling Tests ---

def test_currency_mismatch_returns_unsupported_currency_response(isolated_db: Session):
    agent = ShoppingAgent()

    # Mismatch via explicit request currency
    req1 = ShoppingSearchRequest(category="audio", max_price=Decimal("5000"), currency="INR")
    resp1 = agent.search(req1, isolated_db)
    assert resp1.status == "unsupported_currency"
    assert resp1.total_count == 0
    assert resp1.products == []
    assert "INR" in resp1.message
    assert CATALOGUE_CURRENCY in resp1.message
    assert "not supported" in resp1.message

    # Mismatch via natural language query with EUR
    gemini_eur = MockGeminiText(json.dumps({
        "keywords": ["headphones"],
        "max_budget": 50.0,
        "currency": "EUR",
        "is_shopping_intent": True,
        "is_ambiguous_or_insufficient": False,
    }))
    agent_eur = ShoppingAgent(gemini_eur)
    resp2 = agent_eur.search(ShoppingSearchRequest(query="headphones under 50 euros"), isolated_db)
    assert resp2.status == "unsupported_currency"
    assert resp2.total_count == 0
    assert "EUR" in resp2.message

    # Mismatch via fallback extraction of rupees
    agent_fallback = ShoppingAgent()
    resp3 = agent_fallback.search(ShoppingSearchRequest(query="headphones under 3000 rupees"), isolated_db)
    assert resp3.status == "unsupported_currency"
    assert "INR" in resp3.message


def test_supported_currency_searches_catalogue(isolated_db: Session):
    agent = ShoppingAgent()

    # Supported currency "USD"
    req_usd = ShoppingSearchRequest(category="audio", max_price=Decimal("100.00"), currency="USD")
    resp_usd = agent.search(req_usd, isolated_db)
    assert resp_usd.status == "success"
    assert resp_usd.currency == "USD"
    assert len(resp_usd.products) == 1
    assert resp_usd.products[0].sku == "ANK-SND-01"

    # Supported currency "$"
    req_symbol = ShoppingSearchRequest(category="audio", max_price=Decimal("100.00"), currency="$")
    resp_symbol = agent.search(req_symbol, isolated_db)
    assert resp_symbol.status == "success"
    assert resp_symbol.currency == "USD"
    assert len(resp_symbol.products) == 1


# --- 14. Stock Preference Precedence Tests ---

def test_explicit_stock_preference_overrides_gemini(isolated_db: Session):
    # Gemini extracted in_stock_only=True, but request explicitly has in_stock_only=False
    gemini = MockGeminiText(json.dumps({
        "keywords": ["running", "shoes"],
        "category": "footwear",
        "in_stock_only": True,
        "is_shopping_intent": True,
        "is_ambiguous_or_insufficient": False,
    }))
    agent = ShoppingAgent(gemini)

    req = ShoppingSearchRequest(query="Find running shoes", in_stock_only=False)
    resp = agent.search(req, isolated_db)

    # Out-of-stock Adidas ($180, qty=0) must be included because explicit request wins
    assert resp.status == "success"
    assert resp.total_count == 2
    skus = {p.sku for p in resp.products}
    assert "ADI-UB-01" in skus
    assert "NIKE-AZ-01" in skus


def test_gemini_stock_preference_used_when_request_absent(isolated_db: Session):
    # Request has in_stock_only=None, Gemini extracted in_stock_only=False
    gemini = MockGeminiText(json.dumps({
        "keywords": ["running", "shoes"],
        "category": "footwear",
        "in_stock_only": False,
        "is_shopping_intent": True,
        "is_ambiguous_or_insufficient": False,
    }))
    agent = ShoppingAgent(gemini)

    req = ShoppingSearchRequest(query="Find running shoes including out of stock")
    assert req.in_stock_only is None

    resp = agent.search(req, isolated_db)
    # Out-of-stock Adidas included because extracted preference is False
    assert resp.status == "success"
    assert resp.total_count == 2
    skus = {p.sku for p in resp.products}
    assert "ADI-UB-01" in skus


def test_default_stock_preference_when_neither_supplied(isolated_db: Session):
    # Request has in_stock_only=None, Gemini has in_stock_only=None
    gemini = MockGeminiText(json.dumps({
        "keywords": ["running", "shoes"],
        "category": "footwear",
        "in_stock_only": None,
        "is_shopping_intent": True,
        "is_ambiguous_or_insufficient": False,
    }))
    agent = ShoppingAgent(gemini)

    req = ShoppingSearchRequest(query="running shoes", category="footwear")
    assert req.in_stock_only is None

    resp = agent.search(req, isolated_db)
    # Default is True, so out-of-stock Adidas is excluded
    assert resp.status == "success"
    assert resp.total_count == 1
    assert resp.products[0].sku == "NIKE-AZ-01"


# --- 15. Sorting Preference Precedence Tests ---

def test_explicit_sorting_overrides_gemini(isolated_db: Session):
    # Gemini extracted sort_by=price_desc, but request explicitly specifies price_asc
    gemini = MockGeminiText(json.dumps({
        "keywords": [],
        "category": "footwear",
        "sort_by": "price_desc",
        "is_shopping_intent": True,
        "is_ambiguous_or_insufficient": False,
    }))
    agent = ShoppingAgent(gemini)

    req = ShoppingSearchRequest(
        query="running shoes",
        category="footwear",
        in_stock_only=False,
        sort_by=SortOrder.PRICE_ASC,
    )
    resp = agent.search(req, isolated_db)

    # Explicit PRICE_ASC wins: Nike ($120) before Adidas ($180)
    assert resp.status == "success"
    assert resp.total_count == 2
    assert resp.products[0].sku == "NIKE-AZ-01"
    assert resp.products[1].sku == "ADI-UB-01"


def test_gemini_sorting_used_when_request_absent(isolated_db: Session):
    # Request has sort_by=None, Gemini extracted sort_by=price_desc
    gemini = MockGeminiText(json.dumps({
        "keywords": [],
        "category": "footwear",
        "sort_by": "price_desc",
        "is_shopping_intent": True,
        "is_ambiguous_or_insufficient": False,
    }))
    agent = ShoppingAgent(gemini)

    req = ShoppingSearchRequest(
        query="running shoes expensive first",
        category="footwear",
        in_stock_only=False,
    )
    assert req.sort_by is None

    resp = agent.search(req, isolated_db)
    # Extracted PRICE_DESC wins: Adidas ($180) before Nike ($120)
    assert resp.status == "success"
    assert resp.total_count == 2
    assert resp.products[0].sku == "ADI-UB-01"
    assert resp.products[1].sku == "NIKE-AZ-01"


def test_default_sorting_when_neither_supplied(isolated_db: Session):
    # Neither supplied -> defaults to price ascending
    gemini = MockGeminiText(json.dumps({
        "keywords": [],
        "category": "footwear",
        "sort_by": None,
        "is_shopping_intent": True,
        "is_ambiguous_or_insufficient": False,
    }))
    agent = ShoppingAgent(gemini)

    req = ShoppingSearchRequest(query="running shoes", category="footwear", in_stock_only=False)
    assert req.sort_by is None

    resp = agent.search(req, isolated_db)
    # Default PRICE_ASC: Nike ($120) before Adidas ($180)
    assert resp.products[0].sku == "NIKE-AZ-01"
    assert resp.products[1].sku == "ADI-UB-01"


# --- 16. Minimum Quantity Tests (1, 5, 15) ---

def test_minimum_inventory_quantity_1_5_15(isolated_db: Session):
    agent = ShoppingAgent()

    # Active in stock items:
    # Anker: 25 available
    # Sony: 10 available
    # Nike: 5 available
    # Adidas: 0 available

    # min_quantity=1 -> Sony, Anker, Nike (total 3)
    resp1 = agent.search(ShoppingSearchRequest(min_quantity=1), isolated_db)
    assert resp1.total_count == 3
    assert {p.sku for p in resp1.products} == {"SONY-WH1000XM5", "ANK-SND-01", "NIKE-AZ-01"}

    # min_quantity=5 -> Sony (10), Anker (25), Nike (5) (total 3)
    resp5 = agent.search(ShoppingSearchRequest(min_quantity=5), isolated_db)
    assert resp5.total_count == 3
    assert {p.sku for p in resp5.products} == {"SONY-WH1000XM5", "ANK-SND-01", "NIKE-AZ-01"}

    # min_quantity=15 -> only Anker (25) (total 1; Sony and Nike excluded)
    resp15 = agent.search(ShoppingSearchRequest(min_quantity=15), isolated_db)
    assert resp15.total_count == 1
    assert resp15.products[0].sku == "ANK-SND-01"
    assert resp15.products[0].available_quantity == 25


# --- 17. Missing Policy Information Tests ---

@pytest.mark.parametrize(
    "question",
    [
        "What is your return policy?",
        "Can I get a refund on my purchase?",
        "How many days do I have to return an item?",
        "What is your shipping policy?",
        "Do you offer a warranty on products?",
    ],
)
def test_missing_policy_information_returns_unavailable_message(isolated_db: Session, question: str):
    gemini = MockGeminiText("Invented 30-day return policy with free shipping.")
    supervisor = SupervisorAgent(gemini)

    resp = supervisor.process(question, isolated_db)

    assert resp.destination == "general_assistant"
    assert resp.general_answer is not None
    # Must return verified unavailable message, NOT invented policy periods
    assert resp.general_answer == POLICY_UNAVAILABLE_MESSAGE
    assert "not currently available" in resp.general_answer
    assert "30-day" not in resp.general_answer
    assert "free shipping" not in resp.general_answer


# --- 18. Catalogue Currency Source of Truth & Grouped Number Tests ---

@pytest.mark.parametrize("valid_code", ["USD", "usd", "INR", "inr", "EUR", "eur", "GBP", "JPY", "CAD", "AUD"])
def test_settings_catalogue_currency_valid(valid_code: str):
    s = Settings(
        gemini_api_key="test-key",
        database_url="postgresql+psycopg://user:pass@localhost:5432/test",
        auth_secret_key="test-secret-at-least-32-characters-long",
        catalogue_currency=valid_code,
    )
    assert s.catalogue_currency == valid_code.strip().upper()


@pytest.mark.parametrize("invalid_code", ["US", "123", "INVALID", "TOOLONG", "", "   ", "XYZ", "usd1"])
def test_settings_catalogue_currency_invalid(invalid_code: str):
    with pytest.raises(ValidationError):
        Settings(
            gemini_api_key="test-key",
            database_url="postgresql+psycopg://user:pass@localhost:5432/test",
            auth_secret_key="test-secret-at-least-32-characters-long",
            catalogue_currency=invalid_code,
        )


def test_fallback_extract_intent_grouped_numbers():
    # $1,000 -> max_budget 1000, keywords ['laptop']
    intent1 = _fallback_extract_intent("laptop under $1,000")
    assert intent1.max_budget == Decimal("1000")
    assert intent1.currency == "USD"
    assert "laptop" in intent1.keywords
    assert "000" not in intent1.keywords

    # ₹70,000 -> max_budget 70000, keywords ['phone']
    intent2 = _fallback_extract_intent("phone under ₹70,000")
    assert intent2.max_budget == Decimal("70000")
    assert intent2.currency == "INR"
    assert "phone" in intent2.keywords
    assert "000" not in intent2.keywords

    # ₹1,00,000 -> max_budget 100000
    intent3 = _fallback_extract_intent("laptop under ₹1,00,000")
    assert intent3.max_budget == Decimal("100000")
    assert intent3.currency == "INR"

    # between $1,200 and $3,500
    intent4 = _fallback_extract_intent("camera between $1,200 and $3,500")
    assert intent4.min_budget == Decimal("1200")
    assert intent4.max_budget == Decimal("3500")
    assert intent4.currency == "USD"


def test_inr_catalogue_configuration(isolated_db: Session):
    agent_inr = ShoppingAgent(catalogue_currency="INR")

    # Search with INR budget -> Accepted, products returned with currency INR
    resp = agent_inr.search(
        ShoppingSearchRequest(category="audio", max_price=Decimal("100.00"), currency="INR"),
        isolated_db,
    )
    assert resp.status == "success"
    assert resp.currency == "INR"
    assert len(resp.products) == 1
    assert resp.products[0].sku == "ANK-SND-01"
    assert resp.products[0].currency == "INR"
    assert "₹49.99" in resp.products[0].recommendation_reason
    assert "₹100.00" in resp.products[0].recommendation_reason

    # Query with natural language ₹70,000
    resp_nl = agent_inr.search(ShoppingSearchRequest(query="earbuds under ₹70,000"), isolated_db)
    assert resp_nl.status == "success"
    assert resp_nl.currency == "INR"
    assert any(p.sku == "ANK-SND-01" for p in resp_nl.products)

    # Search with USD budget on INR catalogue -> Rejected as unsupported_currency without numerical comparison
    resp_usd = agent_inr.search(
        ShoppingSearchRequest(category="audio", max_price=Decimal("100.00"), currency="USD"),
        isolated_db,
    )
    assert resp_usd.status == "unsupported_currency"
    assert resp_usd.total_count == 0
    assert resp_usd.currency == "INR"
    assert "INR" in resp_usd.message
    assert "USD" in resp_usd.message


def test_usd_catalogue_rejects_inr_without_conversion(isolated_db: Session):
    agent_usd = ShoppingAgent(catalogue_currency="USD")

    # Grouped number $1,000 succeeds
    resp_grouped = agent_usd.search(ShoppingSearchRequest(query="headphones under $1,000"), isolated_db)
    assert resp_grouped.status == "success"
    assert resp_grouped.currency == "USD"

    # INR request on USD catalogue is rejected without numerical comparison
    resp_inr = agent_usd.search(
        ShoppingSearchRequest(category="audio", max_price=Decimal("5000.00"), currency="INR"),
        isolated_db,
    )
    assert resp_inr.status == "unsupported_currency"
    assert resp_inr.total_count == 0
    assert resp_inr.currency == "USD"
    assert "USD" in resp_inr.message


def test_api_shopping_search_with_configured_currency(api_client):
    client, _ = api_client
    # Switch app state settings catalogue_currency to INR
    original_currency = client.app.state.settings.catalogue_currency
    try:
        client.app.state.settings.catalogue_currency = "INR"
        response = client.post(
            "/api/v1/shopping/search",
            json={"category": "audio", "max_price": 100.0, "currency": "INR"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["currency"] == "INR"
    finally:
        client.app.state.settings.catalogue_currency = original_currency
