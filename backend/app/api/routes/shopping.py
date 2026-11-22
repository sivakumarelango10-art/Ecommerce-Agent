import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from backend.app.api.auth_dependencies import get_optional_current_user
from backend.app.api.dependencies import (
    QuestionAnswerer,
    get_gemini_service,
    get_settings,
)
from backend.app.core.config import Settings
from backend.app.db.models import User
from backend.app.db.session import get_db_session
from backend.app.schemas.questions import QuestionRequest
from backend.app.schemas.shopping import (
    ShoppingSearchRequest,
    ShoppingSearchResponse,
    SupervisorRouteResponse,
)
from backend.app.services.gemini_service import (
    GeminiServiceError,
    GeminiTimeoutError,
)
from backend.app.services.shopping_agent import ShoppingAgent
from backend.app.services.supervisor_agent import SupervisorAgent

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/shopping", tags=["shopping"])


@router.post(
    "/search",
    response_model=ShoppingSearchResponse,
    status_code=status.HTTP_200_OK,
    summary="Search products via natural language or structured filters",
)
def search_products(
    request: ShoppingSearchRequest,
    session: Session = Depends(get_db_session),
    gemini_service: QuestionAnswerer = Depends(get_gemini_service),
    settings: Settings = Depends(get_settings),
    current_user: User | None = Depends(get_optional_current_user),
) -> ShoppingSearchResponse:
    """Discover real catalogue products using Gemini intent extraction and PostgreSQL.

    Supports both guests and authenticated users.
    """
    shopping_agent = ShoppingAgent(
        gemini_service, catalogue_currency=settings.catalogue_currency
    )
    try:
        response = shopping_agent.search(request, session)
        return response
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
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(
            "Shopping search encountered unexpected failure",
            extra={"event": "shopping_search_internal_error", "error_type": type(exc).__name__},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Shopping search could not be completed.",
        ) from exc


@router.post(
    "/chat",
    response_model=SupervisorRouteResponse,
    status_code=status.HTTP_200_OK,
    summary="Route shopping or general questions through the Supervisor Agent",
)
def chat_with_supervisor(
    body: QuestionRequest,
    request: Request,
    gemini_service: QuestionAnswerer = Depends(get_gemini_service),
    settings: Settings = Depends(get_settings),
    current_user: User | None = Depends(get_optional_current_user),
) -> SupervisorRouteResponse:
    """Supervised conversational endpoint routing between shopping discovery and general advice."""
    supervisor = SupervisorAgent(
        gemini_service, catalogue_currency=settings.catalogue_currency
    )
    db_factory = getattr(request.app.state, "db_session_factory", None)
    try:
        return supervisor.process(
            body.question,
            session_factory=db_factory,
            current_user=current_user,
        )
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
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(
            "Supervisor chat encountered unexpected failure",
            extra={"event": "supervisor_chat_internal_error", "error_type": type(exc).__name__},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Supervisor service could not complete the request.",
        ) from exc
