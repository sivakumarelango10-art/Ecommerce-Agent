from typing import Protocol

from fastapi import HTTPException, Request, status

from backend.app.core.config import Settings

class QuestionAnswerer(Protocol):
    def answer(self, question: str) -> str: ...


def get_gemini_service(request: Request) -> QuestionAnswerer:
    service = getattr(request.app.state, "gemini_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Gemini service is unavailable.",
        )
    return service


def get_settings(request: Request) -> Settings:
    return request.app.state.settings
