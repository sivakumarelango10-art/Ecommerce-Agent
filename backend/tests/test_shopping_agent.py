from datetime import datetime, timezone
from decimal import Decimal
import json
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from google.genai import errors, types
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from backend.app.core.config import Settings
from backend.app.core.constants import (
    CATALOGUE_CURRENCY,
    CATALOGUE_UNAVAILABLE_MESSAGE,
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
    GeminiServiceError,
    GeminiTimeoutError,
)
from backend.app.services.product_discovery_service import (
    get_term_search_variants,
    search_catalogue_products,
)
from backend.app.services.shopping_agent import (
    ShoppingAgent,
    _fallback_extract_intent,
    normalize_search_keywords,
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
    assert resp.currency == "INR"
    assert len(resp.products) == 1
    item = resp.products[0]
    assert item.sku == "ANK-SND-01"
    assert item.currency == "INR"
    assert item.recommendation_reason is not None
    assert "₹49.99" in item.recommendation_reason
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


def test_supervisor_routing_order_support_deterministic():
    gemini = MockGeminiText("General advice.")
    supervisor = SupervisorAgent(gemini)

    # Order support inquiries
    for query in [
        "Where is my order?",
        "Show my recent orders",
        "What did I purchase?",
        "Cancel my order",
        "Status of order ORD-20261122-1A2B3C4D",
        "Track my order",
        "What have I ordered?",
    ]:
        dest, conf, _ = supervisor.route(query)
        assert dest == "order_support", f"Expected '{query}' to route to order_support, got {dest}"
        assert conf == 1.0

    # Shopping inquiries that use the word "order" or "find"
    for shop_query in [
        "I want to order a laptop",
        "Find me a laptop",
        "Can I purchase headphones under ₹2000",
    ]:
        dest, conf, _ = supervisor.route(shop_query)
        assert dest == "shopping_agent", f"Expected '{shop_query}' to route to shopping_agent, got {dest}"


def test_supervisor_routing_gemini_proposal():
    # Mock Gemini returning handoff_to_order_support
    mock_order = SimpleNamespace(
        text=None,
        function_calls=[
            types.FunctionCall(name="handoff_to_order_support", args={"reason": "Customer inquiry regarding package."})
        ],
    )
    supervisor = SupervisorAgent(SimpleNamespace(generate=lambda *a, **kw: mock_order, answer=lambda *a: ""))
    dest, conf, reason = supervisor.route("Package inquiry")
    assert dest == "order_support"
    assert conf == 0.9
    assert "package" in reason.lower()


def test_supervisor_process_order_support_unauthenticated(isolated_db: Session):
    gemini = MockGeminiText("Unused.")
    supervisor = SupervisorAgent(gemini)

    resp = supervisor.process("Where is my order?", isolated_db, current_user_id=None)
    assert resp.destination == "order_support"
    assert resp.order_answer is not None
    assert "sign in" in resp.order_answer.lower()



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


def test_api_shopping_search_gemini_timeout_falls_back_to_deterministic_search(api_client):
    client, fake_client = api_client
    fake_client.models.outcome = httpx.ReadTimeout("mocked read timeout")

    response = client.post(
        "/api/v1/shopping/search",
        json={"query": "Find running shoes"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] in {"success", "no_results"}
    assert data["currency"] == "INR"


def test_api_shopping_search_gemini_error_falls_back_to_deterministic_search(api_client):
    client, fake_client = api_client
    fake_client.models.outcome = errors.APIError(500, {"error": "service down"})

    response = client.post(
        "/api/v1/shopping/search",
        json={"query": "Find running shoes"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] in {"success", "no_results"}
    assert data["currency"] == "INR"


def test_api_shopping_search_currency_mismatch_returns_unsupported(api_client):
    client, _ = api_client

    response = client.post(
        "/api/v1/shopping/search",
        json={"currency": "USD", "max_price": 5000.0},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "unsupported_currency"
    assert data["currency"] == "INR"
    assert data["products"] == []
    assert data["total_count"] == 0
    assert "USD" in data["message"]
    assert "INR" in data["message"]


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

    # Mismatch via explicit request currency USD
    req1 = ShoppingSearchRequest(category="audio", max_price=Decimal("5000"), currency="USD")
    resp1 = agent.search(req1, isolated_db)
    assert resp1.status == "unsupported_currency"
    assert resp1.total_count == 0
    assert resp1.products == []
    assert resp1.currency == "INR"
    assert "USD" in resp1.message
    assert "INR" in resp1.message
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
    assert resp2.currency == "INR"
    assert "EUR" in resp2.message
    assert "INR" in resp2.message

    # Mismatch via fallback extraction of dollars
    agent_fallback = ShoppingAgent()
    resp3 = agent_fallback.search(ShoppingSearchRequest(query="headphones under 100 dollars"), isolated_db)
    assert resp3.status == "unsupported_currency"
    assert resp3.currency == "INR"
    assert "USD" in resp3.message
    assert "INR" in resp3.message


def test_supported_currency_searches_catalogue(isolated_db: Session):
    agent = ShoppingAgent()

    # Supported currency "INR"
    req_inr = ShoppingSearchRequest(category="audio", max_price=Decimal("100.00"), currency="INR")
    resp_inr = agent.search(req_inr, isolated_db)
    assert resp_inr.status == "success"
    assert resp_inr.currency == "INR"
    assert len(resp_inr.products) == 1
    assert resp_inr.products[0].sku == "ANK-SND-01"

    # Supported currency "₹"
    req_symbol = ShoppingSearchRequest(category="audio", max_price=Decimal("100.00"), currency="₹")
    resp_symbol = agent.search(req_symbol, isolated_db)
    assert resp_symbol.status == "success"
    assert resp_symbol.currency == "INR"
    assert len(resp_symbol.products) == 1

    # Supported currency "RS"
    req_rs = ShoppingSearchRequest(category="audio", max_price=Decimal("100.00"), currency="RS")
    resp_rs = agent.search(req_rs, isolated_db)
    assert resp_rs.status == "success"
    assert resp_rs.currency == "INR"

    # Supported currency "Rupees"
    req_rupees = ShoppingSearchRequest(category="audio", max_price=Decimal("100.00"), currency="Rupees")
    resp_rupees = agent.search(req_rupees, isolated_db)
    assert resp_rupees.status == "success"
    assert resp_rupees.currency == "INR"


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

def test_settings_catalogue_currency_default_is_inr():
    s = Settings(
        gemini_api_key="test-key",
        database_url="postgresql+psycopg://user:pass@localhost:5432/test",
        auth_secret_key="test-secret-at-least-32-characters-long",
    )
    assert s.catalogue_currency == "INR"


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


def test_ambiguous_query_with_currency_mismatch_returns_unsupported(isolated_db: Session):
    agent = ShoppingAgent()
    # Query is ambiguous ("buy something") but explicitly requests USD / dollars
    resp = agent.search(ShoppingSearchRequest(query="buy something in dollars"), isolated_db)
    assert resp.status == "unsupported_currency"
    assert resp.currency == "INR"
    assert "USD" in resp.message
    assert "INR" in resp.message

    # Explicit currency USD with ambiguous query
    resp2 = agent.search(ShoppingSearchRequest(query="buy something", currency="USD"), isolated_db)
    assert resp2.status == "unsupported_currency"
    assert resp2.currency == "INR"

    # Query with ambiguous words but 50 euros
    resp3 = agent.search(ShoppingSearchRequest(query="shop something with 50 euros"), isolated_db)
    assert resp3.status == "unsupported_currency"
    assert resp3.currency == "INR"
    assert "EUR" in resp3.message


def test_fallback_extract_intent_grouped_numbers():
    # ₹70,000 -> max_budget 70000, keywords ['phone']
    intent1 = _fallback_extract_intent("phone under ₹70,000")
    assert intent1.max_budget == Decimal("70000")
    assert intent1.currency == "INR"
    assert "phone" in intent1.keywords
    assert "000" not in intent1.keywords

    # ₹1,00,000 -> max_budget 100000
    intent2 = _fallback_extract_intent("laptop under ₹1,00,000")
    assert intent2.max_budget == Decimal("100000")
    assert intent2.currency == "INR"
    assert "laptop" in intent2.keywords
    assert "000" not in intent2.keywords

    # ₹12,50,000 -> max_budget 1250000
    intent3 = _fallback_extract_intent("car under ₹12,50,000")
    assert intent3.max_budget == Decimal("1250000")
    assert intent3.currency == "INR"
    assert "car" in intent3.keywords

    # Rs. 70,000
    intent4 = _fallback_extract_intent("laptop under Rs. 70,000")
    assert intent4.max_budget == Decimal("70000")
    assert intent4.currency == "INR"

    # Rs 1,00,000
    intent5 = _fallback_extract_intent("laptop under Rs 1,00,000")
    assert intent5.max_budget == Decimal("100000")
    assert intent5.currency == "INR"

    # 12,50,000 rupees
    intent6 = _fallback_extract_intent("luxury watch under 12,50,000 rupees")
    assert intent6.max_budget == Decimal("1250000")
    assert intent6.currency == "INR"
    assert "watch" in intent6.keywords

    # $1,000 -> max_budget 1000, currency USD
    intent7 = _fallback_extract_intent("laptop under $1,000")
    assert intent7.max_budget == Decimal("1000")
    assert intent7.currency == "USD"

    # between $1,200 and $3,500
    intent8 = _fallback_extract_intent("camera between $1,200 and $3,500")
    assert intent8.min_budget == Decimal("1200")
    assert intent8.max_budget == Decimal("3500")
    assert intent8.currency == "USD"


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

    # Grouped number $1,000 succeeds on USD deployment
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
    # Seed audio product in client database
    session = client.app.state.db_session_factory()
    try:
        cat = ProductCategory(id=uuid4(), name="Audio", slug="audio")
        prod = Product(
            id=uuid4(),
            sku="EARBUD-01",
            name="Wireless Earbuds",
            description="Clear audio earbuds.",
            price=Decimal("49.99"),
            is_active=True,
            categories=[cat],
        )
        inv = Inventory(product_id=prod.id, available_quantity=10)
        session.add_all([cat, prod, inv])
        session.commit()
    finally:
        session.close()

    # Verify default is INR
    response = client.post(
        "/api/v1/shopping/search",
        json={"category": "audio", "max_price": 100.0, "currency": "INR"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["currency"] == "INR"
    assert data["products"][0]["currency"] == "INR"

    # Switch app state settings catalogue_currency to USD for alternative deployment test
    original_currency = client.app.state.settings.catalogue_currency
    try:
        client.app.state.settings.catalogue_currency = "USD"
        response_usd = client.post(
            "/api/v1/shopping/search",
            json={"category": "audio", "max_price": 100.0, "currency": "USD"},
        )
        assert response_usd.status_code == 200
        data_usd = response_usd.json()
        assert data_usd["status"] == "success"
        assert data_usd["currency"] == "USD"
        assert data_usd["products"][0]["currency"] == "USD"
    finally:
        client.app.state.settings.catalogue_currency = original_currency


def test_api_supervisor_chat_uses_configured_inr(api_client):
    client, fake_client = api_client
    # Seed audio product in client database
    session = client.app.state.db_session_factory()
    try:
        cat = ProductCategory(id=uuid4(), name="Earbuds", slug="earbuds")
        prod = Product(
            id=uuid4(),
            sku="EARBUD-02",
            name="Earbuds Pro",
            description="Pro noise cancelling earbuds.",
            price=Decimal("89.99"),
            is_active=True,
            categories=[cat],
        )
        inv = Inventory(product_id=prod.id, available_quantity=5)
        session.add_all([cat, prod, inv])
        session.commit()
    finally:
        session.close()

    response = client.post(
        "/api/v1/shopping/chat",
        json={"question": "Find earbuds under ₹100"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["destination"] == "shopping_agent"
    assert data["shopping_response"] is not None
    assert data["shopping_response"]["currency"] == "INR"
    assert data["shopping_response"]["status"] == "success"
    assert len(data["shopping_response"]["products"]) >= 1
    assert data["shopping_response"]["products"][0]["currency"] == "INR"


def test_agent_entry_point_uses_inr_symbol():
    from backend.app.core.constants import get_currency_symbol
    assert get_currency_symbol("INR") == "₹"
    assert get_currency_symbol("USD") == "$"


# --- 19. Search Request and Intent Input Bounding Tests ---

def test_shopping_search_request_keyword_bounds():
    # 1. More than 10 keywords -> raises ValidationError
    with pytest.raises(ValidationError):
        ShoppingSearchRequest(keywords=[f"term{i}" for i in range(11)])

    # 2. Keyword longer than 80 characters -> raises ValidationError
    with pytest.raises(ValidationError):
        ShoppingSearchRequest(keywords=["a" * 81])

    # 3. Blank or whitespace-only keyword -> raises ValidationError
    with pytest.raises(ValidationError):
        ShoppingSearchRequest(keywords=["valid", ""])
    with pytest.raises(ValidationError):
        ShoppingSearchRequest(keywords=["   "])

    # 4. Valid limit case: exactly 10 keywords, each exactly 80 chars -> succeeds
    ten_eighty_char_keywords = ["x" * 80 for _ in range(10)]
    req = ShoppingSearchRequest(keywords=ten_eighty_char_keywords)
    assert len(req.keywords) == 10
    assert all(len(k) == 80 for k in req.keywords)


def test_shopping_intent_bounds():
    # More than 10 keywords and features -> gracefully bounded to 10 items of max 80 chars
    intent = ShoppingIntent(
        keywords=[f"term{i}" * 20 for i in range(15)],
        required_features=["feature" for _ in range(15)],
    )
    assert len(intent.keywords) == 10
    assert all(len(k) <= 80 for k in intent.keywords)
    assert len(intent.required_features) == 10


def test_api_shopping_search_keyword_validation(api_client):
    client, _ = api_client

    # > 10 keywords -> 422
    resp1 = client.post(
        "/api/v1/shopping/search",
        json={"keywords": [f"item{i}" for i in range(11)]},
    )
    assert resp1.status_code == 422

    # > 80 chars keyword -> 422
    resp2 = client.post(
        "/api/v1/shopping/search",
        json={"keywords": ["a" * 81]},
    )
    assert resp2.status_code == 422

    # Blank keyword -> 422
    resp3 = client.post(
        "/api/v1/shopping/search",
        json={"keywords": ["valid", "   "]},
    )
    assert resp3.status_code == 422

    # Valid 10 keywords -> 200
    resp4 = client.post(
        "/api/v1/shopping/search",
        json={"keywords": [f"term{i}" for i in range(10)]},
    )
    assert resp4.status_code == 200


# --- 20. Database Decoupling from /chat Endpoint Tests ---

def test_api_supervisor_chat_general_question_without_database(api_client):
    """General informational questions succeed even if the database is completely unavailable."""
    client, fake_client = api_client
    fake_client.models.outcome = "Here is advice on choosing between products."

    # Simulate database engine/session_factory completely unavailable
    orig_factory = client.app.state.db_session_factory
    try:
        client.app.state.db_session_factory = None

        response = client.post(
            "/api/v1/shopping/chat",
            json={"question": "What should I compare when choosing headphones?"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["destination"] == "general_assistant"
        assert data["general_answer"] == "Here is advice on choosing between products."
        assert data["shopping_response"] is None
    finally:
        client.app.state.db_session_factory = orig_factory


def test_api_supervisor_chat_policy_question_without_database(api_client):
    """Policy questions return verified policy message without requiring a database session."""
    client, _ = api_client

    orig_factory = client.app.state.db_session_factory
    try:
        client.app.state.db_session_factory = None

        response = client.post(
            "/api/v1/shopping/chat",
            json={"question": "What is your return policy?"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["destination"] == "general_assistant"
        assert data["general_answer"] == POLICY_UNAVAILABLE_MESSAGE
        assert data["shopping_response"] is None
    finally:
        client.app.state.db_session_factory = orig_factory


def test_api_supervisor_chat_shopping_question_when_db_unavailable_returns_safe_message(api_client):
    """Shopping queries return safe CATALOGUE_UNAVAILABLE_MESSAGE if db_session_factory is None."""
    client, _ = api_client

    orig_factory = client.app.state.db_session_factory
    try:
        client.app.state.db_session_factory = None

        response = client.post(
            "/api/v1/shopping/chat",
            json={"question": "Find running shoes under ₹2000"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["destination"] == "shopping_agent"
        assert data["shopping_response"] is not None
        assert data["shopping_response"]["status"] == "no_results"
        assert data["shopping_response"]["message"] == CATALOGUE_UNAVAILABLE_MESSAGE
        assert data["shopping_response"]["products"] == []
        assert data["general_answer"] is None
    finally:
        client.app.state.db_session_factory = orig_factory


def test_api_supervisor_chat_shopping_question_db_error_returns_safe_message(api_client):
    """Shopping queries return safe CATALOGUE_UNAVAILABLE_MESSAGE if DB raises SQLAlchemyError."""
    from unittest.mock import MagicMock
    from sqlalchemy.exc import OperationalError

    client, _ = api_client

    failing_session = MagicMock()
    failing_session.__enter__.return_value = failing_session
    failing_session.__exit__.return_value = False
    failing_session.scalars.side_effect = OperationalError("connection refused", None, Exception("db down"))
    failing_session.scalar.side_effect = OperationalError("connection refused", None, Exception("db down"))

    failing_factory = MagicMock(return_value=failing_session)

    orig_factory = client.app.state.db_session_factory
    try:
        client.app.state.db_session_factory = failing_factory

        response = client.post(
            "/api/v1/shopping/chat",
            json={"question": "Find running shoes under ₹2000"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["destination"] == "shopping_agent"
        assert data["shopping_response"] is not None
        assert data["shopping_response"]["status"] == "no_results"
        assert data["shopping_response"]["message"] == CATALOGUE_UNAVAILABLE_MESSAGE
        assert data["shopping_response"]["products"] == []
        assert data["general_answer"] is None
    finally:
        client.app.state.db_session_factory = orig_factory



# --- 13. Deterministic Fallback on Gemini Service & Timeout Errors ---

def test_intent_extraction_gemini_503_service_error_triggers_fallback():
    """Gemini HTTP 503 / GeminiProviderError triggers deterministic intent fallback."""
    gemini = MockGeminiText(GeminiProviderError("503 Service Unavailable: High load"))
    agent = ShoppingAgent(gemini, catalogue_currency="INR")

    intent = agent.extract_intent("Find wireless headphones under ₹3000")

    assert intent.is_shopping_intent is True
    assert intent.is_ambiguous_or_insufficient is False
    assert intent.max_budget == Decimal("3000")
    assert intent.currency == "INR"
    assert "wireless" in intent.keywords
    assert "headphones" in intent.keywords


def test_intent_extraction_gemini_timeout_triggers_fallback():
    """Gemini timeout triggers deterministic intent fallback."""
    gemini = MockGeminiText(GeminiTimeoutError("The Gemini request timed out after 30000ms"))
    agent = ShoppingAgent(gemini, catalogue_currency="INR")

    intent = agent.extract_intent("Find wireless headphones under ₹3000")

    assert intent.is_shopping_intent is True
    assert intent.is_ambiguous_or_insufficient is False
    assert intent.max_budget == Decimal("3000")
    assert intent.currency == "INR"
    assert "wireless" in intent.keywords
    assert "headphones" in intent.keywords


def test_intent_extraction_gemini_service_error_triggers_fallback():
    """Generic GeminiServiceError triggers deterministic intent fallback."""
    gemini = MockGeminiText(GeminiServiceError("Upstream Gemini transport failure"))
    agent = ShoppingAgent(gemini, catalogue_currency="INR")

    intent = agent.extract_intent("laptop under ₹70,000")

    assert intent.is_shopping_intent is True
    assert intent.is_ambiguous_or_insufficient is False
    assert intent.max_budget == Decimal("70000")
    assert "laptop" in intent.keywords


def test_shopping_search_with_gemini_service_error_reaches_catalogue_search(isolated_db: Session):
    """When Gemini raises GeminiServiceError, search uses fallback intent and successfully queries PostgreSQL."""
    cat_audio = isolated_db.scalar(select(ProductCategory).where(ProductCategory.slug == "audio"))
    prod = Product(
        id=uuid4(),
        sku="BOAT-RCKZ-255",
        name="boAt Rockerz 255 Wireless Earphones",
        description="Bluetooth in-ear neckband wireless earphones with deep bass.",
        price=Decimal("1299.00"),
        is_active=True,
        categories=[cat_audio] if cat_audio else [],
    )
    inv = Inventory(product_id=prod.id, available_quantity=15)
    isolated_db.add_all([prod, inv])
    isolated_db.commit()

    gemini = MockGeminiText(GeminiProviderError("503 Service Unavailable"))
    agent = ShoppingAgent(gemini, catalogue_currency="INR")

    req = ShoppingSearchRequest(query="Find wireless earphones under ₹2000")
    response = agent.search(req, isolated_db)

    assert response.status == "success"
    assert response.total_count >= 1
    found = next((p for p in response.products if p.sku == "BOAT-RCKZ-255"), None)
    assert found is not None
    assert found.price == Decimal("1299.00")
    assert found.in_stock is True
    assert found.recommendation_reason != ""
    assert "₹1299.00 is within your budget of ₹2000.00" in found.recommendation_reason


def test_supervisor_chat_with_gemini_intent_extraction_error_returns_catalogue_products(isolated_db: Session):
    """Supervisor chat delivers real catalogue products via fallback when Gemini intent extraction fails."""
    cat_audio = isolated_db.scalar(select(ProductCategory).where(ProductCategory.slug == "audio"))
    prod = Product(
        id=uuid4(),
        sku="NOISE-TW-100",
        name="Noise Buds Wireless Earbuds",
        description="True wireless earbuds with active noise cancellation.",
        price=Decimal("1899.00"),
        is_active=True,
        categories=[cat_audio] if cat_audio else [],
    )
    inv = Inventory(product_id=prod.id, available_quantity=10)
    isolated_db.add_all([prod, inv])
    isolated_db.commit()

    class FailingIntentGemini:
        def answer(self, prompt: str) -> str:
            raise GeminiProviderError("503 Service Unavailable from model")

    gemini = FailingIntentGemini()
    supervisor = SupervisorAgent(gemini, catalogue_currency="INR")

    result = supervisor.process("Buy wireless earbuds under ₹2500", session=isolated_db)

    assert result.destination == "shopping_agent"
    assert result.shopping_response is not None
    assert result.shopping_response.status == "success"
    assert result.shopping_response.message != CATALOGUE_UNAVAILABLE_MESSAGE
    assert result.shopping_response.total_count >= 1
    found = next((p for p in result.shopping_response.products if p.sku == "NOISE-TW-100"), None)
    assert found is not None


# ---------------------------------------------------------------------------
# 13. Natural-Language Search Keyword Extraction & Catalogue Regression Tests
# ---------------------------------------------------------------------------

@pytest.fixture
def seeded_catalogue_session():
    """Create an isolated in-memory SQLite database populated with the official 26-product catalogue."""
    from backend.scripts.seed_catalogue import seed_catalogue

    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        seed_catalogue(session, dry_run=False)
        yield session
    engine.dispose()


def test_natural_language_query_with_conversational_words_matches_direct_query(seeded_catalogue_session: Session):
    """Bug regression test:
    Query 1: 'I need wireless headphones under 10000 rupees. Recommend the best available option.'
    Query 2: 'Find wireless headphones under 10000'
    Both queries must extract clean keywords ['wireless', 'headphones'], set in_stock_only=True,
    and return identical catalogue products including SoundSilence ANC Wireless Headphones (₹8,999.00).
    """
    agent = ShoppingAgent(gemini_service=None, catalogue_currency="INR")

    # 1. Natural language query with conversational verbs, qualifiers, currency, and availability request
    req1 = ShoppingSearchRequest(query="I need wireless headphones under 10000 rupees. Recommend the best available option.")
    res1 = agent.search(req1, seeded_catalogue_session)

    assert res1.status == "success"
    assert res1.total_count >= 1
    prod1 = res1.products[0]
    assert prod1.sku == "AUDIO-WH-ANC500"
    assert prod1.name == "SoundSilence ANC Wireless Headphones"
    assert prod1.price == Decimal("8999.00")
    assert prod1.in_stock is True

    # 2. Direct concise query
    req2 = ShoppingSearchRequest(query="Find wireless headphones under 10000")
    res2 = agent.search(req2, seeded_catalogue_session)

    assert res2.status == "success"
    assert res2.total_count >= 1
    prod2 = res2.products[0]
    assert prod2.sku == "AUDIO-WH-ANC500"
    assert prod2.name == "SoundSilence ANC Wireless Headphones"
    assert prod2.price == Decimal("8999.00")

    # 3. Assert matching results
    skus_1 = [p.sku for p in res1.products]
    skus_2 = [p.sku for p in res2.products]
    assert skus_1 == skus_2, f"Expected matching results for both queries, got {skus_1} vs {skus_2}"


def test_bluetooth_headphones_query_finds_soundsilence(seeded_catalogue_session: Session):
    """Regression Query 3: 'Best Bluetooth headphones below 10000'
    Must strip 'best' and 'below', extract 'bluetooth' and 'headphones', and return
    SoundSilence ANC Wireless Headphones whose description contains Bluetooth.
    """
    agent = ShoppingAgent(gemini_service=None, catalogue_currency="INR")
    req = ShoppingSearchRequest(query="Best Bluetooth headphones below 10000")
    res = agent.search(req, seeded_catalogue_session)

    assert res.status == "success"
    assert res.total_count >= 1
    found = next((p for p in res.products if p.sku == "AUDIO-WH-ANC500"), None)
    assert found is not None
    assert found.name == "SoundSilence ANC Wireless Headphones"
    assert found.price == Decimal("8999.00")


def test_noise_cancelling_headphones_query(seeded_catalogue_session: Session):
    """Regression Query 4: 'I want noise cancelling headphones'
    Must match products containing multi-word phrase 'noise cancelling' (SoundSilence ANC and ClearTone Pro).
    """
    agent = ShoppingAgent(gemini_service=None, catalogue_currency="INR")
    req = ShoppingSearchRequest(query="I want noise cancelling headphones")
    res = agent.search(req, seeded_catalogue_session)

    assert res.status == "success"
    assert res.total_count >= 1
    skus = [p.sku for p in res.products]
    assert "AUDIO-WH-ANC500" in skus


def test_gaming_laptops_budget_exclusion_and_plural_inflection(seeded_catalogue_session: Session):
    """Regression Query 5: 'Find gaming laptops under 80000'
    Catalogue has 'Vortex Gaming Laptop 16-inch 165Hz' priced at ₹94,999.00.
    - Under budget 80000: must return 0 results (strict budget enforcement).
    - Under budget 100000: must return Vortex Gaming Laptop (plural inflection 'laptops' -> 'laptop').
    """
    agent = ShoppingAgent(gemini_service=None, catalogue_currency="INR")

    # Strict budget: ₹80,000 max budget should exclude ₹94,999 laptop
    req_80k = ShoppingSearchRequest(query="Find gaming laptops under 80000")
    res_80k = agent.search(req_80k, seeded_catalogue_session)
    assert res_80k.status == "no_results" or res_80k.total_count == 0

    # Sufficient budget: ₹100,000 max budget should include ₹94,999 laptop via plural inflection
    req_100k = ShoppingSearchRequest(query="Find gaming laptops under 100000")
    res_100k = agent.search(req_100k, seeded_catalogue_session)
    assert res_100k.status == "success"
    assert res_100k.total_count >= 1
    vortex = next((p for p in res_100k.products if p.sku == "LAPTOP-VORTEX-16"), None)
    assert vortex is not None
    assert vortex.name == "Vortex Gaming Laptop 16-inch 165Hz"
    assert vortex.price == Decimal("94999.00")


def test_keyword_normalization_removes_conversational_fillers():
    """Unit test: normalize_search_keywords filters generic conversational words while keeping tech terms."""
    raw = ["need", "wireless", "headphones", "under", "10000", "rupees", "recommend", "best", "available", "option"]
    cleaned = normalize_search_keywords(raw)
    assert cleaned == ["wireless", "headphones"]

    # Preserves technical terms and multi-word phrases
    tech_raw = ["noise cancelling", "bluetooth", "5g", "anc", "gaming", "battery life"]
    assert normalize_search_keywords(tech_raw) == ["noise cancelling", "bluetooth", "5g", "anc", "gaming", "battery life"]

    # Handles punctuation and empty tokens
    dirty_raw = ["wireless,", "headphones!", "", "   ", "option?"]
    assert normalize_search_keywords(dirty_raw) == ["wireless", "headphones"]


def test_term_search_variants_inflection_and_hyphenation():
    """Unit test: get_term_search_variants produces singular/plural and hyphen variants."""
    assert "laptop" in get_term_search_variants("laptops")
    assert "laptops" in get_term_search_variants("laptop")
    assert "headphone" in get_term_search_variants("headphones")
    assert "headphones" in get_term_search_variants("headphone")
    assert "smartwatch" in get_term_search_variants("smartwatches")
    assert "smartwatches" in get_term_search_variants("smartwatch")
    assert "noise cancelling" in get_term_search_variants("noise-cancelling")
    assert "noise-cancelling" in get_term_search_variants("noise cancelling")


def test_utf8_currency_symbol_and_header_preservation(seeded_catalogue_session: Session):
    """Test that Indian Rupee symbol (₹ / U+20B9) is preserved in recommendation output without encoding distortion."""
    agent = ShoppingAgent(gemini_service=None, catalogue_currency="INR")
    req = ShoppingSearchRequest(query="Find wireless headphones under 10000")
    res = agent.search(req, seeded_catalogue_session)

    assert res.status == "success"
    prod = res.products[0]
    assert prod.currency == "INR"
    assert "₹" in prod.recommendation_reason
    # Verify exact UTF-8 byte encoding
    raw_bytes = prod.recommendation_reason.encode("utf-8")
    assert b"\xe2\x82\xb9" in raw_bytes  # UTF-8 bytes for ₹
    # Verify no Latin-1 mojibake (â¹)
    assert "â¹" not in prod.recommendation_reason


def test_fallback_extract_intent_availability_requested_sets_in_stock_only():
    """Verify availability terms ('available', 'in stock') in query set in_stock_only=True."""
    # 1. 'Find available wireless headphones' -> in_stock_only=True
    intent_avail = _fallback_extract_intent("Find available wireless headphones")
    assert intent_avail.in_stock_only is True
    assert intent_avail.keywords == ["wireless", "headphones"]

    # 2. 'Find wireless headphones in stock' -> in_stock_only=True
    intent_stock = _fallback_extract_intent("Find wireless headphones in stock")
    assert intent_stock.in_stock_only is True
    assert intent_stock.keywords == ["wireless", "headphones"]


def test_fallback_extract_intent_without_availability_preserves_default_null():
    """Verify queries without explicit availability phrasing preserve the intended default/null in_stock_only."""
    intent_plain = _fallback_extract_intent("Find wireless headphones")
    assert intent_plain.in_stock_only is None
    assert intent_plain.keywords == ["wireless", "headphones"]


def test_shopping_search_explicit_api_stock_filter_precedence_over_fallback_intent(isolated_db: Session):
    """Verify explicit API request stock filter retains precedence over fallback extracted availability intent."""
    agent = ShoppingAgent(gemini_service=None, catalogue_currency="USD")

    # 1. Query has 'available' (fallback intent.in_stock_only=True), but explicit API request specifies in_stock_only=False
    # Explicit request in_stock_only=False must take precedence and include out-of-stock Adidas (qty=0)
    req_override = ShoppingSearchRequest(query="Find available running shoes", in_stock_only=False)
    res_override = agent.search(req_override, isolated_db)
    skus_override = {p.sku for p in res_override.products}
    assert "ADI-UB-01" in skus_override
    assert "NIKE-AZ-01" in skus_override

    # 2. Query has 'available' (fallback intent.in_stock_only=True) and API request leaves in_stock_only=None
    # Extracted intent in_stock_only=True is applied, excluding out-of-stock Adidas
    req_intent = ShoppingSearchRequest(query="Find available running shoes")
    assert req_intent.in_stock_only is None
    res_intent = agent.search(req_intent, isolated_db)
    skus_intent = {p.sku for p in res_intent.products}
    assert "ADI-UB-01" not in skus_intent
    assert "NIKE-AZ-01" in skus_intent

    # 3. Query has no availability (fallback intent.in_stock_only=None), explicit request specifies in_stock_only=False
    # Explicit request in_stock_only=False is applied, including out-of-stock Adidas
    req_plain_override = ShoppingSearchRequest(query="Find running shoes", in_stock_only=False)
    res_plain_override = agent.search(req_plain_override, isolated_db)
    skus_plain_override = {p.sku for p in res_plain_override.products}
    assert "ADI-UB-01" in skus_plain_override

    # 4. Query has no availability (fallback intent.in_stock_only=None) and request has in_stock_only=None
    # Falls through to safe default (True), excluding out-of-stock Adidas
    req_plain_default = ShoppingSearchRequest(query="Find running shoes")
    assert req_plain_default.in_stock_only is None
    res_plain_default = agent.search(req_plain_default, isolated_db)
    skus_plain_default = {p.sku for p in res_plain_default.products}
    assert "ADI-UB-01" not in skus_plain_default
    assert "NIKE-AZ-01" in skus_plain_default
