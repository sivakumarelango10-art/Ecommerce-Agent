from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from backend.app.api.auth_dependencies import (
    require_admin_user,
    require_super_admin_user,
)
from backend.app.db.models import User
from backend.app.db.seed_ai_catalogue import seed_ai_provider_catalogue
from backend.app.db.session import get_db_session
from backend.app.schemas.ai_provider import (
    AIModelCreate,
    AIModelResponse,
    AIModelUpdate,
    AIProviderCreate,
    AIProviderResponse,
    AIProviderUpdate,
    CatalogueSeedResponse,
    PlatformAIKeyCreate,
    PlatformAIKeyResponse,
    PlatformAIKeyUpdate,
)
from backend.app.security.key_encryption import KeyEncryptionError
from backend.app.services.ai_provider_service import (
    AIModelNotFoundError,
    AIProviderNotFoundError,
    AIProviderServiceError,
    PlatformKeyNotFoundError,
    SSRFSecurityError,
    create_model,
    create_platform_key,
    create_provider,
    delete_platform_key,
    get_model,
    get_platform_key,
    get_provider,
    list_models,
    list_platform_keys,
    list_providers,
    update_model,
    update_platform_key,
    update_provider,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai", tags=["admin-ai-registry"])


def _extract_request_meta(request: Request) -> tuple[str | None, str | None]:
    ip_address = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")
    return ip_address, user_agent


# ---------------------------------------------------------------------------
# Provider Endpoints
# ---------------------------------------------------------------------------

@router.get("/providers", response_model=list[AIProviderResponse])
def get_providers_endpoint(
    status: str | None = Query(None, description="Filter by connection status (AVAILABLE, PLANNED, CONFIGURED, etc.)"),
    is_enabled: bool | None = Query(None, description="Filter by enabled state"),
    adapter_type: str | None = Query(None, description="Filter by adapter type"),
    _admin: User = Depends(require_admin_user),
    session: Session = Depends(get_db_session),
) -> list[AIProviderResponse]:
    """List all registered AI providers with operational statuses and priorities."""
    providers = list_providers(session, status=status, is_enabled=is_enabled, adapter_type=adapter_type)
    results = []
    for p in providers:
        resp = AIProviderResponse.model_validate(p)
        resp.models_count = len(p.models)
        resp.keys_count = len(p.keys)
        results.append(resp)
    return results


@router.get("/providers/{provider_id}", response_model=AIProviderResponse)
def get_provider_endpoint(
    provider_id: UUID,
    _admin: User = Depends(require_admin_user),
    session: Session = Depends(get_db_session),
) -> AIProviderResponse:
    """Retrieve details for a single AI provider."""
    provider = get_provider(session, provider_id)
    if not provider:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"AI Provider '{provider_id}' not found.",
        )
    resp = AIProviderResponse.model_validate(provider)
    resp.models_count = len(provider.models)
    resp.keys_count = len(provider.keys)
    return resp


@router.post("/providers", response_model=AIProviderResponse, status_code=status.HTTP_201_CREATED)
def create_provider_endpoint(
    payload: AIProviderCreate,
    request: Request,
    admin: User = Depends(require_super_admin_user),
    session: Session = Depends(get_db_session),
) -> AIProviderResponse:
    """Register a new AI provider (Super Admin only). Includes SSRF validation on base_url."""
    ip, ua = _extract_request_meta(request)
    try:
        provider = create_provider(
            session=session,
            payload=payload,
            actor_user_id=admin.id,
            ip_address=ip,
            user_agent=ua,
        )
        session.commit()
    except SSRFSecurityError as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except AIProviderServiceError as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        session.rollback()
        logger.exception("Unexpected error creating AI provider")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to register AI provider.",
        ) from exc

    resp = AIProviderResponse.model_validate(provider)
    resp.models_count = 0
    resp.keys_count = 0
    return resp


@router.patch("/providers/{provider_id}", response_model=AIProviderResponse)
def update_provider_endpoint(
    provider_id: UUID,
    payload: AIProviderUpdate,
    request: Request,
    admin: User = Depends(require_super_admin_user),
    session: Session = Depends(get_db_session),
) -> AIProviderResponse:
    """Update an AI provider configuration (Super Admin only)."""
    ip, ua = _extract_request_meta(request)
    try:
        provider = update_provider(
            session=session,
            provider_id=provider_id,
            payload=payload,
            actor_user_id=admin.id,
            ip_address=ip,
            user_agent=ua,
        )
        session.commit()
    except AIProviderNotFoundError as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except SSRFSecurityError as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except AIProviderServiceError as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        session.rollback()
        logger.exception("Unexpected error updating AI provider")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update AI provider.",
        ) from exc

    resp = AIProviderResponse.model_validate(provider)
    resp.models_count = len(provider.models)
    resp.keys_count = len(provider.keys)
    return resp


# ---------------------------------------------------------------------------
# Model Endpoints
# ---------------------------------------------------------------------------

@router.get("/models", response_model=list[AIModelResponse])
def get_models_endpoint(
    provider_id: UUID | None = Query(None, description="Filter by provider UUID"),
    access_class: str | None = Query(None, description="Filter by access class"),
    is_enabled: bool | None = Query(None, description="Filter by enabled state"),
    _admin: User = Depends(require_admin_user),
    session: Session = Depends(get_db_session),
) -> list[AIModelResponse]:
    """List AI models with capability flags, context limits, and access classes."""
    models = list_models(session, provider_id=provider_id, access_class=access_class, is_enabled=is_enabled)
    return [AIModelResponse.model_validate(m) for m in models]


@router.get("/models/{model_id}", response_model=AIModelResponse)
def get_model_endpoint(
    model_id: UUID,
    _admin: User = Depends(require_admin_user),
    session: Session = Depends(get_db_session),
) -> AIModelResponse:
    """Retrieve details for a single AI model."""
    model = get_model(session, model_id)
    if not model:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"AI Model '{model_id}' not found.",
        )
    return AIModelResponse.model_validate(model)


@router.post("/models", response_model=AIModelResponse, status_code=status.HTTP_201_CREATED)
def create_model_endpoint(
    payload: AIModelCreate,
    request: Request,
    admin: User = Depends(require_super_admin_user),
    session: Session = Depends(get_db_session),
) -> AIModelResponse:
    """Register a new AI model for a provider (Super Admin only)."""
    ip, ua = _extract_request_meta(request)
    try:
        model = create_model(
            session=session,
            payload=payload,
            actor_user_id=admin.id,
            ip_address=ip,
            user_agent=ua,
        )
        session.commit()
    except AIProviderNotFoundError as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except AIProviderServiceError as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        session.rollback()
        logger.exception("Unexpected error creating AI model")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create AI model.",
        ) from exc

    return AIModelResponse.model_validate(model)


@router.patch("/models/{model_id}", response_model=AIModelResponse)
def update_model_endpoint(
    model_id: UUID,
    payload: AIModelUpdate,
    request: Request,
    admin: User = Depends(require_super_admin_user),
    session: Session = Depends(get_db_session),
) -> AIModelResponse:
    """Update AI model configuration, capabilities, or pricing metadata (Super Admin only)."""
    ip, ua = _extract_request_meta(request)
    try:
        model = update_model(
            session=session,
            model_id=model_id,
            payload=payload,
            actor_user_id=admin.id,
            ip_address=ip,
            user_agent=ua,
        )
        session.commit()
    except AIModelNotFoundError as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except AIProviderServiceError as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        session.rollback()
        logger.exception("Unexpected error updating AI model")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update AI model.",
        ) from exc

    return AIModelResponse.model_validate(model)


# ---------------------------------------------------------------------------
# Platform Key Endpoints
# ---------------------------------------------------------------------------

@router.get("/keys", response_model=list[PlatformAIKeyResponse])
def get_platform_keys_endpoint(
    provider_id: UUID | None = Query(None, description="Filter by provider UUID"),
    quota_group: str | None = Query(None, description="Filter by quota group"),
    status: str | None = Query(None, description="Filter by status (UNVERIFIED, ACTIVE, COOLDOWN, etc.)"),
    _admin: User = Depends(require_admin_user),
    session: Session = Depends(get_db_session),
) -> list[PlatformAIKeyResponse]:
    """List platform keys. Plaintext keys and ciphertext are NEVER returned."""
    keys = list_platform_keys(session, provider_id=provider_id, quota_group=quota_group, status=status)
    return [PlatformAIKeyResponse.model_validate(k) for k in keys]


@router.get("/keys/{key_id}", response_model=PlatformAIKeyResponse)
def get_platform_key_endpoint(
    key_id: UUID,
    _admin: User = Depends(require_admin_user),
    session: Session = Depends(get_db_session),
) -> PlatformAIKeyResponse:
    """Retrieve details for a single platform key (masked key hint only)."""
    key_record = get_platform_key(session, key_id)
    if not key_record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Platform AI key '{key_id}' not found.",
        )
    return PlatformAIKeyResponse.model_validate(key_record)


@router.post("/keys", response_model=PlatformAIKeyResponse, status_code=status.HTTP_201_CREATED)
def create_platform_key_endpoint(
    payload: PlatformAIKeyCreate,
    request: Request,
    admin: User = Depends(require_super_admin_user),
    session: Session = Depends(get_db_session),
) -> PlatformAIKeyResponse:
    """Register a new encrypted company platform AI key (Super Admin only). Plaintext is never persisted."""
    settings = getattr(request.app.state, "settings", None)
    encryption_secret = getattr(settings, "platform_key_encryption_secret", None)
    if not encryption_secret or not encryption_secret.get_secret_value().strip():
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Platform key encryption secret is not configured on the server.",
        )

    ip, ua = _extract_request_meta(request)
    try:
        key_record = create_platform_key(
            session=session,
            payload=payload,
            encryption_secret=encryption_secret,
            actor_user_id=admin.id,
            ip_address=ip,
            user_agent=ua,
        )
        session.commit()
    except AIProviderNotFoundError as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (AIProviderServiceError, KeyEncryptionError) as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        session.rollback()
        logger.exception("Unexpected error storing platform AI key")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to store platform AI key.",
        ) from exc

    return PlatformAIKeyResponse.model_validate(key_record)


@router.patch("/keys/{key_id}", response_model=PlatformAIKeyResponse)
def update_platform_key_endpoint(
    key_id: UUID,
    payload: PlatformAIKeyUpdate,
    request: Request,
    admin: User = Depends(require_super_admin_user),
    session: Session = Depends(get_db_session),
) -> PlatformAIKeyResponse:
    """Update platform key label, quota group, status, or active flag (Super Admin only)."""
    ip, ua = _extract_request_meta(request)
    try:
        key_record = update_platform_key(
            session=session,
            key_id=key_id,
            payload=payload,
            actor_user_id=admin.id,
            ip_address=ip,
            user_agent=ua,
        )
        session.commit()
    except PlatformKeyNotFoundError as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except AIProviderServiceError as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        session.rollback()
        logger.exception("Unexpected error updating platform AI key")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update platform AI key.",
        ) from exc

    return PlatformAIKeyResponse.model_validate(key_record)


@router.delete("/keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_platform_key_endpoint(
    key_id: UUID,
    request: Request,
    admin: User = Depends(require_super_admin_user),
    session: Session = Depends(get_db_session),
) -> None:
    """Delete a platform AI key (Super Admin only)."""
    ip, ua = _extract_request_meta(request)
    try:
        delete_platform_key(
            session=session,
            key_id=key_id,
            actor_user_id=admin.id,
            ip_address=ip,
            user_agent=ua,
        )
        session.commit()
    except PlatformKeyNotFoundError as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except Exception as exc:
        session.rollback()
        logger.exception("Unexpected error deleting platform AI key")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete platform AI key.",
        ) from exc


# ---------------------------------------------------------------------------
# Seed Catalogue Trigger
# ---------------------------------------------------------------------------

@router.post("/catalogue/seed", response_model=CatalogueSeedResponse)
def seed_catalogue_endpoint(
    request: Request,
    admin: User = Depends(require_super_admin_user),
    session: Session = Depends(get_db_session),
) -> CatalogueSeedResponse:
    """Trigger deterministic idempotent seed of the initial 20 AI providers (Super Admin only)."""
    ip, ua = _extract_request_meta(request)
    try:
        stats = seed_ai_provider_catalogue(
            session=session,
            actor_user_id=admin.id,
            ip_address=ip,
            user_agent=ua,
        )
        session.commit()
        return CatalogueSeedResponse(**stats)
    except Exception as exc:
        session.rollback()
        logger.exception("Unexpected error seeding AI provider catalogue")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to seed AI provider catalogue.",
        ) from exc
