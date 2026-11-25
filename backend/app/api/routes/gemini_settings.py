from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from backend.app.api.auth_dependencies import get_current_user
from backend.app.api.dependencies import get_settings
from backend.app.core.config import Settings
from backend.app.db.models import User
from backend.app.db.session import get_db_session
from backend.app.schemas.gemini_settings import (
    PersonalGeminiKeyResponse,
    SavePersonalGeminiKeyRequest,
    TogglePersonalGeminiKeyRequest,
    ValidatePersonalGeminiKeyRequest,
    ValidatePersonalGeminiKeyResponse,
)
from backend.app.security.rate_limiter import check_and_record_auth_rate_limit
from backend.app.services.gemini_key_service import (
    GeminiKeyServiceError,
    GeminiKeyValidationError,
    delete_personal_key,
    get_personal_key_status,
    save_personal_key,
    toggle_personal_key,
    validate_gemini_api_key,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/customers/me/gemini-key", tags=["gemini-settings"])


def _resolve_client_factory(request: Request) -> Callable[..., Any] | None:
    return getattr(request.app.state, "gemini_client_factory", None)


@router.get("", response_model=PersonalGeminiKeyResponse)
def get_user_gemini_key_status(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> PersonalGeminiKeyResponse:
    """Retrieve safe metadata for the authenticated user's personal Gemini API key.

    Never exposes the plaintext API key or encrypted ciphertext.
    """
    return get_personal_key_status(session, user.id)


@router.post("", response_model=PersonalGeminiKeyResponse)
def post_user_gemini_key(
    request: Request,
    body: SavePersonalGeminiKeyRequest,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> PersonalGeminiKeyResponse:
    """Validate and store an authenticated user's personal Gemini API key.

    - Protects against brute-force attacks via rate limiting.
    - Validates key against Gemini API before persisting.
    - If validation fails, previous key remains intact and failed key is not saved.
    - Stored encrypted with authenticated HKDF-SHA256 Fernet.
    """
    check_and_record_auth_rate_limit(
        session=session,
        request=request,
        action="gemini_key_validate",
        identifier=str(user.id),
        settings=settings,
    )

    client_factory = _resolve_client_factory(request)

    try:
        record = save_personal_key(
            session=session,
            user_id=user.id,
            api_key=body.api_key,
            settings=settings,
            client_factory=client_factory,
        )
        return PersonalGeminiKeyResponse(
            has_key=True,
            is_enabled=record.is_enabled,
            key_hint=record.key_hint,
            status=record.status,
            last_validated_at=record.last_validated_at,
            last_used_at=record.last_used_at,
            cooldown_until=record.cooldown_until,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )
    except GeminiKeyValidationError as exc:
        if exc.status == "INVALID_KEY":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc
        if exc.status == "QUOTA_EXHAUSTED":
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=str(exc),
            ) from exc
        if exc.status == "MODEL_UNAVAILABLE":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc
        # TEMPORARY_FAILURE
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except GeminiKeyServiceError as exc:
        logger.error("Personal key service error for user %s: %s", user.id, type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Personal key management service is temporarily unavailable.",
        ) from exc


@router.patch("", response_model=PersonalGeminiKeyResponse)
def patch_user_gemini_key(
    body: TogglePersonalGeminiKeyRequest,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> PersonalGeminiKeyResponse:
    """Enable or disable the authenticated user's personal Gemini API key."""
    record = toggle_personal_key(session, user.id, body.is_enabled)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No Gemini API key found for this user.",
        )
    return PersonalGeminiKeyResponse(
        has_key=True,
        is_enabled=record.is_enabled,
        key_hint=record.key_hint,
        status=record.status,
        last_validated_at=record.last_validated_at,
        last_used_at=record.last_used_at,
        cooldown_until=record.cooldown_until,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


@router.delete("", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def delete_user_gemini_key(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> Response:
    """Delete the authenticated user's personal Gemini API key."""
    deleted = delete_personal_key(session, user.id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No Gemini API key found for this user.",
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/validate", response_model=ValidatePersonalGeminiKeyResponse)
def validate_user_gemini_key(
    request: Request,
    body: ValidatePersonalGeminiKeyRequest,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> ValidatePersonalGeminiKeyResponse:
    """Validate a personal Gemini API key without persisting it.

    - Rate limited to prevent key validation abuse.
    - Never stores the key in the database.
    """
    check_and_record_auth_rate_limit(
        session=session,
        request=request,
        action="gemini_key_validate",
        identifier=str(user.id),
        settings=settings,
    )

    client_factory = _resolve_client_factory(request)
    return validate_gemini_api_key(
        api_key=body.api_key,
        settings=settings,
        client_factory=client_factory,
    )
