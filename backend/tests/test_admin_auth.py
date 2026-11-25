from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.api.auth_dependencies import (
    require_admin_user,
    require_super_admin_user,
)
from backend.app.core.config import Settings
from backend.app.db.models import AdminAuditLog, CustomerProfile, User
from backend.app.security.passwords import hash_password, verify_password
from backend.app.security.tokens import create_access_token
from backend.app.services.admin_audit_service import (
    record_admin_audit_event,
    sanitize_audit_metadata,
)
from backend.scripts.create_admin import (
    AdminCreationError,
    create_or_promote_admin,
    main as cli_main,
)

PASSWORD = "test-password-12-chars-long"

admin_auth_test_router = APIRouter(prefix="/test")


@admin_auth_test_router.get("/admin-route")
def _admin_route(user: User = Depends(require_admin_user)):
    return {"status": "ok", "role": user.role, "email": user.email}


@admin_auth_test_router.get("/super-admin-route")
def _super_admin_route(user: User = Depends(require_super_admin_user)):
    return {"status": "ok", "role": user.role, "email": user.email}


def auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def create_user_with_role(
    session: Session,
    settings: Settings,
    email: str,
    role: str = "customer",
    is_active: bool = True,
    password: str = PASSWORD,
) -> tuple[User, str]:
    user = User(
        id=uuid4(),
        email=email.lower().strip(),
        password_hash=hash_password(password),
        role=role,
        is_active=is_active,
    )
    user.profile = CustomerProfile(display_name="Test User")
    session.add(user)
    session.commit()
    session.refresh(user)
    token = create_access_token(user.id, settings)
    return user, token


@pytest.fixture(autouse=True)
def setup_test_endpoints(api_client):
    """Register test endpoints to verify dependency enforcement over HTTP."""
    client, _ = api_client
    if not getattr(client.app.state, "_admin_test_routes_registered", False):
        client.app.include_router(admin_auth_test_router)
        client.app.state._admin_test_routes_registered = True


# ---------------------------------------------------------------------------
# 1. New public registrations always receive customer role
# ---------------------------------------------------------------------------
def test_new_public_registrations_always_receive_customer_role(api_client):
    client, _ = api_client
    res = client.post(
        "/api/v1/auth/register",
        json={
            "email": "customer-role-check@example.com",
            "password": PASSWORD,
            "display_name": "Customer User",
        },
    )
    assert res.status_code == 201
    data = res.json()
    assert data["role"] == "customer"

    session_factory = client.app.state.db_session_factory
    with session_factory() as session:
        user = session.get(User, UUID(data["id"]))
        assert user is not None
        assert user.role == "customer"


# ---------------------------------------------------------------------------
# 2. Registration cannot inject admin role
# ---------------------------------------------------------------------------
def test_registration_cannot_inject_admin_role(api_client):
    client, _ = api_client

    # Attempt to inject role=admin
    res_admin = client.post(
        "/api/v1/auth/register",
        json={
            "email": "hacker-admin@example.com",
            "password": PASSWORD,
            "display_name": "Hacker",
            "role": "admin",
        },
    )
    assert res_admin.status_code == 422

    # Attempt to inject role=super_admin
    res_super = client.post(
        "/api/v1/auth/register",
        json={
            "email": "hacker-super@example.com",
            "password": PASSWORD,
            "display_name": "Hacker Super",
            "role": "super_admin",
        },
    )
    assert res_super.status_code == 422


# ---------------------------------------------------------------------------
# 3. Customer cannot access admin dependency
# ---------------------------------------------------------------------------
def test_customer_cannot_access_admin_dependency(api_client):
    client, _ = api_client
    session_factory = client.app.state.db_session_factory
    settings = client.app.state.settings

    with session_factory() as session:
        _, token = create_user_with_role(session, settings, "customer1@example.com", role="customer")

    res = client.get("/test/admin-route", headers=auth_header(token))
    assert res.status_code == 403
    assert res.json()["detail"] == "Administrative privileges required."


# ---------------------------------------------------------------------------
# 4. Admin can access admin dependency
# ---------------------------------------------------------------------------
def test_admin_can_access_admin_dependency(api_client):
    client, _ = api_client
    session_factory = client.app.state.db_session_factory
    settings = client.app.state.settings

    with session_factory() as session:
        _, token = create_user_with_role(session, settings, "admin1@example.com", role="admin")

    res = client.get("/test/admin-route", headers=auth_header(token))
    assert res.status_code == 200
    assert res.json()["role"] == "admin"


# ---------------------------------------------------------------------------
# 5. Customer cannot access super-admin dependency
# ---------------------------------------------------------------------------
def test_customer_cannot_access_super_admin_dependency(api_client):
    client, _ = api_client
    session_factory = client.app.state.db_session_factory
    settings = client.app.state.settings

    with session_factory() as session:
        _, token = create_user_with_role(session, settings, "customer2@example.com", role="customer")

    res = client.get("/test/super-admin-route", headers=auth_header(token))
    assert res.status_code == 403
    assert res.json()["detail"] == "Super administrator privileges required."


# ---------------------------------------------------------------------------
# 6. Admin cannot access super-admin dependency
# ---------------------------------------------------------------------------
def test_admin_cannot_access_super_admin_dependency(api_client):
    client, _ = api_client
    session_factory = client.app.state.db_session_factory
    settings = client.app.state.settings

    with session_factory() as session:
        _, token = create_user_with_role(session, settings, "admin2@example.com", role="admin")

    res = client.get("/test/super-admin-route", headers=auth_header(token))
    assert res.status_code == 403
    assert res.json()["detail"] == "Super administrator privileges required."


# ---------------------------------------------------------------------------
# 7. Super admin can access both admin and super-admin dependencies
# ---------------------------------------------------------------------------
def test_super_admin_can_access_both_dependencies(api_client):
    client, _ = api_client
    session_factory = client.app.state.db_session_factory
    settings = client.app.state.settings

    with session_factory() as session:
        _, token = create_user_with_role(session, settings, "superadmin1@example.com", role="super_admin")

    # Access admin route
    res_admin = client.get("/test/admin-route", headers=auth_header(token))
    assert res_admin.status_code == 200
    assert res_admin.json()["role"] == "super_admin"

    # Access super-admin route
    res_super = client.get("/test/super-admin-route", headers=auth_header(token))
    assert res_super.status_code == 200
    assert res_super.json()["role"] == "super_admin"


# ---------------------------------------------------------------------------
# 8. Deactivated admin is rejected with 401
# ---------------------------------------------------------------------------
def test_deactivated_admin_is_rejected(api_client):
    client, _ = api_client
    session_factory = client.app.state.db_session_factory
    settings = client.app.state.settings

    with session_factory() as session:
        _, token = create_user_with_role(session, settings, "deactivated-admin@example.com", role="admin", is_active=False)

    res = client.get("/test/admin-route", headers=auth_header(token))
    assert res.status_code == 401
    assert res.json()["detail"] == "Authentication is required."


# ---------------------------------------------------------------------------
# 9. Database role changes take effect immediately even if JWT remains unchanged
# ---------------------------------------------------------------------------
def test_role_revocation_takes_effect_immediately(api_client):
    client, _ = api_client
    session_factory = client.app.state.db_session_factory
    settings = client.app.state.settings

    with session_factory() as session:
        user, token = create_user_with_role(session, settings, "revocable-admin@example.com", role="admin")

    # Step 1: Admin access works
    res_initial = client.get("/test/admin-route", headers=auth_header(token))
    assert res_initial.status_code == 200

    # Step 2: Demote user to customer directly in database
    with session_factory() as session:
        db_user = session.get(User, user.id)
        db_user.role = "customer"
        session.commit()

    # Step 3: Re-request with the exact same unexpired JWT -> immediately 403 Forbidden!
    res_revoked = client.get("/test/admin-route", headers=auth_header(token))
    assert res_revoked.status_code == 403
    assert res_revoked.json()["detail"] == "Administrative privileges required."


# ---------------------------------------------------------------------------
# 10. Invalid role is rejected by database constraints
# ---------------------------------------------------------------------------
def test_invalid_role_rejected_by_database_constraint(api_client):
    client, _ = api_client
    session_factory = client.app.state.db_session_factory

    with session_factory() as session:
        invalid_user = User(
            id=uuid4(),
            email="invalid-role@example.com",
            password_hash="mock-hash",
            role="unauthorized_role",
        )
        session.add(invalid_user)
        with pytest.raises(IntegrityError):
            session.commit()


# ---------------------------------------------------------------------------
# 11. First admin creation uses secure password hashing
# ---------------------------------------------------------------------------
def test_first_admin_creation_uses_secure_password_hashing(api_client):
    client, _ = api_client
    session_factory = client.app.state.db_session_factory

    with session_factory() as session:
        user, is_new = create_or_promote_admin(
            session,
            email="new-superadmin@example.com",
            password="SecureAdminPassword2026!",
            role="super_admin",
        )
        assert is_new is True
        assert user.role == "super_admin"
        assert user.password_hash.startswith("$argon2id$")
        assert user.password_hash != "SecureAdminPassword2026!"
        assert verify_password("SecureAdminPassword2026!", user.password_hash)


# ---------------------------------------------------------------------------
# 12. Existing customer promotion requires explicit confirmation
# ---------------------------------------------------------------------------
def test_customer_promotion_requires_explicit_confirmation(api_client):
    client, _ = api_client
    session_factory = client.app.state.db_session_factory
    settings = client.app.state.settings

    with session_factory() as session:
        user, _ = create_user_with_role(session, settings, "existing-cust@example.com", role="customer")

    # Attempt to promote without confirmation flag
    with session_factory() as session:
        with pytest.raises(AdminCreationError) as exc_info:
            create_or_promote_admin(
                session,
                email="existing-cust@example.com",
                password="NewAdminPassword123!",
                role="super_admin",
                confirm_promote=False,
            )
        assert "Explicit confirmation is required" in str(exc_info.value)

    # Confirm promotion explicitly
    with session_factory() as session:
        promoted, is_new = create_or_promote_admin(
            session,
            email="existing-cust@example.com",
            password="NewAdminPassword123!",
            role="super_admin",
            confirm_promote=True,
        )
        assert is_new is False
        assert promoted.role == "super_admin"
        assert verify_password("NewAdminPassword123!", promoted.password_hash)


# ---------------------------------------------------------------------------
# 13. No default password or secret is generated
# ---------------------------------------------------------------------------
def test_no_default_password_allowed(api_client):
    client, _ = api_client
    session_factory = client.app.state.db_session_factory

    with session_factory() as session:
        # Empty password rejected
        with pytest.raises(AdminCreationError):
            create_or_promote_admin(
                session,
                email="admin-no-pass@example.com",
                password="",
            )

        # Short password (< 12 chars) rejected
        with pytest.raises(AdminCreationError):
            create_or_promote_admin(
                session,
                email="admin-short-pass@example.com",
                password="short",
            )


# ---------------------------------------------------------------------------
# 14. Audit log records safe metadata
# ---------------------------------------------------------------------------
def test_audit_log_records_safe_metadata(api_client):
    client, _ = api_client
    session_factory = client.app.state.db_session_factory
    settings = client.app.state.settings

    with session_factory() as session:
        admin_user, _ = create_user_with_role(session, settings, "audit-actor@example.com", role="super_admin")
        target_id = str(uuid4())

        entry = record_admin_audit_event(
            session,
            actor_user_id=admin_user.id,
            action="USER_DEACTIVATED",
            target_type="user",
            target_id=target_id,
            metadata={"status": "suspended", "reason": "fraudulent_chargeback"},
            ip_address="192.168.1.100",
            user_agent="StoreFlowAdmin/1.0",
        )
        session.commit()

        # Query back from DB
        saved = session.get(AdminAuditLog, entry.id)
        assert saved is not None
        assert saved.actor_user_id == admin_user.id
        assert saved.action == "USER_DEACTIVATED"
        assert saved.target_type == "user"
        assert saved.target_id == target_id
        assert saved.metadata_json == {"status": "suspended", "reason": "fraudulent_chargeback"}
        assert saved.ip_address == "192.168.1.100"
        assert saved.created_at is not None


# ---------------------------------------------------------------------------
# 15. Audit log does not contain secrets
# ---------------------------------------------------------------------------
def test_audit_log_redacts_sensitive_keys(api_client):
    client, _ = api_client
    session_factory = client.app.state.db_session_factory

    dirty_metadata = {
        "user_email": "clean@example.com",
        "password": "CleartextSecretPassword123!",
        "api_key": "AIzaSySuperConfidentialKey",
        "auth_token": "bearer-secret-token",
        "user_prompt": "Confidential customer question",
        "key_hint": "9999",  # Safe exception
    }

    sanitized = sanitize_audit_metadata(dirty_metadata)
    assert sanitized["user_email"] == "clean@example.com"
    assert sanitized["password"] == "[REDACTED]"
    assert sanitized["api_key"] == "[REDACTED]"
    assert sanitized["auth_token"] == "[REDACTED]"
    assert sanitized["user_prompt"] == "[REDACTED]"
    assert sanitized["key_hint"] == "9999"

    with session_factory() as session:
        entry = record_admin_audit_event(
            session,
            actor_user_id=None,
            action="KEY_AUDIT",
            target_type="gemini_key",
            metadata=dirty_metadata,
        )
        session.commit()

        saved = session.get(AdminAuditLog, entry.id)
        assert saved is not None
        assert saved.metadata_json["password"] == "[REDACTED]"
        assert saved.metadata_json["api_key"] == "[REDACTED]"
        assert saved.metadata_json["key_hint"] == "9999"


# ---------------------------------------------------------------------------
# 16. Existing customer authentication remains compatible
# ---------------------------------------------------------------------------
def test_existing_customer_authentication_flow_compatible(api_client):
    client, _ = api_client

    # Registration
    reg = client.post(
        "/api/v1/auth/register",
        json={
            "email": "standard-customer@example.com",
            "password": PASSWORD,
            "display_name": "Standard Customer",
        },
    )
    assert reg.status_code == 201

    # Login
    login_res = client.post(
        "/api/v1/auth/login",
        json={"email": "standard-customer@example.com", "password": PASSWORD},
    )
    assert login_res.status_code == 200
    token = login_res.json()["access_token"]

    # Customer profile
    prof_res = client.get("/api/v1/customers/me/profile", headers=auth_header(token))
    assert prof_res.status_code == 200
    assert prof_res.json()["display_name"] == "Standard Customer"


# ---------------------------------------------------------------------------
# 17. CLI no longer accepts --password argument
# ---------------------------------------------------------------------------
def test_cli_rejects_password_argument():
    with pytest.raises(SystemExit) as exc:
        cli_main(["--email", "admin@example.com", "--password", "secret-password-12"])
    assert exc.value.code != 0


# ---------------------------------------------------------------------------
# 18. CLI prompts for password using getpass and hashes with Argon2id
# ---------------------------------------------------------------------------
def test_cli_uses_getpass_interactively(api_client, monkeypatch):
    client, _ = api_client
    session_factory = client.app.state.db_session_factory

    monkeypatch.setattr("getpass.getpass", lambda prompt="": "ValidAdminPassword123!")
    monkeypatch.setattr("backend.scripts.create_admin.create_session_factory", lambda engine: session_factory)

    exit_code = cli_main(["--email", "cli-admin@example.com", "--role", "super_admin"])
    assert exit_code == 0

    with session_factory() as session:
        user = session.scalar(select(User).where(User.email == "cli-admin@example.com"))
        assert user is not None
        assert user.role == "super_admin"
        assert verify_password("ValidAdminPassword123!", user.password_hash)


# ---------------------------------------------------------------------------
# 19. CLI password confirmation mismatch fails
# ---------------------------------------------------------------------------
def test_cli_password_mismatch_fails(monkeypatch):
    prompts = iter(["PasswordOne123!", "PasswordTwo123!"])
    monkeypatch.setattr("getpass.getpass", lambda prompt="": next(prompts))
    exit_code = cli_main(["--email", "mismatch-admin@example.com"])
    assert exit_code == 1


# ---------------------------------------------------------------------------
# 20. Long target IDs do not crash audit logging
# ---------------------------------------------------------------------------
def test_long_target_id_does_not_crash_audit_log(api_client):
    client, _ = api_client
    session_factory = client.app.state.db_session_factory

    long_id = "x" * 250  # Longer than 128 characters
    with session_factory() as session:
        entry = record_admin_audit_event(
            session,
            actor_user_id=None,
            action="LONG_ID_TEST",
            target_type="system_resource",
            target_id=long_id,
            metadata={"notes": "Testing oversized target ID persistence"},
        )
        session.commit()

        saved = session.get(AdminAuditLog, entry.id)
        assert saved is not None
        assert len(saved.target_id) <= 128
        assert saved.target_id == "x" * 128


# ---------------------------------------------------------------------------
# 21. Nested metadata secret values are redacted while ordinary text remains
# ---------------------------------------------------------------------------
def test_nested_metadata_secret_values_are_redacted(api_client):
    client, _ = api_client
    session_factory = client.app.state.db_session_factory

    metadata_with_innocent_keys = {
        "notes": "User supplied API key AIzaSyD-fake-key-with-thirty-five-chars-1234 in ticket.",
        "description": "Auth header used: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J_wStmWBWnQ_3eQ2mB8p_m7bB1_h8m6b8.",
        "ordinary_text": "This is a completely normal customer comment.",
        "key_hint": "4321",
        "key_source": "company",
        "nested_details": {
            "internal_comment": "Customer password hash was $argon2id$v=19$m=65536,t=3,p=4$fakehash123$fakehash456",
            "stored_payload": "Ciphertext is v1:gAAAAABfakefernetciphertexttokenwithmorethan20chars",
        },
        "audit_list": [
            "Normal log entry",
            "Nested API key: AIzaSyAnotherFakeGeminiApiKeyThirtyFiveCharsLong",
            {"step": "Raw JWT: eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J_wStmWBWnQ_3eQ2mB8p_m7bB1_h8m6b8"},
        ],
    }

    sanitized = sanitize_audit_metadata(metadata_with_innocent_keys)
    assert sanitized is not None

    # Secrets under innocent keys must be redacted
    assert "AIzaSyD" not in sanitized["notes"]
    assert "[REDACTED_SECRET]" in sanitized["notes"]
    assert "User supplied API key [REDACTED_SECRET] in ticket." == sanitized["notes"]

    assert "eyJhbGci" not in sanitized["description"]
    assert "[REDACTED_SECRET]" in sanitized["description"]

    # Argon2 and Ciphertext in nested dicts must be redacted
    assert "$argon2id$" not in sanitized["nested_details"]["internal_comment"]
    assert "[REDACTED_SECRET]" in sanitized["nested_details"]["internal_comment"]

    assert "v1:gAAAAAB" not in sanitized["nested_details"]["stored_payload"]
    assert "[REDACTED_SECRET]" in sanitized["nested_details"]["stored_payload"]

    # List items must be sanitized
    assert sanitized["audit_list"][0] == "Normal log entry"
    assert "[REDACTED_SECRET]" in sanitized["audit_list"][1]
    assert "AIzaSy" not in sanitized["audit_list"][1]
    assert "[REDACTED_SECRET]" in sanitized["audit_list"][2]["step"]

    # Ordinary text and safe exceptions must remain intact
    assert sanitized["ordinary_text"] == "This is a completely normal customer comment."
    assert sanitized["key_hint"] == "4321"
    assert sanitized["key_source"] == "company"

    with session_factory() as session:
        entry = record_admin_audit_event(
            session,
            actor_user_id=None,
            action="DEEP_SECRET_TEST",
            target_type="audit",
            metadata=metadata_with_innocent_keys,
        )
        session.commit()

        saved = session.get(AdminAuditLog, entry.id)
        assert saved is not None
        assert "AIza" not in str(saved.metadata_json)
        assert "eyJ" not in str(saved.metadata_json)
        assert "$argon2id$" not in str(saved.metadata_json)
        assert "v1:gAAAAAB" not in str(saved.metadata_json)
        assert saved.metadata_json["key_hint"] == "4321"
        assert saved.metadata_json["ordinary_text"] == "This is a completely normal customer comment."


# ---------------------------------------------------------------------------
# 22. Existing audit log indexes remain intact
# ---------------------------------------------------------------------------
def test_audit_log_indexes_intact():
    index_names = {idx.name for idx in AdminAuditLog.__table__.indexes}
    assert "ix_admin_audit_logs_actor_user_id" in index_names
    assert "ix_admin_audit_logs_action" in index_names
    assert "ix_admin_audit_logs_created_at" in index_names
    assert "ix_admin_audit_logs_actor_created" in index_names
    assert "ix_admin_audit_logs_action_created" in index_names

