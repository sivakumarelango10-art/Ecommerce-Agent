import logging
import re
from typing import Literal

from google.genai import types
from sqlalchemy.orm import Session

from backend.app.schemas.shopping import (
    ShoppingSearchRequest,
    SupervisorRouteResponse,
)
from backend.app.services.gemini_service import GeminiService
from backend.app.services.shopping_agent import ShoppingAgent

logger = logging.getLogger(__name__)

# Deterministic patterns indicating shopping / catalogue search
_SHOPPING_PATTERNS = [
    re.compile(r"\b(?:buy|purchase|shop|shopping|browse|find|search|looking for|recommend)\b", re.IGNORECASE),
    re.compile(r"\b(?:price|cost|budget|under \$?|below \$?|less than \$?|in stock|available)\b", re.IGNORECASE),
    re.compile(r"\b(?:product|products|item|items|catalogue|catalog|shoes|laptop|phone|headphones)\b", re.IGNORECASE),
    re.compile(r"\$\d+", re.IGNORECASE),
]

# Deterministic patterns indicating general informational / advice questions
_GENERAL_PATTERNS = [
    re.compile(r"\b(?:what should i compare|how does|what is your policy|explain the difference)\b", re.IGNORECASE),
    re.compile(r"\b(?:return policy|shipping policy|terms of service|warranty policy)\b", re.IGNORECASE),
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
    ) -> None:
        self._gemini = gemini_service
        self._shopping_agent = shopping_agent or ShoppingAgent(gemini_service)

    def route(
        self, message: str
    ) -> tuple[Literal["shopping_agent", "general_assistant"], float, str]:
        """Classify message to destination deterministically or via Gemini proposal."""
        clean = message.strip()
        if not clean:
            return "general_assistant", 1.0, "Empty query routed to general assistant."

        # Check explicit general patterns first
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
                "Gemini supervisor routing failed, defaulting to general assistant",
                extra={"event": "supervisor_route_fallback", "error": str(exc)},
            )

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
            shopping_resp = self._shopping_agent.search(req, session)
            return SupervisorRouteResponse(
                destination="shopping_agent",
                confidence=confidence,
                reason=reason,
                shopping_response=shopping_resp,
                general_answer=None,
            )

        answer = self._gemini.answer(message)
        return SupervisorRouteResponse(
            destination="general_assistant",
            confidence=confidence,
            reason=reason,
            shopping_response=None,
            general_answer=answer,
        )
