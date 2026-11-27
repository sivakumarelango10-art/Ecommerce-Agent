from __future__ import annotations

from typing import Any
from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from backend.app.api.auth_dependencies import require_admin_user
from backend.app.db.models import User
from backend.app.db.session import get_db_session
from backend.app.schemas.admin import AdminProviderKeySlotItem, AdminProvidersResponse

router = APIRouter()


def _get_provider_payload(request: Request) -> AdminProvidersResponse:
    settings = getattr(request.app.state, "settings", None)
    configured_model = settings.gemini_model if settings else "gemini-3.8-flash"
    service = getattr(request.app.state, "gemini_service", None)

    slots: list[AdminProviderKeySlotItem] = []
    if service and hasattr(service, "get_platform_key_health"):
        health_data = service.get_platform_key_health()
        for item in health_data:
            slots.append(
                AdminProviderKeySlotItem(
                    slot_index=item["slot_index"],
                    slot_id=item["slot_id"],
                    key_id=item["key_id"],
                    masked_key=item["masked_key"],
                    quota_group=item["quota_group"],
                    is_active=item["is_active"],
                    in_cooldown=item["in_cooldown"],
                    cooldown_remaining_seconds=item["cooldown_remaining_seconds"],
                    health_status=item["health_status"],
                    failure_count=item["failure_count"],
                    last_used_at=item["last_used_at"],
                )
            )

    total_slots = len(slots)
    active_slots = sum(1 for s in slots if s.is_active)
    healthy_slots = sum(1 for s in slots if s.health_status == "HEALTHY")
    cooldown_slots = sum(1 for s in slots if s.health_status == "COOLDOWN")
    disabled_slots = sum(1 for s in slots if s.health_status == "DISABLED")

    return AdminProvidersResponse(
        provider="google",
        configured_model=configured_model,
        total_slots=total_slots,
        active_slots=active_slots,
        healthy_slots=healthy_slots,
        cooldown_slots=cooldown_slots,
        disabled_slots=disabled_slots,
        keys=slots,
    )


@router.get("/providers", response_model=AdminProvidersResponse)
def get_providers_overview(
    request: Request,
    _admin: User = Depends(require_admin_user),
) -> AdminProvidersResponse:
    """Safe read-only provider configuration and platform-managed Gemini pool overview."""
    return _get_provider_payload(request)


@router.get("/providers/platform-keys", response_model=AdminProvidersResponse)
def get_platform_keys(
    request: Request,
    _admin: User = Depends(require_admin_user),
) -> AdminProvidersResponse:
    """Safe read-only platform Gemini key pool slots, masked hints, and cooldown states."""
    return _get_provider_payload(request)
