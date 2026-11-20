import logging
import re
from typing import Literal

from google.genai import types
from sqlalchemy.orm import Session

from backend.app.core.constants import (
    CATALOGUE_UNAVAILABLE_MESSAGE,
    DEFAULT_CATALOGUE_CURRENCY,
    POLICY_UNAVAILABLE_MESSAGE,
)
from backend.app.schemas.shopping import (
    ShoppingSearchRequest,
    ShoppingSearchResponse,
    SupervisorRouteResponse,
)
from backend.app.services.gemini_service import GeminiService
from backend.app.services.shopping_agent import (
    ShoppingAgent,
    _fallback_extract_intent,
)

logger = logging.getLogger(__name__)

# Deterministic patterns indicating shopping / catalogue search
_SHOPPING_PATTERNS = [
    re.compile(r"\b(?:buy|purchase|shop|shopping|browse|find|search|looking for|recommend)\b", re.IGNORECASE),
    re.compile(r"\b(?:price|cost|budget|under|below|less than|in stock|available)\b", re.IGNORECASE),
    re.compile(r"\b(?:product|products|item|items|catalogue|catalog|shoes|laptop|phone|headphones)\b", re.IGNORECASE),
    re.compile(r"(?:[\$€£₹¥]|rs\.?)\s*\d+", re.IGNORECASE),
]

# Deterministic patterns indicating general informational / advice questions
_GENERAL_PATTERNS = [
    re.compile(r"\b(?:what should i compare|how does|what is your policy|explain the difference)\b", re.IGNORECASE),
    re.compile(r"\b(?:return policy|shipping policy|terms of service|warranty policy)\b", re.IGNORECASE),
]

_POLICY_PATTERNS = [
    re.compile(r"\b(?:return|refund|shipping|delivery|warranty|exchange)\s+polic(?:y|ies)\b", re.IGNORECASE),
    re.compile(r"\b(?:return\s+window|days\s+to\s+return|money\s+back\s+guarantee)\b", re.IGNORECASE),
    re.compile(r"\b(?:how\s+(?:long|many\s+days)\s+do\s+i\s+have\s+to\s+return)\b", re.IGNORECASE),
    re.compile(r"\b(?:do\s+you\s+(?:offer|have)\s+(?:a\s+)?warranty)\b", re.IGNORECASE),
    re.compile(r"\b(?:what\s+is\s+the\s+warranty|how\s+does\s+(?:shipping|delivery)\s+work)\b", re.IGNORECASE),
    re.compile(r"\b(?:can\s+i\s+get\s+a\s+refund|how\s+do\s+returns\s+work)\b", re.IGNORECASE),
]


_ROUTING_TOOL = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name="handoff_to_shopping_agent",
            description="Route to the Shopping Agent for discovering, searching, filtering, or recommending products from the catalogue.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "query": types.Schema(
                        type=types.Type.STRING,
                        description="The customer's shopping query.",
                    ),
                    "reason": types.Schema(
                        type=types.Type.STRING,
                        description="Why this request is a shopping/product discovery request.",
                    ),
                },
                required=["query"],
            ),
        ),
        types.FunctionDeclaration(
            name="answer_general_question",
            description="Route to the General Assistant for store policies, general consumer advice, or informational questions.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "query": types.Schema(
                        type=types.Type.STRING,
                        description="The customer's general question.",
                    ),
                    "reason": types.Schema(
                        type=types.Type.STRING,
                        description="Why this request is a general informational question.",
                    ),
                },
                required=["query"],
            ),
        ),
    ]
)


class SupervisorAgent:
    """Supervisor Agent routing requests to Shopping Agent or General Assistant."""

    def __init__(
        self,
        gemini_service: GeminiService,
        shopping_agent: ShoppingAgent | None = None,
        catalogue_currency: str = DEFAULT_CATALOGUE_CURRENCY,
    ) -> None:
        self._gemini = gemini_service
        self._catalogue_currency = catalogue_currency.strip().upper()
        self._shopping_agent = shopping_agent or ShoppingAgent(
            gemini_service, catalogue_currency=self._catalogue_currency
        )

    def route(
        self, message: str
    ) -> tuple[Literal["shopping_agent", "general_assistant"], float, str]:
        """Classify message to destination deterministically or via Gemini proposal."""
        clean = message.strip()
        if not clean:
            return "general_assistant", 1.0, "Empty query routed to general assistant."

        # Check explicit policy and general patterns first
        for pat in _POLICY_PATTERNS:
            if pat.search(clean):
                return "general_assistant", 1.0, f"Deterministic policy match: {pat.pattern}"

        for pat in _GENERAL_PATTERNS:
            if pat.search(clean):
                return "general_assistant", 1.0, f"Deterministic pattern match: {pat.pattern}"

        # Check shopping patterns
        for pat in _SHOPPING_PATTERNS:
            if pat.search(clean):
                return "shopping_agent", 1.0, f"Deterministic pattern match: {pat.pattern}"

        # If not clearly matched deterministically, use Gemini function calling proposal
        try:
            result = self._gemini.generate(
                f"Determine whether this user message requires looking up products in the catalogue or answering a general informational question:\n\n{clean}",
                tools=[_ROUTING_TOOL],
            )
            for call in result.function_calls:
                if call.name == "handoff_to_shopping_agent":
                    reason = str(call.args.get("reason", "Gemini proposed shopping agent handoff."))
                    return "shopping_agent", 0.9, reason
                if call.name == "answer_general_question":
                    reason = str(call.args.get("reason", "Gemini proposed general question routing."))
                    return "general_assistant", 0.9, reason
        except Exception as exc:
            logger.warning(
                "Gemini supervisor routing failed, falling back to deterministic heuristics",
                extra={"event": "supervisor_route_fallback", "error": str(exc)},
            )
            fallback_intent = _fallback_extract_intent(clean)
            if fallback_intent.is_shopping_intent and (
                fallback_intent.keywords
                or fallback_intent.min_budget is not None
                or fallback_intent.max_budget is not None
                or fallback_intent.category
            ):
                return "shopping_agent", 0.7, "Fallback heuristic identified shopping request."

        return "general_assistant", 0.5, "Default fallback to general assistant."

    def process(
        self,
        message: str,
        session: Session,
    ) -> SupervisorRouteResponse:
        destination, confidence, reason = self.route(message)

        logger.info(
            "Supervisor routed message",
            extra={
                "event": "supervisor_routed",
                "destination": destination,
                "confidence": confidence,
                "reason": reason,
            },
        )

        if destination == "shopping_agent":
            req = ShoppingSearchRequest(query=message)
            try:
                shopping_resp = self._shopping_agent.search(req, session)
            except Exception as exc:
                logger.error(
                    "Catalogue search failed in supervisor",
                    extra={"event": "supervisor_catalogue_search_error", "error_type": type(exc).__name__},
                )
                shopping_resp = ShoppingSearchResponse(
                    query=message,
                    currency=self._catalogue_currency,
                    status="no_results",
                    message=CATALOGUE_UNAVAILABLE_MESSAGE,
                    products=[],
                    total_count=0,
                    limit=req.limit,
                    offset=req.offset,
                )
            return SupervisorRouteResponse(
                destination="shopping_agent",
                confidence=confidence,
                reason=reason,
                shopping_response=shopping_resp,
                general_answer=None,
            )

        # Policy questions: return verified message if no policy data is available
        clean_msg = message.strip()
        if any(pat.search(clean_msg) for pat in _POLICY_PATTERNS):
            return SupervisorRouteResponse(
                destination="general_assistant",
                confidence=1.0,
                reason="Policy question intercepted; no verified store policies currently configured.",
                shopping_response=None,
                general_answer=POLICY_UNAVAILABLE_MESSAGE,
            )

        answer = self._gemini.answer(message)
        return SupervisorRouteResponse(
            destination="general_assistant",
            confidence=confidence,
            reason=reason,
            shopping_response=None,
            general_answer=answer,
        )
