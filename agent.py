"""Compatibility helper for the existing Streamlit entry point."""

from backend.app.core.config import load_settings
from backend.app.db.session import create_database_engine, create_session_factory
from backend.app.services.gemini_service import GeminiService
from backend.app.services.supervisor_agent import SupervisorAgent


def answer_question(question: str) -> str:
    settings = load_settings()
    service = GeminiService(settings)
    engine = None
    try:
        try:
            engine = create_database_engine(settings.database_url.get_secret_value())
            session_factory = create_session_factory(engine)
            with session_factory() as session:
                supervisor = SupervisorAgent(service)
                result = supervisor.process(question, session)
                if result.destination == "shopping_agent" and result.shopping_response:
                    resp = result.shopping_response
                    if not resp.products:
                        return resp.message
                    lines = [resp.message, ""]
                    for p in resp.products:
                        cat_str = (
                            f" [{', '.join(c.name for c in p.categories)}]"
                            if p.categories
                            else ""
                        )
                        lines.append(f"- **{p.name}**{cat_str}: ${p.price:.2f}")
                        if p.recommendation_reason:
                            lines.append(f"  {p.recommendation_reason}")
                    return "\n".join(lines)
                return result.general_answer or service.answer(question)
        except Exception:
            return service.answer(question)
        finally:
            if engine is not None:
                engine.dispose()
    finally:
        service.close()


if __name__ == "__main__":
    print(answer_question("What should I compare when choosing an online product?"))
