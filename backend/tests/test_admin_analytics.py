from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.config import Settings
from backend.app.core.datetime_utils import utc_now
from backend.app.db.models import (
    AdminAuditLog,
    CustomerProfile,
    GeminiUsageEvent,
    Order,
    User,
    UserGeminiSettings,
)
from backend.app.security.passwords import hash_password
from backend.app.security.tokens import create_access_token
from backend.app.services.admin_audit_service import record_admin_audit_event

PASSWORD = "test-password-12-chars-long"


def auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def create_user_with_role(
    session: Session,
    settings: Settings,
    email: str,
    role: str = "customer",
    is_active: bool = True,
    password: str = PASSWORD,
    created_at: datetime | None = None,
) -> tuple[User, str]:
    user = User(
        id=uuid4(),
        email=email.lower().strip(),
        password_hash=hash_password(password),
        role=role,
        is_active=is_active,
    )
    if created_at is not None:
        user.created_at = created_at
    user.profile = CustomerProfile(display_name=f"User {email}")
    session.add(user)
    session.commit()
    session.refresh(user)
    token = create_access_token(user.id, settings)
    return user, token


def create_usage_event(
    session: Session,
    *,
    correlation_id: str,
    user_id: UUID | None,
    key_source: str = "COMPANY",
    model_name: str = "gemini-2.5-flash",
    attempt_number: int = 1,
    status: str = "SUCCESS",
    input_tokens: int | None = 100,
    output_tokens: int | None = 50,
    total_tokens: int | None = 150,
    latency_ms: float | None = 250.0,
    error_category: str | None = None,
    fallback_reason: str | None = None,
    created_at: datetime | None = None,
) -> GeminiUsageEvent:
    event = GeminiUsageEvent(
        id=uuid4(),
        correlation_id=correlation_id,
        user_id=user_id,
        provider="google",
        model_name=model_name,
        key_source=key_source,
        attempt_number=attempt_number,
        status=status,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        latency_ms=latency_ms,
        error_category=error_category,
        fallback_reason=fallback_reason,
        created_at=created_at or utc_now(),
    )
    session.add(event)
    session.commit()
    session.refresh(event)
    return event


# ---------------------------------------------------------------------------
# 1. Customer receives 403 from admin endpoints
# ---------------------------------------------------------------------------
def test_customer_receives_403_from_admin_endpoints(api_client):
    client, _ = api_client
    session_factory = client.app.state.db_session_factory
    settings = client.app.state.settings

    with session_factory() as session:
        _, token = create_user_with_role(session, settings, "unauthorized-cust@example.com", role="customer")

    endpoints = [
        "/api/v1/admin/overview",
        "/api/v1/admin/analytics/usage",
        "/api/v1/admin/analytics/models",
        "/api/v1/admin/analytics/sources",
        "/api/v1/admin/analytics/fallbacks",
        "/api/v1/admin/users",
        "/api/v1/admin/providers",
        "/api/v1/admin/providers/platform-keys",
        "/api/v1/admin/audit-logs",
    ]

    for ep in endpoints:
        res = client.get(ep, headers=auth_header(token))
        assert res.status_code == 403, f"Endpoint {ep} expected 403 but got {res.status_code}"


# ---------------------------------------------------------------------------
# 2. Admin can view overview
# ---------------------------------------------------------------------------
def test_admin_can_view_overview(api_client):
    client, _ = api_client
    session_factory = client.app.state.db_session_factory
    settings = client.app.state.settings

    with session_factory() as session:
        _, token = create_user_with_role(session, settings, "admin-overview@example.com", role="admin")

    res = client.get("/api/v1/admin/overview", headers=auth_header(token))
    assert res.status_code == 200
    data = res.json()
    assert "total_registered_users" in data
    assert "active_users" in data
    assert "total_logical_ai_requests" in data


# ---------------------------------------------------------------------------
# 3. Super admin can view overview
# ---------------------------------------------------------------------------
def test_super_admin_can_view_overview(api_client):
    client, _ = api_client
    session_factory = client.app.state.db_session_factory
    settings = client.app.state.settings

    with session_factory() as session:
        _, token = create_user_with_role(session, settings, "superadmin-overview@example.com", role="super_admin")

    res = client.get("/api/v1/admin/overview", headers=auth_header(token))
    assert res.status_code == 200
    data = res.json()
    assert data["total_registered_users"] >= 1


# ---------------------------------------------------------------------------
# 4. Total account count is accurate
# ---------------------------------------------------------------------------
def test_total_account_count_is_accurate(api_client):
    client, _ = api_client
    session_factory = client.app.state.db_session_factory
    settings = client.app.state.settings

    with session_factory() as session:
        create_user_with_role(session, settings, "u1@example.com", role="customer", is_active=True)
        create_user_with_role(session, settings, "u2@example.com", role="customer", is_active=True)
        create_user_with_role(session, settings, "u3@example.com", role="customer", is_active=False)
        _, admin_token = create_user_with_role(session, settings, "admin-count@example.com", role="admin", is_active=True)

    res = client.get("/api/v1/admin/overview", headers=auth_header(admin_token))
    assert res.status_code == 200
    data = res.json()
    assert data["total_registered_users"] == 4
    assert data["active_users"] == 3
    assert data["inactive_users"] == 1


# ---------------------------------------------------------------------------
# 5. New registration date filters are accurate
# ---------------------------------------------------------------------------
def test_new_registration_date_filters_are_accurate(api_client):
    client, _ = api_client
    session_factory = client.app.state.db_session_factory
    settings = client.app.state.settings
    now = utc_now()

    with session_factory() as session:
        # Today user
        create_user_with_role(session, settings, "today@example.com", created_at=now)
        # Yesterday user (different day, same month)
        create_user_with_role(session, settings, "yesterday@example.com", created_at=now - timedelta(days=1))
        # 40 days ago user (different month)
        create_user_with_role(session, settings, "old@example.com", created_at=now - timedelta(days=40))
        _, token = create_user_with_role(session, settings, "admin-dates@example.com", role="admin", created_at=now)

    res = client.get("/api/v1/admin/overview", headers=auth_header(token))
    assert res.status_code == 200
    data = res.json()
    assert data["new_users_today"] >= 2  # today + admin-dates
    assert data["new_users_this_month"] >= 3
    assert data["total_registered_users"] == 4


# ---------------------------------------------------------------------------
# 6. Logical requests differ correctly from provider attempts
# ---------------------------------------------------------------------------
def test_logical_requests_differ_from_provider_attempts(api_client):
    client, _ = api_client
    session_factory = client.app.state.db_session_factory
    settings = client.app.state.settings

    with session_factory() as session:
        admin_user, token = create_user_with_role(session, settings, "admin-ratio@example.com", role="admin")

        # 1 logical request with 3 attempts
        cid = f"req-{uuid4()}"
        create_usage_event(session, correlation_id=cid, user_id=admin_user.id, attempt_number=1, status="FAILED")
        create_usage_event(session, correlation_id=cid, user_id=admin_user.id, attempt_number=2, status="FAILED")
        create_usage_event(session, correlation_id=cid, user_id=admin_user.id, attempt_number=3, status="SUCCESS")

    res = client.get("/api/v1/admin/overview", headers=auth_header(token))
    assert res.status_code == 200
    data = res.json()
    assert data["total_logical_ai_requests"] == 1
    assert data["total_provider_attempts"] == 3


# ---------------------------------------------------------------------------
# 7. Personal and company attempts are separated
# ---------------------------------------------------------------------------
def test_personal_and_company_attempts_are_separated(api_client):
    client, _ = api_client
    session_factory = client.app.state.db_session_factory
    settings = client.app.state.settings

    with session_factory() as session:
        admin_user, token = create_user_with_role(session, settings, "admin-sources@example.com", role="admin")

        cid1 = f"req-{uuid4()}"
        cid2 = f"req-{uuid4()}"
        create_usage_event(session, correlation_id=cid1, user_id=admin_user.id, key_source="COMPANY")
        create_usage_event(session, correlation_id=cid1, user_id=admin_user.id, key_source="COMPANY")
        create_usage_event(session, correlation_id=cid2, user_id=admin_user.id, key_source="PERSONAL")

    res = client.get("/api/v1/admin/overview", headers=auth_header(token))
    assert res.status_code == 200
    data = res.json()
    assert data["company_provider_attempts"] == 2
    assert data["personal_provider_attempts"] == 1


# ---------------------------------------------------------------------------
# 8. Guest requests are counted
# ---------------------------------------------------------------------------
def test_guest_requests_are_counted(api_client):
    client, _ = api_client
    session_factory = client.app.state.db_session_factory
    settings = client.app.state.settings

    with session_factory() as session:
        admin_user, token = create_user_with_role(session, settings, "admin-guest@example.com", role="admin")

        # Guest request (user_id=None)
        cid_guest = f"guest-{uuid4()}"
        create_usage_event(session, correlation_id=cid_guest, user_id=None, key_source="COMPANY")

        # Authenticated request
        cid_user = f"user-{uuid4()}"
        create_usage_event(session, correlation_id=cid_user, user_id=admin_user.id, key_source="COMPANY")

    res = client.get("/api/v1/admin/overview", headers=auth_header(token))
    assert res.status_code == 200
    data = res.json()
    assert data["guest_ai_requests"] == 1
    assert data["total_logical_ai_requests"] == 2


# ---------------------------------------------------------------------------
# 9. Personal fallback is counted once as a logical request
# ---------------------------------------------------------------------------
def test_personal_fallback_counted_once_as_logical_request(api_client):
    client, _ = api_client
    session_factory = client.app.state.db_session_factory
    settings = client.app.state.settings

    with session_factory() as session:
        admin_user, token = create_user_with_role(session, settings, "admin-fallback@example.com", role="admin")

        cid = f"fallback-{uuid4()}"
        # Attempt 1: Personal failed with fallback
        create_usage_event(
            session,
            correlation_id=cid,
            user_id=admin_user.id,
            key_source="PERSONAL",
            attempt_number=1,
            status="COOLDOWN",
            fallback_reason="personal_key_quota_exhausted",
            error_category="QUOTA_EXHAUSTED",
        )
        # Attempt 2: Company recovered
        create_usage_event(
            session,
            correlation_id=cid,
            user_id=admin_user.id,
            key_source="COMPANY",
            attempt_number=2,
            status="SUCCESS",
        )

    res = client.get("/api/v1/admin/overview", headers=auth_header(token))
    assert res.status_code == 200
    data = res.json()
    assert data["total_logical_ai_requests"] == 1
    assert data["total_provider_attempts"] == 2
    assert data["personal_to_company_fallbacks"] == 1


# ---------------------------------------------------------------------------
# 10. Missing usage metadata stays NULL
# ---------------------------------------------------------------------------
def test_missing_usage_metadata_stays_null(api_client):
    client, _ = api_client
    session_factory = client.app.state.db_session_factory
    settings = client.app.state.settings

    with session_factory() as session:
        admin_user, token = create_user_with_role(session, settings, "admin-null-tokens@example.com", role="admin")
        cid = f"notokens-{uuid4()}"
        create_usage_event(
            session,
            correlation_id=cid,
            user_id=admin_user.id,
            input_tokens=None,
            output_tokens=None,
            total_tokens=None,
        )

    res = client.get("/api/v1/admin/overview", headers=auth_header(token))
    assert res.status_code == 200
    data = res.json()
    assert data["total_input_tokens"] is None
    assert data["total_output_tokens"] is None
    assert data["total_tokens"] is None


# ---------------------------------------------------------------------------
# 11. Per-user usage aggregation is correct
# ---------------------------------------------------------------------------
def test_per_user_usage_aggregation_is_correct(api_client):
    client, _ = api_client
    session_factory = client.app.state.db_session_factory
    settings = client.app.state.settings

    with session_factory() as session:
        admin_user, token = create_user_with_role(session, settings, "admin-detail@example.com", role="admin")
        target_user, _ = create_user_with_role(session, settings, "target-analytics@example.com", role="customer")

        cid1 = f"c1-{uuid4()}"
        cid2 = f"c2-{uuid4()}"
        create_usage_event(session, correlation_id=cid1, user_id=target_user.id, model_name="gemini-2.5-flash", total_tokens=100)
        create_usage_event(session, correlation_id=cid2, user_id=target_user.id, model_name="gemini-1.5-pro", total_tokens=200)

    res = client.get(f"/api/v1/admin/users/{target_user.id}", headers=auth_header(token))
    assert res.status_code == 200
    data = res.json()
    assert data["id"] == str(target_user.id)
    assert data["ai_summary"]["total_requests"] == 2
    assert data["ai_summary"]["total_tokens"] == 300
    assert len(data["model_breakdown"]) == 2


# ---------------------------------------------------------------------------
# 12. Search, pagination and filters work
# ---------------------------------------------------------------------------
def test_user_search_pagination_and_filters_work(api_client):
    client, _ = api_client
    session_factory = client.app.state.db_session_factory
    settings = client.app.state.settings

    with session_factory() as session:
        admin_user, token = create_user_with_role(session, settings, "admin-usersearch@example.com", role="admin")
        create_user_with_role(session, settings, "alice.smith@example.com", role="customer", is_active=True)
        create_user_with_role(session, settings, "bob.jones@example.com", role="customer", is_active=False)
        create_user_with_role(session, settings, "alice.walker@example.com", role="customer", is_active=True)

    # Search for alice
    res_search = client.get("/api/v1/admin/users?search=alice", headers=auth_header(token))
    assert res_search.status_code == 200
    data_search = res_search.json()
    assert data_search["total"] == 2

    # Filter inactive
    res_inactive = client.get("/api/v1/admin/users?is_active=false", headers=auth_header(token))
    assert res_inactive.status_code == 200
    data_inactive = res_inactive.json()
    assert data_inactive["total"] == 1
    assert data_inactive["items"][0]["email"] == "bob.jones@example.com"

    # Pagination
    res_page = client.get("/api/v1/admin/users?page_size=2&page=1", headers=auth_header(token))
    assert res_page.status_code == 200
    data_page = res_page.json()
    assert len(data_page["items"]) == 2
    assert data_page["total"] == 4
    assert data_page["total_pages"] == 2


# ---------------------------------------------------------------------------
# 13. Customer suspension is audited
# ---------------------------------------------------------------------------
def test_customer_suspension_is_audited(api_client):
    client, _ = api_client
    session_factory = client.app.state.db_session_factory
    settings = client.app.state.settings

    with session_factory() as session:
        admin_user, token = create_user_with_role(session, settings, "admin-suspender@example.com", role="admin")
        cust_user, _ = create_user_with_role(session, settings, "suspend-target@example.com", role="customer", is_active=True)

    res = client.patch(
        f"/api/v1/admin/users/{cust_user.id}/status",
        headers=auth_header(token),
        json={"is_active": False, "reason": "Suspected fraudulent behavior"},
    )
    assert res.status_code == 200
    assert res.json()["is_active"] is False

    with session_factory() as session:
        db_user = session.get(User, cust_user.id)
        assert db_user.is_active is False

        # Verify audit log
        logs = session.scalars(
            select(AdminAuditLog).where(
                AdminAuditLog.target_id == str(cust_user.id),
                AdminAuditLog.action == "USER_DEACTIVATED",
            )
        ).all()
        assert len(logs) == 1
        assert logs[0].actor_user_id == admin_user.id
        assert logs[0].metadata_json["reason"] == "Suspected fraudulent behavior"


# ---------------------------------------------------------------------------
# 14. Admin cannot suspend a super_admin
# ---------------------------------------------------------------------------
def test_admin_cannot_suspend_super_admin(api_client):
    client, _ = api_client
    session_factory = client.app.state.db_session_factory
    settings = client.app.state.settings

    with session_factory() as session:
        admin_user, admin_token = create_user_with_role(session, settings, "admin-rogue@example.com", role="admin")
        super_user, _ = create_user_with_role(session, settings, "super-target@example.com", role="super_admin")

    res = client.patch(
        f"/api/v1/admin/users/{super_user.id}/status",
        headers=auth_header(admin_token),
        json={"is_active": False},
    )
    assert res.status_code == 403
    assert "super administrator" in res.json()["detail"].lower()


# ---------------------------------------------------------------------------
# 15. Last active super_admin cannot be removed or deactivated
# ---------------------------------------------------------------------------
def test_last_active_super_admin_cannot_be_removed(api_client):
    client, _ = api_client
    session_factory = client.app.state.db_session_factory
    settings = client.app.state.settings

    with session_factory() as session:
        # Sole super admin
        super_user, super_token = create_user_with_role(session, settings, "sole-super@example.com", role="super_admin")

    # Attempt to deactivate sole super admin
    res_deact = client.patch(
        f"/api/v1/admin/users/{super_user.id}/status",
        headers=auth_header(super_token),
        json={"is_active": False},
    )
    assert res_deact.status_code == 400
    assert "last active super administrator" in res_deact.json()["detail"]

    # Attempt to demote sole super admin to customer
    res_demote = client.patch(
        f"/api/v1/admin/users/{super_user.id}/role",
        headers=auth_header(super_token),
        json={"role": "customer"},
    )
    assert res_demote.status_code == 400
    assert "last active super administrator" in res_demote.json()["detail"]


# ---------------------------------------------------------------------------
# 16. Personal key metadata is masked
# ---------------------------------------------------------------------------
def test_personal_key_metadata_is_masked(api_client):
    client, _ = api_client
    session_factory = client.app.state.db_session_factory
    settings = client.app.state.settings

    with session_factory() as session:
        admin_user, token = create_user_with_role(session, settings, "admin-maskcheck@example.com", role="admin")
        cust_user, _ = create_user_with_role(session, settings, "cust-with-key@example.com", role="customer")

        gemini_rec = UserGeminiSettings(
            user_id=cust_user.id,
            encrypted_api_key="v1:gAAAAABmockfernetciphertextsecretstringneverdisclose1234",
            key_hint="9876",
            status="ACTIVE",
            is_enabled=True,
        )
        session.add(gemini_rec)
        session.commit()

    res = client.get(f"/api/v1/admin/users/{cust_user.id}", headers=auth_header(token))
    assert res.status_code == 200
    data = res.json()
    assert data["gemini_settings"] is not None
    assert data["gemini_settings"]["key_hint"] == "9876"
    assert data["gemini_settings"]["status"] == "ACTIVE"


# ---------------------------------------------------------------------------
# 17. API key ciphertext never appears in API responses
# ---------------------------------------------------------------------------
def test_api_key_ciphertext_never_appears_in_api_responses(api_client):
    client, _ = api_client
    session_factory = client.app.state.db_session_factory
    settings = client.app.state.settings

    ciphertext = "v1:gAAAAABverysecretciphertextneverleakinresponses12345678"

    with session_factory() as session:
        admin_user, token = create_user_with_role(session, settings, "admin-crypto-audit@example.com", role="admin")
        cust_user, _ = create_user_with_role(session, settings, "cust-token-holder@example.com", role="customer")

        gemini_rec = UserGeminiSettings(
            user_id=cust_user.id,
            encrypted_api_key=ciphertext,
            key_hint="1234",
            status="ACTIVE",
            is_enabled=True,
        )
        session.add(gemini_rec)
        session.commit()

    res_list = client.get("/api/v1/admin/users", headers=auth_header(token))
    assert res_list.status_code == 200
    assert "gAAAAAB" not in res_list.text
    assert "ciphertext" not in res_list.text

    res_detail = client.get(f"/api/v1/admin/users/{cust_user.id}", headers=auth_header(token))
    assert res_detail.status_code == 200
    assert "gAAAAAB" not in res_detail.text


# ---------------------------------------------------------------------------
# 18. Audit metadata is sanitized
# ---------------------------------------------------------------------------
def test_audit_metadata_is_sanitized(api_client):
    client, _ = api_client
    session_factory = client.app.state.db_session_factory
    settings = client.app.state.settings

    with session_factory() as session:
        admin_user, token = create_user_with_role(session, settings, "admin-auditcheck@example.com", role="admin")

        record_admin_audit_event(
            session,
            actor_user_id=admin_user.id,
            action="TEST_SENSITIVE_AUDIT",
            target_type="config",
            metadata={
                "notes": "User key was FAKE_API_KEY_google_gemini_test_value_1234",
                "ordinary_field": "Safe configuration detail",
            },
        )
        session.commit()

    res = client.get("/api/v1/admin/audit-logs", headers=auth_header(token))
    assert res.status_code == 200
    data = res.json()
    assert len(data["items"]) >= 1
    item = next(i for i in data["items"] if i["action"] == "TEST_SENSITIVE_AUDIT")
    assert "FAKE_API_KEY" not in str(item["metadata_json"])
    assert "[REDACTED_SECRET]" in str(item["metadata_json"])
    assert item["metadata_json"]["ordinary_field"] == "Safe configuration detail"


# ---------------------------------------------------------------------------
# 19. Date range validation rejects invalid requests
# ---------------------------------------------------------------------------
def test_date_range_validation_rejects_invalid_requests(api_client):
    client, _ = api_client
    session_factory = client.app.state.db_session_factory
    settings = client.app.state.settings

    with session_factory() as session:
        _, token = create_user_with_role(session, settings, "admin-daterange@example.com", role="admin")

    # start_date > end_date
    res_inv = client.get(
        "/api/v1/admin/analytics/usage?start_date=2026-12-01T00:00:00Z&end_date=2026-11-01T00:00:00Z",
        headers=auth_header(token),
    )
    assert res_inv.status_code == 400
    assert "start_date must be earlier than or equal to end_date" in res_inv.json()["detail"]

    # Range > 90 days for daily
    res_too_long = client.get(
        "/api/v1/admin/analytics/usage?start_date=2026-01-01T00:00:00Z&end_date=2026-06-01T00:00:00Z",
        headers=auth_header(token),
    )
    assert res_too_long.status_code == 400
    assert "maximum allowed window" in res_too_long.json()["detail"]


# ---------------------------------------------------------------------------
# 20. Database failures and missing data are handled safely
# ---------------------------------------------------------------------------
def test_database_failures_and_missing_data_handled_safely(api_client):
    client, _ = api_client
    session_factory = client.app.state.db_session_factory
    settings = client.app.state.settings

    with session_factory() as session:
        _, token = create_user_with_role(session, settings, "admin-safefail@example.com", role="admin")

    # Non-existent user returns 404
    missing_id = uuid4()
    res_404 = client.get(f"/api/v1/admin/users/{missing_id}", headers=auth_header(token))
    assert res_404.status_code == 404
    assert "not found" in res_404.json()["detail"].lower()

    # Empty analytics returns empty lists without error
    res_models = client.get("/api/v1/admin/analytics/models", headers=auth_header(token))
    assert res_models.status_code == 200
    assert isinstance(res_models.json()["models"], list)

    res_fallbacks = client.get("/api/v1/admin/analytics/fallbacks", headers=auth_header(token))
    assert res_fallbacks.status_code == 200
    assert res_fallbacks.json()["total_fallbacks"] == 0


def test_platform_key_source_included_in_company_analytics_and_fallbacks(api_client):
    """Both COMPANY and PLATFORM_KEY telemetry rows count toward company pool metrics."""
    client, _ = api_client
    session_factory = client.app.state.db_session_factory
    settings = client.app.state.settings

    with session_factory() as session:
        admin_user, token = create_user_with_role(
            session, settings, "admin-platform-source@example.com", role="admin"
        )
        cid1 = f"plat-{uuid4()}"
        cid2 = f"plat-fb-{uuid4()}"
        create_usage_event(
            session,
            correlation_id=cid1,
            user_id=admin_user.id,
            model_name="llama-3.3-70b-versatile",
            key_source="PLATFORM_KEY",
            status="SUCCESS",
        )
        create_usage_event(
            session,
            correlation_id=cid2,
            user_id=admin_user.id,
            key_source="PERSONAL",
            attempt_number=1,
            status="COOLDOWN",
            fallback_reason="personal_key_quota_exhausted",
            error_category="QUOTA_EXHAUSTED",
        )
        create_usage_event(
            session,
            correlation_id=cid2,
            user_id=admin_user.id,
            key_source="PLATFORM_KEY",
            attempt_number=2,
            status="SUCCESS",
        )

    overview = client.get("/api/v1/admin/overview", headers=auth_header(token)).json()
    assert overview["company_provider_attempts"] == 2
    assert overview["personal_provider_attempts"] == 1
    assert overview["personal_to_company_fallbacks"] == 1
    assert overview["fallback_recovery_rate"] == 100.0

    user_detail = client.get(f"/api/v1/admin/users/{admin_user.id}", headers=auth_header(token)).json()
    assert user_detail["source_breakdown"]["company_attempts"] == 2

    sources = client.get("/api/v1/admin/analytics/sources", headers=auth_header(token)).json()
    company_metric = next(s for s in sources["sources"] if s["source"] == "company")
    assert company_metric["provider_attempts"] == 2
    assert all(t["source"] != "platform_key" for t in sources["trends"])

    fallbacks = client.get("/api/v1/admin/analytics/fallbacks", headers=auth_header(token)).json()
    assert fallbacks["total_fallbacks"] == 1
    assert fallbacks["successful_company_fallbacks"] == 1
    assert fallbacks["failed_fallbacks"] == 0

