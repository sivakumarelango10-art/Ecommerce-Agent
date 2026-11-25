from __future__ import annotations

import math
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from backend.app.api.auth_dependencies import (
    require_admin_user,
    require_super_admin_user,
)
from backend.app.db.models import (
    CustomerProfile,
    GeminiUsageEvent,
    User,
    UserGeminiSettings,
)
from backend.app.db.session import get_db_session
from backend.app.schemas.admin import (
    AdminUserDetailResponse,
    AdminUserListItem,
    AdminUserListResponse,
    AdminUserRoleUpdateRequest,
    AdminUserStatusUpdateRequest,
)
from backend.app.services.admin_audit_service import record_admin_audit_event

router = APIRouter()


@router.get("/users", response_model=AdminUserListResponse)
def list_users(
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page (max 100)"),
    search: str | None = Query(None, description="Search by email prefix/substring"),
    role: str | None = Query(None, pattern=r"^(customer|admin|super_admin)$"),
    is_active: bool | None = Query(None),
    _admin: User = Depends(require_admin_user),
    session: Session = Depends(get_db_session),
) -> AdminUserListResponse:
    """Paginated user directory with aggregated AI usage and personal key status."""
    # 1. Base filter conditions
    filters = []
    if search:
        clean_search = f"%{search.strip().lower()}%"
        filters.append(func.lower(User.email).like(clean_search))
    if role:
        filters.append(User.role == role)
    if is_active is not None:
        filters.append(User.is_active == is_active)

    # 2. Total count
    total_count = session.scalar(
        select(func.count(User.id)).where(*filters)
    ) or 0

    total_pages = math.ceil(total_count / page_size) if total_count > 0 else 0
    offset = (page - 1) * page_size

    # 3. Aggregated AI telemetry subquery per user
    ai_subq = (
        select(
            GeminiUsageEvent.user_id.label("uid"),
            func.count(func.distinct(GeminiUsageEvent.correlation_id)).label("total_requests"),
            func.count(GeminiUsageEvent.id).label("total_attempts"),
            func.count(case((func.upper(GeminiUsageEvent.key_source) == "COMPANY", GeminiUsageEvent.id))).label("company_attempts"),
            func.count(case((func.upper(GeminiUsageEvent.key_source) == "PERSONAL", GeminiUsageEvent.id))).label("personal_attempts"),
            func.sum(GeminiUsageEvent.total_tokens).label("total_tokens"),
            func.max(GeminiUsageEvent.created_at).label("last_ai_activity"),
        )
        .where(GeminiUsageEvent.user_id.isnot(None))
        .group_by(GeminiUsageEvent.user_id)
        .subquery()
    )

    # 4. Main query joining CustomerProfile, UserGeminiSettings, and ai_subq
    query = (
        select(
            User,
            CustomerProfile.display_name,
            UserGeminiSettings.status.label("gemini_status"),
            UserGeminiSettings.key_hint.label("gemini_key_hint"),
            UserGeminiSettings.is_enabled.label("gemini_key_enabled"),
            UserGeminiSettings.cooldown_until.label("gemini_cooldown_until"),
            ai_subq.c.total_requests,
            ai_subq.c.total_attempts,
            ai_subq.c.company_attempts,
            ai_subq.c.personal_attempts,
            ai_subq.c.total_tokens,
            ai_subq.c.last_ai_activity,
        )
        .outerjoin(CustomerProfile, CustomerProfile.user_id == User.id)
        .outerjoin(UserGeminiSettings, UserGeminiSettings.user_id == User.id)
        .outerjoin(ai_subq, ai_subq.c.uid == User.id)
        .where(*filters)
        .order_by(User.created_at.desc())
        .offset(offset)
        .limit(page_size)
    )

    rows = session.execute(query).all()

    items = [
        AdminUserListItem(
            id=row.User.id,
            email=row.User.email,
            role=row.User.role,
            is_active=row.User.is_active,
            created_at=row.User.created_at,
            display_name=row.display_name,
            total_requests=row.total_requests or 0,
            total_attempts=row.total_attempts or 0,
            company_attempts=row.company_attempts or 0,
            personal_attempts=row.personal_attempts or 0,
            total_tokens=int(row.total_tokens) if row.total_tokens is not None else None,
            last_ai_activity=row.last_ai_activity,
            gemini_key_status=row.gemini_status,
            gemini_key_hint=row.gemini_key_hint,
            gemini_key_enabled=row.gemini_key_enabled,
            gemini_cooldown_until=row.gemini_cooldown_until,
        )
        for row in rows
    ]

    return AdminUserListResponse(
        items=items,
        total=total_count,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.get("/users/{user_id}", response_model=AdminUserDetailResponse)
def get_user_detail(
    user_id: UUID,
    _admin: User = Depends(require_admin_user),
    session: Session = Depends(get_db_session),
) -> AdminUserDetailResponse:
    """Detailed user profile, AI token consumption, model breakdown, and recent activity."""
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with ID '{user_id}' not found.",
        )

    profile = session.scalar(select(CustomerProfile).where(CustomerProfile.user_id == user_id))
    gemini_settings = session.scalar(select(UserGeminiSettings).where(UserGeminiSettings.user_id == user_id))

    # Aggregated AI statistics
    ai_summary_row = session.execute(
        select(
            func.count(func.distinct(GeminiUsageEvent.correlation_id)).label("total_requests"),
            func.count(GeminiUsageEvent.id).label("total_attempts"),
            func.count(case((func.upper(GeminiUsageEvent.key_source) == "COMPANY", GeminiUsageEvent.id))).label("company_attempts"),
            func.count(case((func.upper(GeminiUsageEvent.key_source) == "PERSONAL", GeminiUsageEvent.id))).label("personal_attempts"),
            func.sum(GeminiUsageEvent.input_tokens).label("input_tokens"),
            func.sum(GeminiUsageEvent.output_tokens).label("output_tokens"),
            func.sum(GeminiUsageEvent.total_tokens).label("total_tokens"),
            func.max(GeminiUsageEvent.created_at).label("last_ai_activity"),
        ).where(GeminiUsageEvent.user_id == user_id)
    ).one()

    ai_summary = {
        "total_requests": ai_summary_row.total_requests or 0,
        "total_attempts": ai_summary_row.total_attempts or 0,
        "company_attempts": ai_summary_row.company_attempts or 0,
        "personal_attempts": ai_summary_row.personal_attempts or 0,
        "input_tokens": int(ai_summary_row.input_tokens) if ai_summary_row.input_tokens is not None else None,
        "output_tokens": int(ai_summary_row.output_tokens) if ai_summary_row.output_tokens is not None else None,
        "total_tokens": int(ai_summary_row.total_tokens) if ai_summary_row.total_tokens is not None else None,
        "last_ai_activity": ai_summary_row.last_ai_activity.isoformat() if ai_summary_row.last_ai_activity else None,
    }

    # Model breakdown
    model_rows = session.execute(
        select(
            GeminiUsageEvent.model_name,
            func.count(GeminiUsageEvent.id).label("attempts"),
            func.sum(GeminiUsageEvent.total_tokens).label("tokens"),
        )
        .where(GeminiUsageEvent.user_id == user_id)
        .group_by(GeminiUsageEvent.model_name)
    ).all()

    model_breakdown = [
        {
            "model_name": r.model_name,
            "attempts": r.attempts or 0,
            "tokens": int(r.tokens) if r.tokens is not None else None,
        }
        for r in model_rows
    ]

    # Source breakdown
    source_breakdown = {
        "company_attempts": ai_summary_row.company_attempts or 0,
        "personal_attempts": ai_summary_row.personal_attempts or 0,
    }

    # Recent activity (up to 10 events)
    recent_events = session.scalars(
        select(GeminiUsageEvent)
        .where(GeminiUsageEvent.user_id == user_id)
        .order_by(GeminiUsageEvent.created_at.desc())
        .limit(10)
    ).all()

    recent_activity = [
        {
            "correlation_id": ev.correlation_id,
            "model_name": ev.model_name,
            "key_source": ev.key_source,
            "status": ev.status,
            "error_category": ev.error_category,
            "total_tokens": ev.total_tokens,
            "latency_ms": ev.latency_ms,
            "created_at": ev.created_at.isoformat(),
        }
        for ev in recent_events
    ]

    # Safe personal key metadata (Strictly excludes ciphertext)
    safe_settings = None
    if gemini_settings:
        safe_settings = {
            "status": gemini_settings.status,
            "key_hint": gemini_settings.key_hint,
            "is_enabled": gemini_settings.is_enabled,
            "cooldown_until": gemini_settings.cooldown_until.isoformat() if gemini_settings.cooldown_until else None,
            "last_validated_at": gemini_settings.last_validated_at.isoformat() if gemini_settings.last_validated_at else None,
            "last_used_at": gemini_settings.last_used_at.isoformat() if gemini_settings.last_used_at else None,
        }

    return AdminUserDetailResponse(
        id=user.id,
        email=user.email,
        role=user.role,
        is_active=user.is_active,
        created_at=user.created_at,
        display_name=profile.display_name if profile else None,
        phone=profile.phone if profile else None,
        ai_summary=ai_summary,
        model_breakdown=model_breakdown,
        source_breakdown=source_breakdown,
        recent_activity=recent_activity,
        gemini_settings=safe_settings,
    )


@router.patch("/users/{user_id}/status")
def update_user_status(
    user_id: UUID,
    payload: AdminUserStatusUpdateRequest,
    current_admin: User = Depends(require_admin_user),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Activate or deactivate a user account with RBAC guards and audit logging."""
    target_user = session.get(User, user_id)
    if target_user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with ID '{user_id}' not found.",
        )

    # RBAC Guard 1: Admins cannot suspend or modify super_admin accounts
    if target_user.role == "super_admin" and current_admin.role != "super_admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrative privileges insufficient: only super administrators may modify super administrator accounts.",
        )

    # RBAC Guard 2: Require super_admin to modify admin accounts
    if target_user.role == "admin" and current_admin.role != "super_admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Super administrative privileges required to modify administrative accounts.",
        )

    # RBAC Guard 3: Prevent deactivating the last active super_admin
    if target_user.role == "super_admin" and not payload.is_active:
        active_super_admins = session.scalar(
            select(func.count(User.id)).where(User.role == "super_admin", User.is_active.is_(True))
        ) or 0
        if active_super_admins <= 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot deactivate the last active super administrator account.",
            )

    old_status = target_user.is_active
    target_user.is_active = payload.is_active

    action = "USER_ACTIVATED" if payload.is_active else "USER_DEACTIVATED"
    record_admin_audit_event(
        session,
        actor_user_id=current_admin.id,
        action=action,
        target_type="user",
        target_id=str(target_user.id),
        metadata={
            "old_status": old_status,
            "new_status": payload.is_active,
            "email": target_user.email,
            "role": target_user.role,
            "reason": payload.reason,
        },
    )

    session.commit()
    session.refresh(target_user)

    return {
        "status": "ok",
        "user_id": str(target_user.id),
        "email": target_user.email,
        "is_active": target_user.is_active,
        "action": action,
    }


@router.patch("/users/{user_id}/role")
def update_user_role(
    user_id: UUID,
    payload: AdminUserRoleUpdateRequest,
    current_super_admin: User = Depends(require_super_admin_user),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Change user role. Super admin privileges strictly required."""
    target_user = session.get(User, user_id)
    if target_user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with ID '{user_id}' not found.",
        )

    new_role = payload.role.strip().lower()
    if new_role not in ("customer", "admin", "super_admin"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid role: '{new_role}'.",
        )

    # Self-modification guard
    if target_user.id == current_super_admin.id and new_role != "super_admin":
        active_super_admins = session.scalar(
            select(func.count(User.id)).where(User.role == "super_admin", User.is_active.is_(True))
        ) or 0
        if active_super_admins <= 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot demote the last active super administrator account.",
            )

    # Last active super_admin removal/demotion guard
    if target_user.role == "super_admin" and new_role != "super_admin":
        active_super_admins = session.scalar(
            select(func.count(User.id)).where(User.role == "super_admin", User.is_active.is_(True))
        ) or 0
        if active_super_admins <= 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot demote the last active super administrator account.",
            )

    old_role = target_user.role
    target_user.role = new_role

    record_admin_audit_event(
        session,
        actor_user_id=current_super_admin.id,
        action="USER_ROLE_CHANGED",
        target_type="user",
        target_id=str(target_user.id),
        metadata={
            "old_role": old_role,
            "new_role": new_role,
            "email": target_user.email,
            "reason": payload.reason,
        },
    )

    session.commit()
    session.refresh(target_user)

    return {
        "status": "ok",
        "user_id": str(target_user.id),
        "email": target_user.email,
        "old_role": old_role,
        "new_role": target_user.role,
    }
