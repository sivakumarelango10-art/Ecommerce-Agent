from __future__ import annotations

from datetime import datetime
import math
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.api.auth_dependencies import require_admin_user
from backend.app.core.datetime_utils import ensure_utc
from backend.app.db.models import AdminAuditLog, User
from backend.app.db.session import get_db_session
from backend.app.schemas.admin import AdminAuditLogItem, AdminAuditLogResponse
from backend.app.services.admin_audit_service import sanitize_audit_metadata

router = APIRouter()


@router.get("/audit-logs", response_model=AdminAuditLogResponse)
def get_admin_audit_logs(
    actor_id: UUID | None = Query(None, description="Filter by actor user ID"),
    action: str | None = Query(None, description="Filter by action name"),
    target_type: str | None = Query(None, description="Filter by target type"),
    start_date: datetime | None = Query(None, description="Filter by start timestamp"),
    end_date: datetime | None = Query(None, description="Filter by end timestamp"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page (max 100)"),
    _admin: User = Depends(require_admin_user),
    session: Session = Depends(get_db_session),
) -> AdminAuditLogResponse:
    """Query administrative and security audit events with sanitized metadata."""
    filters = []
    if actor_id:
        filters.append(AdminAuditLog.actor_user_id == actor_id)
    if action:
        filters.append(AdminAuditLog.action == action.strip())
    if target_type:
        filters.append(AdminAuditLog.target_type == target_type.strip())
    if start_date:
        filters.append(AdminAuditLog.created_at >= ensure_utc(start_date))
    if end_date:
        filters.append(AdminAuditLog.created_at <= ensure_utc(end_date))

    # Total count
    total_count = session.scalar(
        select(func.count(AdminAuditLog.id)).where(*filters)
    ) or 0

    total_pages = math.ceil(total_count / page_size) if total_count > 0 else 0
    offset = (page - 1) * page_size

    # Query items joining User for actor email
    stmt = (
        select(AdminAuditLog, User.email.label("actor_email"))
        .outerjoin(User, User.id == AdminAuditLog.actor_user_id)
        .where(*filters)
        .order_by(AdminAuditLog.created_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    rows = session.execute(stmt).all()

    items = []
    for log_entry, actor_email in rows:
        # Re-apply strict sanitization at the API response boundary
        clean_metadata = sanitize_audit_metadata(log_entry.metadata_json)
        items.append(
            AdminAuditLogItem(
                id=log_entry.id,
                actor_user_id=log_entry.actor_user_id,
                actor_email=actor_email,
                action=log_entry.action,
                target_type=log_entry.target_type,
                target_id=log_entry.target_id,
                metadata_json=clean_metadata,
                ip_address=log_entry.ip_address,
                user_agent=log_entry.user_agent,
                created_at=log_entry.created_at,
            )
        )

    return AdminAuditLogResponse(
        items=items,
        total=total_count,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )
