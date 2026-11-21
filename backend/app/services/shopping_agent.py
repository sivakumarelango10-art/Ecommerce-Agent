from decimal import Decimal, InvalidOperation
import json
import logging
import re
from typing import Protocol

from sqlalchemy.orm import Session

from backend.app.core.constants import (
    DEFAULT_CATALOGUE_CURRENCY,
    get_currency_symbol,
    get_supported_currency_aliases,
)
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
  "currency": null or "string (e.g. INR)",
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
3. Extract price limits accurately (e.g., "under ₹10,000" -> max_budget: 10000; "between 2000 and 5000" -> min_budget: 2000, max_budget: 5000).
4. Output ONLY the raw JSON object, without markdown code blocks, explanation, or commentary.
"""


_SEARCH_STOP_WORDS = {
    "a", "an", "the", "and", "or", "in", "on", "at", "to", "for", "of", "with", "by",
    "buy", "purchase", "shop", "shopping", "want", "need", "find", "search", "looking",
    "look", "show", "get", "give", "me", "i", "we", "you", "he", "she", "it",
    "some", "any", "something", "anything", "please", "item", "items", "product",
    "products", "good", "best", "recommend", "under", "below", "above", "between",
    "usd", "inr", "eur", "gbp", "cad", "aud", "jpy", "rupee", "rupees", "dollar",
    "dollars", "euro", "euros", "pound", "pounds", "rs",
}


def _clean_json_text(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


_GROUPED_NUMBER_PATTERN = r"(?:\d{1,3}(?:,\d{2,3})+|\d+)(?:\.\d{1,2})?"
_CURRENCY_PREFIX_PATTERN = r"(?:[\$€£₹¥]|C\$|A\$|rs\.?|\b(?:usd|inr|eur|gbp|cad|aud|jpy|rupees?|dollars?|euros?|pounds?)\b)\s*"


def _fallback_extract_intent(query: str) -> ShoppingIntent:
    """Deterministic fallback if Gemini returns unparseable content."""
    clean_query = query.strip()
    raw_words = [w for w in re.findall(r"\b[A-Za-z0-9_-]+\b", clean_query) if len(w) > 2]
    words = [w for w in raw_words if w.lower() not in _SEARCH_STOP_WORDS and not w.isdigit()]

    # Currency extraction
    detected_currency: str | None = None
    curr_match = re.search(
        r"(?:\b(?:USD|INR|EUR|GBP|CAD|AUD|JPY|rupees?|euros?|pounds?|dollars?)\b|rs\.?|[€£₹¥$]|C\$|A\$)",
        clean_query,
        re.IGNORECASE,
    )
    if curr_match:
        matched_curr = curr_match.group(0).upper()
        if matched_curr in {"$", "DOLLAR", "DOLLARS"}:
            detected_currency = "USD"
        elif matched_curr in {"₹", "RUPEE", "RUPEES", "RS", "RS."}:
            detected_currency = "INR"
        elif matched_curr in {"€", "EURO", "EUROS"}:
            detected_currency = "EUR"
        elif matched_curr in {"£", "POUND", "POUNDS"}:
            detected_currency = "GBP"
        elif matched_curr in {"¥", "YEN"}:
            detected_currency = "JPY"
        elif matched_curr in {"C$"}:
            detected_currency = "CAD"
        elif matched_curr in {"A$"}:
            detected_currency = "AUD"
        else:
            detected_currency = matched_curr

    # Heuristic price extraction supporting grouped digits (₹70,000, ₹1,00,000, ₹12,50,000, $1,000)
    max_budget: Decimal | None = None
    min_budget: Decimal | None = None
    under_match = re.search(
        rf"(?:under|below|less than|up to)\s*(?:{_CURRENCY_PREFIX_PATTERN})?({_GROUPED_NUMBER_PATTERN})",
        clean_query,
        re.IGNORECASE,
    )
    if under_match:
        try:
            val_str = under_match.group(1).replace(",", "")
            max_budget = Decimal(val_str)
        except InvalidOperation:
            pass

    between_match = re.search(
        rf"(?:between|from)\s*(?:{_CURRENCY_PREFIX_PATTERN})?({_GROUPED_NUMBER_PATTERN})\s*(?:and|to)\s*(?:{_CURRENCY_PREFIX_PATTERN})?({_GROUPED_NUMBER_PATTERN})",
        clean_query,
        re.IGNORECASE,
    )
    if between_match:
        try:
            min_val_str = between_match.group(1).replace(",", "")
            max_val_str = between_match.group(2).replace(",", "")
            min_budget = Decimal(min_val_str)
            max_budget = Decimal(max_val_str)
        except InvalidOperation:
            pass

    if max_budget is None and min_budget is None:
        budget_match = re.search(
            rf"(?:budget(?: of)?|for|max(?:imum)?)\s*(?:{_CURRENCY_PREFIX_PATTERN})?({_GROUPED_NUMBER_PATTERN})",
            clean_query,
            re.IGNORECASE,
        )
        if budget_match:
            try:
                max_budget = Decimal(budget_match.group(1).replace(",", ""))
            except InvalidOperation:
                pass

    if max_budget is None and min_budget is None:
        symbol_price_match = re.search(
            rf"(?:[\$€£₹¥]|C\$|A\$|rs\.?)\s*({_GROUPED_NUMBER_PATTERN})",
            clean_query,
            re.IGNORECASE,
        )
        if symbol_price_match:
            try:
                max_budget = Decimal(symbol_price_match.group(1).replace(",", ""))
            except InvalidOperation:
                pass

    # Ambiguity check
    vague_phrases = {"buy something", "shop", "help me buy", "show products", "anything", "show me"}
    is_vague = clean_query.lower() in vague_phrases or len(words) == 0

    return ShoppingIntent(
        keywords=words,
        min_budget=min_budget,
        max_budget=max_budget,
        currency=detected_currency,
        is_shopping_intent=True,
        is_ambiguous_or_insufficient=is_vague,
        clarification_message=(
            "Could you please specify what kind of product or category you are looking for?"
            if is_vague
            else None
        ),
    )


class ShoppingAgent:
    def __init__(
        self,
        gemini_service: TextGenerator | None = None,
        catalogue_currency: str = DEFAULT_CATALOGUE_CURRENCY,
    ) -> None:
        self._gemini = gemini_service
        self._catalogue_currency = catalogue_currency.strip().upper()
        self._supported_currencies = get_supported_currency_aliases(self._catalogue_currency)
        self._currency_symbol = get_currency_symbol(self._catalogue_currency)

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
                extra={"event": "intent_extraction_fallback", "error_type": type(exc).__name__},
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
                    "terms_count": len(intent.keywords),
                    "has_category": bool(intent.category),
                    "has_min_budget": intent.min_budget is not None,
                    "has_max_budget": intent.max_budget is not None,
                },
            )
            return intent
        except Exception as exc:
            logger.warning(
                "Failed to parse Gemini intent JSON, using heuristic fallback",
                extra={"event": "intent_json_parse_error", "error_type": type(exc).__name__},
            )
            return _fallback_extract_intent(query)

    def generate_grounded_explanation(
        self,
        product: Product,
        intent: ShoppingIntent | None,
        max_budget: Decimal | None = None,
    ) -> str:
        """Produce an explanation grounded strictly in verified database fields."""
        reasons: list[str] = []
        symbol = self._currency_symbol
        price_str = f"{symbol}{product.price:.2f}"

        effective_max = (
            max_budget
            if max_budget is not None
            else (intent.max_budget if intent else None)
        )
        if effective_max is not None and product.price <= effective_max:
            reasons.append(f"{price_str} is within your budget of {symbol}{effective_max:.2f}")
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

        # Currency validation:
        # Determine requested currency (from explicit request or extracted intent)
        # Check this BEFORE ambiguity check so that ambiguous queries specifying mismatched
        # currencies (e.g. "buy something in dollars") return unsupported_currency, not insufficient_information.
        requested_currency = request.currency or (intent.currency if intent else None)
        if requested_currency:
            norm_curr = requested_currency.strip().upper()
            if norm_curr not in self._supported_currencies:
                logger.info(
                    "Unsupported currency requested",
                    extra={
                        "event": "unsupported_currency",
                        "catalogue_currency": self._catalogue_currency,
                    },
                )
                return ShoppingSearchResponse(
                    query=request.query,
                    intent=intent,
                    currency=self._catalogue_currency,
                    status="unsupported_currency",
                    message=(
                        f"The catalogue prices and budgets are in {self._catalogue_currency}. "
                        f"Searches with budget in '{requested_currency}' are not supported, "
                        f"and currency conversions are not performed. Please specify your budget in {self._catalogue_currency}."
                    ),
                    products=[],
                    total_count=0,
                    limit=request.limit,
                    offset=request.offset,
                )

        # Ambiguity check:
        # Only after currency validation passes, check if the query lacks sufficient search criteria
        if intent and intent.is_ambiguous_or_insufficient:
            return ShoppingSearchResponse(
                query=request.query,
                intent=intent,
                currency=self._catalogue_currency,
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

        # Sorting preference precedence:
        # 1. Explicit request preference
        # 2. Extracted intent preference
        # 3. Default to price ascending
        if request.sort_by is not None:
            sort_by = request.sort_by
        elif intent and intent.sort_by is not None:
            sort_by = intent.sort_by
        else:
            sort_by = SortOrder.PRICE_ASC

        # Stock preference precedence:
        # 1. Explicit request preference
        # 2. Extracted intent preference
        # 3. Safe default (True)
        if request.in_stock_only is not None:
            in_stock_only = request.in_stock_only
        elif intent and intent.in_stock_only is not None:
            in_stock_only = intent.in_stock_only
        else:
            in_stock_only = True

        # Minimum quantity precedence:
        # 1. Explicit request preference
        # 2. Extracted intent preference
        # 3. Safe default (1)
        if request.min_quantity is not None:
            min_quantity = request.min_quantity
        elif intent and intent.min_quantity is not None:
            min_quantity = intent.min_quantity
        else:
            min_quantity = 1

        products, total_count = search_catalogue_products(
            session=session,
            keywords=keywords,
            category=category,
            min_price=min_price,
            max_price=max_price,
            brand=brand,
            required_features=required_features,
            in_stock_only=in_stock_only,
            min_quantity=min_quantity,
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
                    budget_part.append(f"min {self._currency_symbol}{min_price:.2f}")
                if max_price is not None:
                    budget_part.append(f"max {self._currency_symbol}{max_price:.2f}")
                criteria_details.append(f"with budget ({', '.join(budget_part)})")
            if keywords:
                criteria_details.append(f"matching '{', '.join(keywords)}'")

            criteria_summary = " ".join(criteria_details) if criteria_details else "matching your search"
            return ShoppingSearchResponse(
                query=request.query,
                intent=intent,
                currency=self._catalogue_currency,
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
            explanation = self.generate_grounded_explanation(
                p, intent, max_budget=max_price
            )
            items.append(
                ShoppingProductItem(
                    id=p.id,
                    sku=p.sku,
                    name=p.name,
                    description=p.description,
                    price=p.price,
                    currency=self._catalogue_currency,
                    categories=[ProductCategoryResponse.model_validate(c) for c in p.categories],
                    in_stock=qty > 0,
                    available_quantity=qty,
                    recommendation_reason=explanation,
                )
            )

        return ShoppingSearchResponse(
            query=request.query,
            intent=intent,
            currency=self._catalogue_currency,
            status="success",
            message=f"Found {total_count} matching product{'s' if total_count != 1 else ''}.",
            products=items,
            total_count=total_count,
            limit=request.limit,
            offset=request.offset,
        )
