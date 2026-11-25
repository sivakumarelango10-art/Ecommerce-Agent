from typing import Any, Protocol, Sequence
from uuid import uuid4

from fastapi import Depends, HTTPException, Request, status
from google.genai import types
from sqlalchemy.orm import Session

from backend.app.api.auth_dependencies import get_optional_current_user
from backend.app.core.config import Settings
from backend.app.db.models import User
from backend.app.db.session import get_db_session
from backend.app.services.gemini_service import GeminiResult
from backend.app.services.personal_gemini_runtime import RequestScopedGeminiService
from backend.app.services.providers import (
    AIProviderOrchestrator,
    GeminiProviderAdapter,
    GroqProviderAdapter,
    ProviderRuntimeResolver,
)


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


def get_ai_orchestrator(
    request: Request,
    session: Session = Depends(get_db_session),
    current_user: User | None = Depends(get_optional_current_user),
    settings: Settings = Depends(get_settings),
) -> AIProviderOrchestrator:
    platform_service = getattr(request.app.state, "gemini_service", None)
    if platform_service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI provider service is unavailable.",
        )
    session_factory = getattr(request.app.state, "db_session_factory", None)
    correlation_id = (
        request.headers.get("x-correlation-id")
        or request.headers.get("x-request-id")
        or str(uuid4())
    )
    client_factory = getattr(request.app.state, "gemini_client_factory", None)
    scoped_service = RequestScopedGeminiService(
        platform_service=platform_service,
        settings=settings,
        user=current_user,
        session_factory=session_factory,
        correlation_id=correlation_id,
        client_factory=client_factory,
    )

    # Build or retrieve resolver
    app_resolver = getattr(request.app.state, "provider_resolver", None)
    resolver = ProviderRuntimeResolver()
    if app_resolver is not None:
        for code, adapter in app_resolver._adapters.items():
            if code != "google_gemini":
                resolver.register_adapter(adapter)
    else:
        try:
            resolver.register_adapter(GroqProviderAdapter())
        except Exception:
            pass

    # Register request-scoped Gemini adapter (ensures user key priority, cooldowns, and slot health)
    scoped_gemini_adapter = GeminiProviderAdapter(
        scoped_service,
        settings=settings,
        default_model=settings.gemini_model,
    )
    resolver.register_adapter(scoped_gemini_adapter)

    return AIProviderOrchestrator(
        resolver=resolver,
        session=session,
        default_provider="google_gemini",
        default_model=settings.gemini_model,
        user=current_user,
        correlation_id=correlation_id,
        settings=settings,
    )
