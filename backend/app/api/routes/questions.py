from fastapi import APIRouter, Depends, HTTPException, status

from backend.app.api.dependencies import QuestionAnswerer, get_gemini_service
from backend.app.schemas.questions import AnswerResponse, QuestionRequest
from backend.app.services.gemini_service import (
    GeminiServiceError,
    GeminiTimeoutError,
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
    service: QuestionAnswerer = Depends(get_gemini_service),
) -> AnswerResponse:
    try:
        answer = service.answer(body.question)
    except GeminiTimeoutError as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="The Gemini request timed out.",
        ) from exc
    except GeminiServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="The Gemini service could not complete the request.",
        ) from exc
    return AnswerResponse(answer=answer)
