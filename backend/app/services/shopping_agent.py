from decimal import Decimal, InvalidOperation
import json
import logging
import re
from typing import Protocol

from sqlalchemy.orm import Session

from backend.app.db.models import Product
from backend.app.schemas.catalog import ProductCategoryResponse
from backend.app.schemas.shopping import (
    ShoppingIntent,
    ShoppingProductItem,
    ShoppingSearchRequest,
    ShoppingSearchResponse,
    SortOrder,
)
from backend.app.services.gemini_service import (
    GeminiResponseError,
    GeminiServiceError,
    GeminiTimeoutError,
)
from backend.app.services.product_discovery_service import search_catalogue_products

logger = logging.getLogger(__name__)


class TextGenerator(Protocol):
    def answer(self, question: str) -> str: ...


_INTENT_EXTRACTION_PROMPT = """You are a shopping intent extractor for an e-commerce platform.
Analyze the user's shopping request and output ONLY a valid JSON object with the following schema:
{
  "keywords": ["list", "of", "search", "keywords"],
  "product_name": null or "string",
  "category": null or "string (e.g. electronics, audio, clothing)",
  "min_budget": null or number,
  "max_budget": null or number,
  "currency": null or "string (e.g. USD)",
  "brand": null or "string",
  "required_features": ["list", "of", "features"],
  "use_case": null or "string",
  "in_stock_only": true or false,
  "min_quantity": 1,
  "sort_by": null or "price_asc" or "price_desc" or "name_asc" or "name_desc" or "newest",
  "is_shopping_intent": true or false,
  "is_ambiguous_or_insufficient": true or false,
  "clarification_message": null or "question asking for missing details if ambiguous"
}

Rules:
1. Do not invent details. If a preference is not stated or implied, leave it as null or empty list.
2. If the user message is too vague to search (e.g. "I want to buy something", "help me shop", "show me products"), set "is_ambiguous_or_insufficient": true and provide a helpful "clarification_message".
3. Extract price limits accurately (e.g., "under $100" -> max_budget: 100; "between 20 and 50" -> min_budget: 20, max_budget: 50).
4. Output ONLY the raw JSON object, without markdown code blocks, explanation, or commentary.
"""


_SEARCH_STOP_WORDS = {
    "a", "an", "the", "and", "or", "in", "on", "at", "to", "for", "of", "with", "by",
    "buy", "purchase", "shop", "shopping", "want", "need", "find", "search", "looking",
    "look", "show", "get", "give", "me", "i", "we", "you", "he", "she", "it",
    "some", "any", "something", "anything", "please", "item", "items", "product",
    "products", "good", "best", "recommend", "under", "below", "above", "between",
}


def _clean_json_text(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def _fallback_extract_intent(query: str) -> ShoppingIntent:
    """Deterministic fallback if Gemini returns unparseable content."""
    clean_query = query.strip()
    raw_words = [w for w in re.findall(r"\b[A-Za-z0-9_-]+\b", clean_query) if len(w) > 2]
    words = [w for w in raw_words if w.lower() not in _SEARCH_STOP_WORDS]

    # Heuristic price extraction
    max_budget: Decimal | None = None
    min_budget: Decimal | None = None
    under_match = re.search(r"(?:under|below|less than|up to)\s*\$?(\d+(?:\.\d{1,2})?)", clean_query, re.IGNORECASE)
    if under_match:
        try:
            max_budget = Decimal(under_match.group(1))
        except InvalidOperation:
            pass

    between_match = re.search(
        r"(?:between|from)\s*\$?(\d+(?:\.\d{1,2})?)\s*(?:and|to)\s*\$?(\d+(?:\.\d{1,2})?)",
        clean_query,
        re.IGNORECASE,
    )
    if between_match:
        try:
            min_budget = Decimal(between_match.group(1))
            max_budget = Decimal(between_match.group(2))
        except InvalidOperation:
            pass

    # Ambiguity check
    vague_phrases = {"buy something", "shop", "help me buy", "show products", "anything", "show me"}
    is_vague = clean_query.lower() in vague_phrases or len(words) == 0

    return ShoppingIntent(
        keywords=words,
        min_budget=min_budget,
        max_budget=max_budget,
        is_shopping_intent=True,
        is_ambiguous_or_insufficient=is_vague,
        clarification_message=(
            "Could you please specify what kind of product or category you are looking for?"
            if is_vague
            else None
        ),
    )


class ShoppingAgent:
    def __init__(self, gemini_service: TextGenerator | None = None) -> None:
        self._gemini = gemini_service

    def extract_intent(self, query: str) -> ShoppingIntent:
        query = query.strip()
        if not query:
            return ShoppingIntent(
                is_shopping_intent=False,
                is_ambiguous_or_insufficient=True,
                clarification_message="Please tell me what you would like to search for.",
            )

        if self._gemini is None:
            return _fallback_extract_intent(query)

        prompt = f"{_INTENT_EXTRACTION_PROMPT}\nUser request: {query}"
        try:
            raw_response = self._gemini.answer(prompt)
        except (GeminiTimeoutError, GeminiServiceError):
            raise
        except Exception as exc:
            logger.warning(
                "Gemini intent extraction failed, using fallback",
                extra={"event": "intent_extraction_fallback", "error": str(exc)},
            )
            return _fallback_extract_intent(query)

        try:
            json_text = _clean_json_text(raw_response)
            data = json.loads(json_text)
            intent = ShoppingIntent.model_validate(data)
            logger.info(
                "Shopping intent successfully extracted",
                extra={
                    "event": "intent_extracted",
                    "keywords": intent.keywords,
                    "category": intent.category,
                    "min_budget": str(intent.min_budget) if intent.min_budget else None,
                    "max_budget": str(intent.max_budget) if intent.max_budget else None,
                },
            )
            return intent
        except Exception as exc:
            logger.warning(
                "Failed to parse Gemini intent JSON, using heuristic fallback",
                extra={"event": "intent_json_parse_error", "raw": raw_response[:200], "error": str(exc)},
            )
            return _fallback_extract_intent(query)

    def generate_grounded_explanation(
        self, product: Product, intent: ShoppingIntent | None
    ) -> str:
        """Produce an explanation grounded strictly in verified database fields."""
        reasons: list[str] = []
        price_str = f"${product.price:.2f}"

        if intent and intent.max_budget is not None and product.price <= intent.max_budget:
            reasons.append(f"{price_str} is within your budget of ${intent.max_budget:.2f}")
        else:
            reasons.append(f"Priced at {price_str}")

        qty = product.inventory.available_quantity if product.inventory else 0
        if qty > 0:
            reasons.append(f"in stock ({qty} available)")
        else:
            reasons.append("currently out of stock")

        if product.categories:
            category_names = ", ".join(c.name for c in product.categories)
            reasons.append(f"category: {category_names}")

        return "; ".join(reasons) + "."

    def search(
        self,
        request: ShoppingSearchRequest,
        session: Session,
    ) -> ShoppingSearchResponse:
        intent: ShoppingIntent | None = None

        if request.query and request.query.strip():
            intent = self.extract_intent(request.query)

            # Check for ambiguity
            if intent.is_ambiguous_or_insufficient:
                return ShoppingSearchResponse(
                    query=request.query,
                    intent=intent,
                    status="insufficient_information",
                    message=(
                        intent.clarification_message
                        or "Your request is too broad. Please provide a category, product name, or budget."
                    ),
                    products=[],
                    total_count=0,
                    limit=request.limit,
                    offset=request.offset,
                )

        # Merge intent with explicit overrides from request
        category = request.category or (intent.category if intent else None)
        min_price = (
            request.min_price
            if request.min_price is not None
            else (intent.min_budget if intent else None)
        )
        max_price = (
            request.max_price
            if request.max_price is not None
            else (intent.max_budget if intent else None)
        )
        brand = request.brand or (intent.brand if intent else None)
        keywords = list(request.keywords)
        if intent and intent.keywords:
            for kw in intent.keywords:
                if kw not in keywords:
                    keywords.append(kw)
        if intent and intent.product_name and intent.product_name not in keywords:
            keywords.append(intent.product_name)

        required_features = intent.required_features if intent else []
        sort_by = request.sort_by or (intent.sort_by if intent else SortOrder.PRICE_ASC)
        in_stock_only = request.in_stock_only if request.in_stock_only is not None else (
            intent.in_stock_only if intent else True
        )

        products, total_count = search_catalogue_products(
            session=session,
            keywords=keywords,
            category=category,
            min_price=min_price,
            max_price=max_price,
            brand=brand,
            required_features=required_features,
            in_stock_only=in_stock_only,
            sort_by=sort_by,
            limit=request.limit,
            offset=request.offset,
        )

        if total_count == 0:
            criteria_details = []
            if category:
                criteria_details.append(f"in category '{category}'")
            if min_price is not None or max_price is not None:
                budget_part = []
                if min_price is not None:
                    budget_part.append(f"min ${min_price:.2f}")
                if max_price is not None:
                    budget_part.append(f"max ${max_price:.2f}")
                criteria_details.append(f"with budget ({', '.join(budget_part)})")
            if keywords:
                criteria_details.append(f"matching '{', '.join(keywords)}'")

            criteria_summary = " ".join(criteria_details) if criteria_details else "matching your search"
            return ShoppingSearchResponse(
                query=request.query,
                intent=intent,
                status="no_results",
                message=f"No products found {criteria_summary}. Try broadening your search or adjusting filters.",
                products=[],
                total_count=0,
                limit=request.limit,
                offset=request.offset,
            )

        items: list[ShoppingProductItem] = []
        for p in products:
            qty = p.inventory.available_quantity if p.inventory else 0
            explanation = self.generate_grounded_explanation(p, intent)
            items.append(
                ShoppingProductItem(
                    id=p.id,
                    sku=p.sku,
                    name=p.name,
                    description=p.description,
                    price=p.price,
                    categories=[ProductCategoryResponse.model_validate(c) for c in p.categories],
                    in_stock=qty > 0,
                    available_quantity=qty,
                    recommendation_reason=explanation,
                )
            )

        return ShoppingSearchResponse(
            query=request.query,
            intent=intent,
            status="success",
            message=f"Found {total_count} matching product{'s' if total_count != 1 else ''}.",
            products=items,
            total_count=total_count,
            limit=request.limit,
            offset=request.offset,
        )
