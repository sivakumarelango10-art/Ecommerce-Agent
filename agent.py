"""Compatibility helper for the existing Streamlit entry point."""

from backend.app.core.config import load_settings
from backend.app.services.gemini_service import GeminiService


def answer_question(question: str) -> str:
    service = GeminiService(load_settings())
    try:
        return service.answer(question)
    finally:
        service.close()


if __name__ == "__main__":
    print(answer_question("What should I compare when choosing an online product?"))
