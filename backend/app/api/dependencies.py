from typing import Any, Protocol, Sequence
from uuid import uuid4

from fastapi import Depends, HTTPException, Request, status
from google.genai import types

from backend.app.api.auth_dependencies import get_optional_current_user
from backend.app.core.config import Settings
from backend.app.db.models import User
from backend.app.services.gemini_service import GeminiResult
from backend.app.services.personal_gemini_runtime import RequestScopedGeminiService


class QuestionAnswerer(Protocol):
    def answer(self, question: str) -> str: ...
    def generate(
        self,
        contents: str | Sequence[types.Content] | None = None,
        *,
        question: str | None = None,
        tools: Sequence[types.Tool] | None = None,
        system_instruction: str | None = None,
        user_key: Any = None,
    ) -> GeminiResult: ...


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_gemini_service(
    request: Request,
    current_user: User | None = Depends(get_optional_current_user),
    settings: Settings = Depends(get_settings),
) -> QuestionAnswerer:
    platform_service = getattr(request.app.state, "gemini_service", None)
    if platform_service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Gemini service is unavailable.",
        )
    session_factory = getattr(request.app.state, "db_session_factory", None)
    correlation_id = (
        request.headers.get("x-correlation-id")
        or request.headers.get("x-request-id")
        or str(uuid4())
    )
    client_factory = getattr(request.app.state, "gemini_client_factory", None)
    return RequestScopedGeminiService(
        platform_service=platform_service,
        settings=settings,
        user=current_user,
        session_factory=session_factory,
        correlation_id=correlation_id,
        client_factory=client_factory,
    )
