from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Overview Schemas
# ---------------------------------------------------------------------------
class AdminOverviewResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    total_registered_users: int
    active_users: int
    inactive_users: int
    new_users_today: int
    new_users_this_week: int
    new_users_this_month: int
    total_orders: int
    total_logical_ai_requests: int
    total_provider_attempts: int
    company_provider_attempts: int
    personal_provider_attempts: int
    guest_ai_requests: int
    successful_attempts: int
    failed_attempts: int
    total_input_tokens: int | None = None
    total_output_tokens: int | None = None
    total_tokens: int | None = None
    personal_to_company_fallbacks: int


# ---------------------------------------------------------------------------
# Analytics Schemas
# ---------------------------------------------------------------------------
class AdminAnalyticsUsagePoint(BaseModel):
    period: str
    logical_requests: int
    provider_attempts: int
    company_attempts: int
    personal_attempts: int
    guest_requests: int
    successful_attempts: int
    failed_attempts: int
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    fallbacks: int


class AdminAnalyticsUsageResponse(BaseModel):
    group_by: str
    start_date: datetime | None = None
    end_date: datetime | None = None
    data: list[AdminAnalyticsUsagePoint]


class AdminModelMetric(BaseModel):
    model_name: str
    provider_attempts: int
    logical_requests: int
    successful_attempts: int
    failed_attempts: int
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    avg_latency_ms: float | None = None


class AdminModelsResponse(BaseModel):
    start_date: datetime | None = None
    end_date: datetime | None = None
    models: list[AdminModelMetric]


class AdminSourceMetric(BaseModel):
    source: str
    provider_attempts: int
    logical_requests: int
    successful_attempts: int
    failed_attempts: int
    total_tokens: int | None = None


class AdminSourcesResponse(BaseModel):
    start_date: datetime | None = None
    end_date: datetime | None = None
    sources: list[AdminSourceMetric]
    trends: list[dict[str, Any]] = Field(default_factory=list)


class AdminFallbacksResponse(BaseModel):
    start_date: datetime | None = None
    end_date: datetime | None = None
    total_fallbacks: int
    personal_quota_failures: int
    invalid_personal_credentials: int
    temporary_provider_failures: int
    successful_company_fallbacks: int
    failed_fallbacks: int
    breakdown_by_reason: list[dict[str, Any]] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# User Management Schemas
# ---------------------------------------------------------------------------
class AdminUserListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    role: str
    is_active: bool
    created_at: datetime
    display_name: str | None = None
    total_requests: int = 0
    total_attempts: int = 0
    company_attempts: int = 0
    personal_attempts: int = 0
    total_tokens: int | None = None
    last_ai_activity: datetime | None = None
    gemini_key_status: str | None = None
    gemini_key_hint: str | None = None
    gemini_key_enabled: bool | None = None
    gemini_cooldown_until: datetime | None = None


class AdminUserListResponse(BaseModel):
    items: list[AdminUserListItem]
    total: int
    page: int
    page_size: int
    total_pages: int


class AdminUserDetailResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    role: str
    is_active: bool
    created_at: datetime
    display_name: str | None = None
    phone: str | None = None
    ai_summary: dict[str, Any]
    model_breakdown: list[dict[str, Any]]
    source_breakdown: dict[str, Any]
    recent_activity: list[dict[str, Any]]
    gemini_settings: dict[str, Any] | None = None


class AdminUserStatusUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_active: bool
    reason: str | None = None


class AdminUserRoleUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str = Field(..., pattern=r"^(customer|admin|super_admin)$")
    reason: str | None = None


# ---------------------------------------------------------------------------
# Provider & Health Schemas
# ---------------------------------------------------------------------------
class AdminProviderKeySlotItem(BaseModel):
    slot_index: int
    slot_id: str
    key_id: str
    masked_key: str
    quota_group: str
    is_active: bool
    in_cooldown: bool
    cooldown_remaining_seconds: float
    health_status: str
    failure_count: int
    last_used_at: datetime | None = None


class AdminProvidersResponse(BaseModel):
    provider: str = "google"
    configured_model: str
    total_slots: int
    active_slots: int
    healthy_slots: int
    cooldown_slots: int
    disabled_slots: int
    keys: list[AdminProviderKeySlotItem]


# ---------------------------------------------------------------------------
# Audit Log Schemas
# ---------------------------------------------------------------------------
class AdminAuditLogItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    actor_user_id: UUID | None
    actor_email: str | None = None
    action: str
    target_type: str
    target_id: str | None
    metadata_json: dict[str, Any] | None
    ip_address: str | None
    user_agent: str | None
    created_at: datetime


class AdminAuditLogResponse(BaseModel):
    items: list[AdminAuditLogItem]
    total: int
    page: int
    page_size: int
    total_pages: int
