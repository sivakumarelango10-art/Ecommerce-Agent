"""Compatibility helper for the existing Streamlit entry point."""

import logging

from backend.app.core.config import load_settings
from backend.app.core.constants import (
    CATALOGUE_UNAVAILABLE_MESSAGE,
    POLICY_UNAVAILABLE_MESSAGE,
    get_currency_symbol,
)
from backend.app.db.session import create_database_engine, create_session_factory
from backend.app.services.gemini_service import GeminiService
from backend.app.services.supervisor_agent import (
    _POLICY_PATTERNS,
    _SHOPPING_PATTERNS,
    SupervisorAgent,
)

logger = logging.getLogger(__name__)


def answer_question(question: str) -> str:
    settings = load_settings()
    service = GeminiService(settings)
    supervisor = SupervisorAgent(
        service, catalogue_currency=settings.catalogue_currency
    )
    engine = None
    try:
        clean_q = question.strip()
        if not clean_q:
            return "Please ask a question."

        # Intercept policy questions first: return verified unavailable message without DB/Gemini
        if any(pat.search(clean_q) for pat in _POLICY_PATTERNS):
            return POLICY_UNAVAILABLE_MESSAGE

        # Determine route safely without requiring database access
        try:
            destination, _, _ = supervisor.route(clean_q)
        except Exception as exc:
            logger.warning(
                "Supervisor routing failed in agent entry point",
                extra={"event": "agent_routing_error", "error_type": type(exc).__name__},
            )
            if any(pat.search(clean_q) for pat in _SHOPPING_PATTERNS):
                destination = "shopping_agent"
            else:
                destination = "general_assistant"

        if destination == "shopping_agent":
            try:
                engine = create_database_engine(settings.database_url.get_secret_value())
                session_factory = create_session_factory(engine)
                with session_factory() as session:
                    result = supervisor.process(clean_q, session)
                    if result.shopping_response:
                        resp = result.shopping_response
                        if not resp.products:
                            return resp.message
                        lines = [resp.message, ""]
                        symbol = get_currency_symbol(resp.currency)
                        for p in resp.products:
                            cat_str = (
                                f" [{', '.join(c.name for c in p.categories)}]"
                                if p.categories
                                else ""
                            )
                            lines.append(f"- **{p.name}**{cat_str}: {symbol}{p.price:.2f}")
                            if p.recommendation_reason:
                                lines.append(f"  {p.recommendation_reason}")
                        return "\n".join(lines)
                    return CATALOGUE_UNAVAILABLE_MESSAGE
            except Exception as exc:
                logger.error(
                    "Catalogue search failed in agent entry point",
                    extra={
                        "event": "agent_catalogue_failure",
                        "error_type": type(exc).__name__,
                    },
                )
                return CATALOGUE_UNAVAILABLE_MESSAGE
            finally:
                if engine is not None:
                    engine.dispose()
        else:
            # General assistant route: informational queries that do not require catalogue data
            return service.answer(clean_q)
    finally:
        service.close()


if __name__ == "__main__":
    print(answer_question("What should I compare when choosing an online product?"))
