from __future__ import annotations

from datetime import timedelta
from fastapi import APIRouter, Depends
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from backend.app.api.auth_dependencies import require_admin_user
from backend.app.core.datetime_utils import utc_now
from backend.app.db.models import GeminiUsageEvent, Order, User
from backend.app.db.session import get_db_session
from backend.app.schemas.admin import AdminOverviewResponse

router = APIRouter()


@router.get("/overview", response_model=AdminOverviewResponse)
def get_admin_overview(
    _admin: User = Depends(require_admin_user),
    session: Session = Depends(get_db_session),
) -> AdminOverviewResponse:
    """Return high-level StoreFlow administrative and AI telemetry metrics.

    Calculated entirely via database aggregations.
    """
    now = utc_now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=now.weekday())
    month_start = today_start.replace(day=1)

    # 1. User metrics
    user_stats = session.execute(
        select(
            func.count(User.id).label("total"),
            func.count(case((User.is_active.is_(True), User.id))).label("active"),
            func.count(case((User.is_active.is_(False), User.id))).label("inactive"),
            func.count(case((User.created_at >= today_start, User.id))).label("today"),
            func.count(case((User.created_at >= week_start, User.id))).label("week"),
            func.count(case((User.created_at >= month_start, User.id))).label("month"),
        )
    ).one()

    # 2. Total orders
    total_orders = session.scalar(select(func.count(Order.id))) or 0

    # 3. AI telemetry metrics
    ai_stats = session.execute(
        select(
            func.count(func.distinct(GeminiUsageEvent.correlation_id)).label("logical_requests"),
            func.count(GeminiUsageEvent.id).label("provider_attempts"),
            func.count(case((func.upper(GeminiUsageEvent.key_source) == "COMPANY", GeminiUsageEvent.id))).label("company_attempts"),
            func.count(case((func.upper(GeminiUsageEvent.key_source) == "PERSONAL", GeminiUsageEvent.id))).label("personal_attempts"),
            func.count(func.distinct(case((GeminiUsageEvent.user_id.is_(None), GeminiUsageEvent.correlation_id)))).label("guest_requests"),
            func.count(case((GeminiUsageEvent.status == "SUCCESS", GeminiUsageEvent.id))).label("success"),
            func.count(case((GeminiUsageEvent.status != "SUCCESS", GeminiUsageEvent.id))).label("failed"),
            func.sum(GeminiUsageEvent.input_tokens).label("input_tokens"),
            func.sum(GeminiUsageEvent.output_tokens).label("output_tokens"),
            func.sum(GeminiUsageEvent.total_tokens).label("total_tokens"),
            func.count(func.distinct(case((GeminiUsageEvent.fallback_reason.isnot(None), GeminiUsageEvent.correlation_id)))).label("fallbacks"),
        )
    ).one()

    return AdminOverviewResponse(
        total_registered_users=user_stats.total or 0,
        active_users=user_stats.active or 0,
        inactive_users=user_stats.inactive or 0,
        new_users_today=user_stats.today or 0,
        new_users_this_week=user_stats.week or 0,
        new_users_this_month=user_stats.month or 0,
        total_orders=total_orders,
        total_logical_ai_requests=ai_stats.logical_requests or 0,
        total_provider_attempts=ai_stats.provider_attempts or 0,
        company_provider_attempts=ai_stats.company_attempts or 0,
        personal_provider_attempts=ai_stats.personal_attempts or 0,
        guest_ai_requests=ai_stats.guest_requests or 0,
        successful_attempts=ai_stats.success or 0,
        failed_attempts=ai_stats.failed or 0,
        total_input_tokens=int(ai_stats.input_tokens) if ai_stats.input_tokens is not None else None,
        total_output_tokens=int(ai_stats.output_tokens) if ai_stats.output_tokens is not None else None,
        total_tokens=int(ai_stats.total_tokens) if ai_stats.total_tokens is not None else None,
        personal_to_company_fallbacks=ai_stats.fallbacks or 0,
    )
