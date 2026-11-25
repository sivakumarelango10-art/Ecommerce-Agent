from fastapi import APIRouter, Depends, HTTPException, status

from backend.app.api.dependencies import get_ai_orchestrator
from backend.app.schemas.questions import AnswerResponse, QuestionRequest
from backend.app.services.gemini_service import (
    GeminiServiceError,
    GeminiTimeoutError,
)
from backend.app.services.providers import (
    AIProviderError,
    AIProviderOrchestrator,
    AIProviderTimeoutError,
)

router = APIRouter()


@router.post(
    "/ask",
    response_model=AnswerResponse,
    status_code=status.HTTP_200_OK,
    tags=["assistant"],
)
def ask_question(
    body: QuestionRequest,
    orchestrator: AIProviderOrchestrator = Depends(get_ai_orchestrator),
) -> AnswerResponse:
    try:
        answer = orchestrator.generate_text(body.question)
    except (AIProviderTimeoutError, GeminiTimeoutError) as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="The Gemini request timed out.",
        ) from exc
    except (AIProviderError, GeminiServiceError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="The Gemini service could not complete the request.",
        ) from exc
    return AnswerResponse(answer=answer)
