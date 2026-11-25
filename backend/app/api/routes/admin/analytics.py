from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from backend.app.api.auth_dependencies import require_admin_user
from backend.app.core.datetime_utils import ensure_utc, utc_now
from backend.app.db.models import GeminiUsageEvent, User
from backend.app.db.session import get_db_session
from backend.app.schemas.admin import (
    AdminAnalyticsUsagePoint,
    AdminAnalyticsUsageResponse,
    AdminFallbacksResponse,
    AdminModelMetric,
    AdminModelsResponse,
    AdminSourceMetric,
    AdminSourcesResponse,
)

router = APIRouter()


def _validate_date_range(
    start_date: datetime | None,
    end_date: datetime | None,
    max_days: int = 90,
) -> tuple[datetime | None, datetime | None]:
    """Validate and normalize start/end dates."""
    start_utc = ensure_utc(start_date) if start_date else None
    end_utc = ensure_utc(end_date) if end_date else None

    if start_utc and end_utc and start_utc > end_utc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="start_date must be earlier than or equal to end_date.",
        )

    if start_utc and end_utc:
        diff = end_utc - start_utc
        if diff > timedelta(days=max_days):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Requested date range exceeds the maximum allowed window of {max_days} days.",
            )

    return start_utc, end_utc


def _get_period_column(session: Session, group_by: str):
    """Dialect-compatible date formatting column for SQLite and PostgreSQL."""
    dialect_name = session.bind.dialect.name if session.bind else "sqlite"
    if dialect_name == "postgresql":
        if group_by == "hourly":
            return func.to_char(GeminiUsageEvent.created_at, "YYYY-MM-DD HH24:00:00")
        return func.to_char(GeminiUsageEvent.created_at, "YYYY-MM-DD")
    else:
        # SQLite
        if group_by == "hourly":
            return func.strftime("%Y-%m-%d %H:00:00", GeminiUsageEvent.created_at)
        return func.strftime("%Y-%m-%d", GeminiUsageEvent.created_at)


@router.get("/analytics/usage", response_model=AdminAnalyticsUsageResponse)
def get_usage_analytics(
    start_date: datetime | None = Query(None, description="Start date filter (UTC)"),
    end_date: datetime | None = Query(None, description="End date filter (UTC)"),
    group_by: str = Query("daily", pattern=r"^(daily|hourly)$"),
    _admin: User = Depends(require_admin_user),
    session: Session = Depends(get_db_session),
) -> AdminAnalyticsUsageResponse:
    """Grouped AI request volume, token consumption, and success rates over time."""
    max_days = 7 if group_by == "hourly" else 90
    start_utc, end_utc = _validate_date_range(start_date, end_date, max_days=max_days)

    period_col = _get_period_column(session, group_by).label("period")

    stmt = select(
        period_col,
        func.count(func.distinct(GeminiUsageEvent.correlation_id)).label("logical_requests"),
        func.count(GeminiUsageEvent.id).label("provider_attempts"),
        func.count(case((func.upper(GeminiUsageEvent.key_source) == "COMPANY", GeminiUsageEvent.id))).label("company_attempts"),
        func.count(case((func.upper(GeminiUsageEvent.key_source) == "PERSONAL", GeminiUsageEvent.id))).label("personal_attempts"),
        func.count(func.distinct(case((GeminiUsageEvent.user_id.is_(None), GeminiUsageEvent.correlation_id)))).label("guest_requests"),
        func.count(case((GeminiUsageEvent.status == "SUCCESS", GeminiUsageEvent.id))).label("successful_attempts"),
        func.count(case((GeminiUsageEvent.status != "SUCCESS", GeminiUsageEvent.id))).label("failed_attempts"),
        func.sum(GeminiUsageEvent.input_tokens).label("input_tokens"),
        func.sum(GeminiUsageEvent.output_tokens).label("output_tokens"),
        func.sum(GeminiUsageEvent.total_tokens).label("total_tokens"),
        func.count(func.distinct(case((GeminiUsageEvent.fallback_reason.isnot(None), GeminiUsageEvent.correlation_id)))).label("fallbacks"),
    )

    if start_utc:
        stmt = stmt.where(GeminiUsageEvent.created_at >= start_utc)
    if end_utc:
        stmt = stmt.where(GeminiUsageEvent.created_at <= end_utc)

    stmt = stmt.group_by(period_col).order_by(period_col.asc())
    rows = session.execute(stmt).all()

    points = [
        AdminAnalyticsUsagePoint(
            period=row.period or "unknown",
            logical_requests=row.logical_requests or 0,
            provider_attempts=row.provider_attempts or 0,
            company_attempts=row.company_attempts or 0,
            personal_attempts=row.personal_attempts or 0,
            guest_requests=row.guest_requests or 0,
            successful_attempts=row.successful_attempts or 0,
            failed_attempts=row.failed_attempts or 0,
            input_tokens=int(row.input_tokens) if row.input_tokens is not None else None,
            output_tokens=int(row.output_tokens) if row.output_tokens is not None else None,
            total_tokens=int(row.total_tokens) if row.total_tokens is not None else None,
            fallbacks=row.fallbacks or 0,
        )
        for row in rows
    ]

    return AdminAnalyticsUsageResponse(
        group_by=group_by,
        start_date=start_utc,
        end_date=end_utc,
        data=points,
    )


@router.get("/analytics/models", response_model=AdminModelsResponse)
def get_models_analytics(
    start_date: datetime | None = Query(None),
    end_date: datetime | None = Query(None),
    _admin: User = Depends(require_admin_user),
    session: Session = Depends(get_db_session),
) -> AdminModelsResponse:
    """Model-wise performance, volume, token consumption, and latency breakdown."""
    start_utc, end_utc = _validate_date_range(start_date, end_date)

    stmt = select(
        GeminiUsageEvent.model_name,
        func.count(GeminiUsageEvent.id).label("provider_attempts"),
        func.count(func.distinct(GeminiUsageEvent.correlation_id)).label("logical_requests"),
        func.count(case((GeminiUsageEvent.status == "SUCCESS", GeminiUsageEvent.id))).label("successful_attempts"),
        func.count(case((GeminiUsageEvent.status != "SUCCESS", GeminiUsageEvent.id))).label("failed_attempts"),
        func.sum(GeminiUsageEvent.input_tokens).label("input_tokens"),
        func.sum(GeminiUsageEvent.output_tokens).label("output_tokens"),
        func.sum(GeminiUsageEvent.total_tokens).label("total_tokens"),
        func.avg(GeminiUsageEvent.latency_ms).label("avg_latency_ms"),
    )

    if start_utc:
        stmt = stmt.where(GeminiUsageEvent.created_at >= start_utc)
    if end_utc:
        stmt = stmt.where(GeminiUsageEvent.created_at <= end_utc)

    stmt = stmt.group_by(GeminiUsageEvent.model_name).order_by(func.count(GeminiUsageEvent.id).desc())
    rows = session.execute(stmt).all()

    models = [
        AdminModelMetric(
            model_name=row.model_name,
            provider_attempts=row.provider_attempts or 0,
            logical_requests=row.logical_requests or 0,
            successful_attempts=row.successful_attempts or 0,
            failed_attempts=row.failed_attempts or 0,
            input_tokens=int(row.input_tokens) if row.input_tokens is not None else None,
            output_tokens=int(row.output_tokens) if row.output_tokens is not None else None,
            total_tokens=int(row.total_tokens) if row.total_tokens is not None else None,
            avg_latency_ms=round(float(row.avg_latency_ms), 1) if row.avg_latency_ms is not None else None,
        )
        for row in rows
    ]

    return AdminModelsResponse(
        start_date=start_utc,
        end_date=end_utc,
        models=models,
    )


@router.get("/analytics/sources", response_model=AdminSourcesResponse)
def get_sources_analytics(
    start_date: datetime | None = Query(None),
    end_date: datetime | None = Query(None),
    _admin: User = Depends(require_admin_user),
    session: Session = Depends(get_db_session),
) -> AdminSourcesResponse:
    """Usage volume and tokens split by key source (COMPANY, PERSONAL, GUEST)."""
    start_utc, end_utc = _validate_date_range(start_date, end_date)

    base_query = select(
        func.count(GeminiUsageEvent.id).label("provider_attempts"),
        func.count(func.distinct(GeminiUsageEvent.correlation_id)).label("logical_requests"),
        func.count(case((GeminiUsageEvent.status == "SUCCESS", GeminiUsageEvent.id))).label("successful_attempts"),
        func.count(case((GeminiUsageEvent.status != "SUCCESS", GeminiUsageEvent.id))).label("failed_attempts"),
        func.sum(GeminiUsageEvent.total_tokens).label("total_tokens"),
    )

    if start_utc:
        base_query = base_query.where(GeminiUsageEvent.created_at >= start_utc)
    if end_utc:
        base_query = base_query.where(GeminiUsageEvent.created_at <= end_utc)

    # 1. Company source
    company_row = session.execute(
        base_query.where(func.upper(GeminiUsageEvent.key_source) == "COMPANY")
    ).one()

    # 2. Personal source
    personal_row = session.execute(
        base_query.where(func.upper(GeminiUsageEvent.key_source) == "PERSONAL")
    ).one()

    # 3. Guest requests
    guest_row = session.execute(
        base_query.where(GeminiUsageEvent.user_id.is_(None))
    ).one()

    sources = [
        AdminSourceMetric(
            source="company",
            provider_attempts=company_row.provider_attempts or 0,
            logical_requests=company_row.logical_requests or 0,
            successful_attempts=company_row.successful_attempts or 0,
            failed_attempts=company_row.failed_attempts or 0,
            total_tokens=int(company_row.total_tokens) if company_row.total_tokens is not None else None,
        ),
        AdminSourceMetric(
            source="personal",
            provider_attempts=personal_row.provider_attempts or 0,
            logical_requests=personal_row.logical_requests or 0,
            successful_attempts=personal_row.successful_attempts or 0,
            failed_attempts=personal_row.failed_attempts or 0,
            total_tokens=int(personal_row.total_tokens) if personal_row.total_tokens is not None else None,
        ),
        AdminSourceMetric(
            source="guest",
            provider_attempts=guest_row.provider_attempts or 0,
            logical_requests=guest_row.logical_requests or 0,
            successful_attempts=guest_row.successful_attempts or 0,
            failed_attempts=guest_row.failed_attempts or 0,
            total_tokens=int(guest_row.total_tokens) if guest_row.total_tokens is not None else None,
        ),
    ]

    # Trends by source over time
    period_col = _get_period_column(session, "daily").label("period")
    trend_stmt = select(
        period_col,
        func.upper(GeminiUsageEvent.key_source).label("key_source"),
        func.count(GeminiUsageEvent.id).label("attempts"),
        func.count(func.distinct(GeminiUsageEvent.correlation_id)).label("logical_requests"),
    )
    if start_utc:
        trend_stmt = trend_stmt.where(GeminiUsageEvent.created_at >= start_utc)
    if end_utc:
        trend_stmt = trend_stmt.where(GeminiUsageEvent.created_at <= end_utc)
    trend_stmt = trend_stmt.group_by(period_col, func.upper(GeminiUsageEvent.key_source)).order_by(period_col.asc())
    trend_rows = session.execute(trend_stmt).all()

    trends = [
        {
            "period": r.period,
            "source": str(r.key_source).lower(),
            "attempts": r.attempts or 0,
            "logical_requests": r.logical_requests or 0,
        }
        for r in trend_rows
    ]

    return AdminSourcesResponse(
        start_date=start_utc,
        end_date=end_utc,
        sources=sources,
        trends=trends,
    )


@router.get("/analytics/fallbacks", response_model=AdminFallbacksResponse)
def get_fallbacks_analytics(
    start_date: datetime | None = Query(None),
    end_date: datetime | None = Query(None),
    _admin: User = Depends(require_admin_user),
    session: Session = Depends(get_db_session),
) -> AdminFallbacksResponse:
    """Detailed analytics on personal key failovers and company pool recovery."""
    start_utc, end_utc = _validate_date_range(start_date, end_date)

    # Base filter
    conds = []
    if start_utc:
        conds.append(GeminiUsageEvent.created_at >= start_utc)
    if end_utc:
        conds.append(GeminiUsageEvent.created_at <= end_utc)

    # Subquery of correlation_ids that experienced a fallback
    fallback_filter = (
        (GeminiUsageEvent.fallback_reason.isnot(None))
        | (GeminiUsageEvent.error_category == "QUOTA_EXHAUSTED")
        | (GeminiUsageEvent.error_category == "INVALID_CREDENTIAL")
    )
    fallback_correlations = (
        select(GeminiUsageEvent.correlation_id)
        .where(fallback_filter, *conds)
        .distinct()
        .subquery()
    )

    total_fallbacks = (
        session.scalar(
            select(func.count()).select_from(fallback_correlations)
        )
        or 0
    )

    # Breakdown by category
    quota_count = session.scalar(
        select(func.count(func.distinct(GeminiUsageEvent.correlation_id)))
        .where(
            (GeminiUsageEvent.error_category == "QUOTA_EXHAUSTED")
            | (GeminiUsageEvent.fallback_reason.ilike("%quota%")),
            *conds,
        )
    ) or 0

    invalid_count = session.scalar(
        select(func.count(func.distinct(GeminiUsageEvent.correlation_id)))
        .where(
            (GeminiUsageEvent.error_category == "INVALID_CREDENTIAL")
            | (GeminiUsageEvent.fallback_reason.ilike("%invalid%")),
            *conds,
        )
    ) or 0

    transient_count = session.scalar(
        select(func.count(func.distinct(GeminiUsageEvent.correlation_id)))
        .where(
            GeminiUsageEvent.error_category.in_(["TEMPORARY_FAILURE", "TIMEOUT"])
            | (GeminiUsageEvent.fallback_reason.ilike("%transient%"))
            | (GeminiUsageEvent.fallback_reason.ilike("%timeout%")),
            *conds,
        )
    ) or 0

    # Successful company fallbacks: correlation_id in fallback_correlations with a company SUCCESS event
    success_fallbacks = session.scalar(
        select(func.count(func.distinct(GeminiUsageEvent.correlation_id)))
        .where(
            GeminiUsageEvent.correlation_id.in_(select(fallback_correlations)),
            func.upper(GeminiUsageEvent.key_source) == "COMPANY",
            GeminiUsageEvent.status == "SUCCESS",
            *conds,
        )
    ) or 0

    failed_fallbacks = max(0, total_fallbacks - success_fallbacks)

    # Grouping by fallback_reason string
    reason_stmt = (
        select(
            GeminiUsageEvent.fallback_reason,
            func.count(func.distinct(GeminiUsageEvent.correlation_id)).label("count"),
            func.max(GeminiUsageEvent.created_at).label("last_occurred"),
        )
        .where(GeminiUsageEvent.fallback_reason.isnot(None), *conds)
        .group_by(GeminiUsageEvent.fallback_reason)
        .order_by(func.count(func.distinct(GeminiUsageEvent.correlation_id)).desc())
    )
    reason_rows = session.execute(reason_stmt).all()

    breakdown = [
        {
            "reason": r.fallback_reason,
            "count": r.count or 0,
            "last_occurred": r.last_occurred.isoformat() if r.last_occurred else None,
        }
        for r in reason_rows
    ]

    return AdminFallbacksResponse(
        start_date=start_utc,
        end_date=end_utc,
        total_fallbacks=total_fallbacks,
        personal_quota_failures=quota_count,
        invalid_personal_credentials=invalid_count,
        temporary_provider_failures=transient_count,
        successful_company_fallbacks=success_fallbacks,
        failed_fallbacks=failed_fallbacks,
        breakdown_by_reason=breakdown,
    )
