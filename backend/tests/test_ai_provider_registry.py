from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import json
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.core.config import Settings
from backend.app.db.models import (
    AdminAuditLog,
    AIModel,
    AIProvider,
    CustomerProfile,
    ModelAccessClass,
    PlatformAIKey,
    PlatformKeyStatus,
    ProviderConnectionStatus,
    User,
)
from backend.app.db.seed_ai_catalogue import (
    INITIAL_PROVIDERS_DATA,
    RETIRED_GEMINI_MODEL_IDENTIFIERS,
    seed_ai_provider_catalogue,
)
from backend.app.schemas.ai_provider import (
    AIModelCreate,
    AIProviderCreate,
    PlatformAIKeyCreate,
)
from backend.app.security.key_encryption import (
    KeyEncryptionError,
    decrypt_platform_key,
    decrypt_user_key,
    encrypt_platform_key,
    encrypt_user_key,
    mask_platform_key,
    mask_user_key,
)
from backend.app.security.passwords import hash_password
from backend.app.security.tokens import create_access_token
from backend.app.services.ai_provider_service import (
    AIProviderServiceError,
    SSRFSecurityError,
    create_model,
    create_platform_key,
    create_provider,
    validate_provider_base_url,
)

PLATFORM_TEST_SECRET = SecretStr("test-platform-encryption-secret-32-bytes-long!")
USER_TEST_SECRET = SecretStr("test-user-encryption-secret-32-bytes-long-12345")
ALT_TEST_SECRET = SecretStr("alt-test-encryption-secret-32-bytes-long-987654321")


def auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def create_user_with_role(
    session: Session,
    settings: Settings,
    email: str,
    role: str = "customer",
) -> tuple[User, str]:
    user = User(
        id=uuid4(),
        email=email.lower().strip(),
        password_hash=hash_password("Password123456!"),
        role=role,
        is_active=True,
    )
    user.profile = CustomerProfile(display_name=f"{role.capitalize()} User")
    session.add(user)
    session.commit()
    session.refresh(user)
    token = create_access_token(user.id, settings)
    return user, token


@pytest.fixture(autouse=True)
def configure_platform_key_secret(api_client):
    client, _ = api_client
    client.app.state.settings.platform_key_encryption_secret = PLATFORM_TEST_SECRET
    client.app.state.settings.user_key_encryption_secret = USER_TEST_SECRET


# ---------------------------------------------------------------------------
# 1. Provider model creation and constraints
# ---------------------------------------------------------------------------
def test_provider_model_creation_and_constraints(api_client):
    client, _ = api_client
    session_factory = client.app.state.db_session_factory

    with session_factory() as session:
        provider = AIProvider(
            code="test_provider",
            display_name="Test Provider",
            adapter_type="custom_adapter",
            base_url="https://api.testprovider.com/v1",
            documentation_url="https://docs.testprovider.com",
            is_enabled=True,
            priority=50,
            connection_status=ProviderConnectionStatus.CONFIGURED.value,
        )
        session.add(provider)
        session.commit()
        session.refresh(provider)

        assert provider.id is not None
        assert provider.code == "test_provider"
        assert provider.priority == 50
        assert provider.connection_status == "CONFIGURED"

        # Invalid connection status violates check constraint
        invalid_provider = AIProvider(
            code="invalid_provider",
            display_name="Invalid Provider",
            connection_status="NON_EXISTENT_STATUS",
        )
        session.add(invalid_provider)
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()


# ---------------------------------------------------------------------------
# 2. Duplicate provider code rejected
# ---------------------------------------------------------------------------
def test_duplicate_provider_code_rejected(api_client):
    client, _ = api_client
    session_factory = client.app.state.db_session_factory

    with session_factory() as session:
        p1 = AIProvider(code="unique_code", display_name="Provider 1")
        session.add(p1)
        session.commit()

        # Case-insensitive duplicate
        p2 = AIProvider(code="UNIQUE_CODE", display_name="Provider 2")
        session.add(p2)
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()


# ---------------------------------------------------------------------------
# 3. Seed catalogue creates 20 providers idempotently
# ---------------------------------------------------------------------------
def test_seed_catalogue_idempotent_20_providers(api_client):
    client, _ = api_client
    session_factory = client.app.state.db_session_factory

    with session_factory() as session:
        # First seed run
        stats1 = seed_ai_provider_catalogue(session)
        session.commit()

        assert stats1["providers_created"] == 20
        assert stats1["models_created"] >= 2

        count = len(list(session.scalars(select(AIProvider)).all()))
        assert count == 20

        # Second seed run (must be idempotent)
        stats2 = seed_ai_provider_catalogue(session)
        session.commit()

        assert stats2["providers_created"] == 0
        assert stats2["providers_updated"] == 20

        total_after = len(list(session.scalars(select(AIProvider)).all()))
        assert total_after == 20


# ---------------------------------------------------------------------------
# 4. Seed catalogue initial provider statuses
# ---------------------------------------------------------------------------
def test_seed_catalogue_initial_status_and_stable_models(api_client):
    client, _ = api_client
    session_factory = client.app.state.db_session_factory

    with session_factory() as session:
        seed_ai_provider_catalogue(session)
        session.commit()

        gemini = session.scalar(select(AIProvider).where(AIProvider.code == "google_gemini"))
        assert gemini is not None
        assert gemini.connection_status == ProviderConnectionStatus.AVAILABLE.value

        other_providers = session.scalars(select(AIProvider).where(AIProvider.code != "google_gemini")).all()
        assert len(other_providers) == 19
        for p in other_providers:
            assert p.connection_status == ProviderConnectionStatus.PLANNED.value

        # Gemini baseline models
        assert len(gemini.models) >= 2
        model_identifiers = {m.model_identifier for m in gemini.models}
        assert "gemini-3.8-flash" in model_identifiers
        assert "gemini-3.5-flash-lite" in model_identifiers
        assert "gemini-2.5-flash" not in model_identifiers
        assert "gemini-2.5-pro" not in model_identifiers
        assert "gemini-2.0-flash" not in model_identifiers


# ---------------------------------------------------------------------------
# 5. Retired Gemini models are not seeded as active (Phase 6E.1.2)
# ---------------------------------------------------------------------------
def test_retired_gemini_models_not_seeded_as_active(api_client):
    client, _ = api_client
    session_factory = client.app.state.db_session_factory

    with session_factory() as session:
        # Simulate an existing database where retired Gemini models were previously present (1.5, 2.0, 2.5)
        gemini = AIProvider(
            code="google_gemini",
            display_name="Google Gemini",
            adapter_type="google_gemini",
            connection_status=ProviderConnectionStatus.AVAILABLE.value,
        )
        session.add(gemini)
        session.flush()

        retired_15_flash = AIModel(
            provider_id=gemini.id,
            model_identifier="gemini-1.5-flash",
            display_name="Gemini 1.5 Flash (Legacy)",
            is_enabled=True,
            is_default=True,
        )
        retired_25_flash = AIModel(
            provider_id=gemini.id,
            model_identifier="gemini-2.5-flash",
            display_name="Gemini 2.5 Flash (Legacy)",
            is_enabled=True,
            is_default=False,
        )
        retired_20_flash = AIModel(
            provider_id=gemini.id,
            model_identifier="gemini-2.0-flash",
            display_name="Gemini 2.0 Flash (Legacy)",
            is_enabled=True,
            is_default=False,
        )
        session.add_all([retired_15_flash, retired_25_flash, retired_20_flash])
        session.commit()

        # Run catalogue seed
        seed_ai_provider_catalogue(session)
        session.commit()

        # Check retired models are now inactive and not default
        session.refresh(retired_15_flash)
        session.refresh(retired_25_flash)
        session.refresh(retired_20_flash)

        assert retired_15_flash.is_enabled is False
        assert retired_15_flash.is_default is False
        assert "RETIRED" in retired_15_flash.description

        assert retired_25_flash.is_enabled is False
        assert retired_25_flash.is_default is False
        assert "RETIRED" in retired_25_flash.description

        assert retired_20_flash.is_enabled is False
        assert retired_20_flash.is_default is False
        assert "RETIRED" in retired_20_flash.description

        # Verify newly added stable models are active
        active_models = session.scalars(
            select(AIModel).where(AIModel.provider_id == gemini.id, AIModel.is_enabled.is_(True))
        ).all()
        active_identifiers = {m.model_identifier for m in active_models}
        assert "gemini-3.8-flash" in active_identifiers
        assert "gemini-3.5-flash-lite" in active_identifiers
        for retired_id in RETIRED_GEMINI_MODEL_IDENTIFIERS:
            assert retired_id not in active_identifiers


# ---------------------------------------------------------------------------
# 6. Current stable model identifiers are seeded correctly (Phase 6E.1.2)
# ---------------------------------------------------------------------------
def test_current_stable_model_identifiers_seeded_correctly(api_client):
    client, _ = api_client
    session_factory = client.app.state.db_session_factory

    with session_factory() as session:
        seed_ai_provider_catalogue(session)
        session.commit()

        gemini = session.scalar(select(AIProvider).where(AIProvider.code == "google_gemini"))
        assert gemini is not None

        stable_models = {m.model_identifier: m for m in gemini.models}
        assert "gemini-3.8-flash" in stable_models
        assert "gemini-3.5-flash-lite" in stable_models
        assert "gemini-2.5-flash" not in stable_models
        assert "gemini-2.5-pro" not in stable_models
        assert "gemini-2.0-flash" not in stable_models

        flash_38 = stable_models["gemini-3.8-flash"]
        assert flash_38.is_enabled is True
        assert flash_38.is_default is True
        assert flash_38.pricing_input_per_million is None
        assert flash_38.pricing_output_per_million is None
        assert flash_38.pricing_url == "https://ai.google.dev/pricing"
        assert flash_38.terms_url == "https://ai.google.dev/gemini-api/terms"
        assert flash_38.last_pricing_verified_at is None
        assert flash_38.access_class == ModelAccessClass.FREE_TIER.value
        assert flash_38.free_tier_limit_metadata is not None
        assert flash_38.free_tier_limit_metadata["is_free_tier_available"] is True
        assert flash_38.free_tier_limit_metadata["has_unlimited_access"] is False

        lite_35 = stable_models["gemini-3.5-flash-lite"]
        assert lite_35.is_enabled is True
        assert lite_35.is_default is False
        assert lite_35.pricing_input_per_million is None
        assert lite_35.pricing_output_per_million is None
        assert lite_35.pricing_url == "https://ai.google.dev/pricing"
        assert lite_35.terms_url == "https://ai.google.dev/gemini-api/terms"
        assert lite_35.last_pricing_verified_at is None
        assert lite_35.access_class == ModelAccessClass.FREE_TIER.value
        assert lite_35.free_tier_limit_metadata is not None
        assert lite_35.free_tier_limit_metadata["is_free_tier_available"] is True
        assert lite_35.free_tier_limit_metadata["has_unlimited_access"] is False


# ---------------------------------------------------------------------------
# 7. Existing disabled model state survives repeated seeding (Phase 6E.1.2)
# ---------------------------------------------------------------------------
def test_existing_disabled_model_state_survives_repeated_seeding(api_client):
    client, _ = api_client
    session_factory = client.app.state.db_session_factory

    with session_factory() as session:
        seed_ai_provider_catalogue(session)
        session.commit()

        # Admin deliberately disables gemini-3.5-flash-lite
        gemini = session.scalar(select(AIProvider).where(AIProvider.code == "google_gemini"))
        lite_model = session.scalar(
            select(AIModel).where(AIModel.provider_id == gemini.id, AIModel.model_identifier == "gemini-3.5-flash-lite")
        )
        assert lite_model is not None
        lite_model.is_enabled = False
        session.commit()

        # Re-run seed
        seed_ai_provider_catalogue(session)
        session.commit()

        session.refresh(lite_model)
        # Admin disable choice must survive!
        assert lite_model.is_enabled is False


# ---------------------------------------------------------------------------
# 8. Existing default-model selection survives repeated seeding (Phase 6E.1.2)
# ---------------------------------------------------------------------------
def test_existing_default_model_selection_survives_repeated_seeding(api_client):
    client, _ = api_client
    session_factory = client.app.state.db_session_factory

    with session_factory() as session:
        seed_ai_provider_catalogue(session)
        session.commit()

        gemini = session.scalar(select(AIProvider).where(AIProvider.code == "google_gemini"))

        # Admin selects gemini-3.5-flash-lite as the custom default instead of gemini-3.8-flash
        flash_38 = session.scalar(
            select(AIModel).where(AIModel.provider_id == gemini.id, AIModel.model_identifier == "gemini-3.8-flash")
        )
        lite_35 = session.scalar(
            select(AIModel).where(AIModel.provider_id == gemini.id, AIModel.model_identifier == "gemini-3.5-flash-lite")
        )
        flash_38.is_default = False
        lite_35.is_default = True
        session.commit()

        # Re-run seed
        seed_ai_provider_catalogue(session)
        session.commit()

        session.refresh(flash_38)
        session.refresh(lite_35)

        # Admin custom default selection must NOT be reset!
        assert lite_35.is_default is True
        assert flash_38.is_default is False


# ---------------------------------------------------------------------------
# 9. Official provider hosts are accepted (Phase 6E.1.1 Scenario 5)
# ---------------------------------------------------------------------------
def test_official_provider_hosts_accepted():
    for item in INITIAL_PROVIDERS_DATA:
        code = item["code"]
        base_url = item["base_url"]
        if base_url:
            validated = validate_provider_base_url(base_url, provider_code=code)
            assert validated == base_url


# ---------------------------------------------------------------------------
# 10. Lookalike domains are rejected (Phase 6E.1.1 Scenario 6)
# ---------------------------------------------------------------------------
def test_lookalike_domains_rejected():
    lookalike_cases = [
        ("openai", "https://api.openai.com.attacker.com/v1"),
        ("openai", "https://evil-openai.com/v1"),
        ("openai", "https://openai.com/v1"),  # Must be api.openai.com
        ("groq", "https://api.groq.com.fake.io/openai/v1"),
        ("openrouter", "https://openrouter.ai.malicious.net/api/v1"),
        ("google_gemini", "https://generativelanguage.googleapis.com.phish.org/v1"),
        ("aws_bedrock", "https://bedrock-runtime.attacker.com/v1"),
        # Provider mismatch (trying to point openai to groq's domain)
        ("openai", "https://api.groq.com/openai/v1"),
    ]

    for provider_code, bad_url in lookalike_cases:
        with pytest.raises(SSRFSecurityError, match="not an authorized endpoint"):
            validate_provider_base_url(bad_url, provider_code=provider_code)


# ---------------------------------------------------------------------------
# 11. Unexpected ports and URL credentials rejected (Phase 6E.1.1 Scenario 7)
# ---------------------------------------------------------------------------
def test_unexpected_ports_and_url_credentials_rejected():
    # URL credentials
    with pytest.raises(SSRFSecurityError, match="credentials"):
        validate_provider_base_url("https://admin:password@api.openai.com/v1", provider_code="openai")

    # Non-standard port
    with pytest.raises(SSRFSecurityError, match="unexpected port"):
        validate_provider_base_url("https://api.openai.com:8443/v1", provider_code="openai")

    # Insecure HTTP port 80
    with pytest.raises(SSRFSecurityError, match="HTTPS scheme"):
        validate_provider_base_url("http://api.openai.com:80/v1", provider_code="openai")


# ---------------------------------------------------------------------------
# 12. Private or metadata endpoints rejected (Phase 6E.1.1 Scenario 8)
# ---------------------------------------------------------------------------
def test_private_or_metadata_endpoints_rejected():
    dangerous_urls = [
        "https://localhost/v1",
        "https://127.0.0.1/v1",
        "https://[::1]/v1",
        "https://169.254.169.254/latest/meta-data",
        "https://metadata.google.internal/computeMetadata/v1",
        "https://10.0.1.2/v1",
        "https://172.16.0.1/v1",
        "https://192.168.1.1/v1",
        "https://service.local/v1",
        "https://internal.company.internal/v1",
    ]
    for url in dangerous_urls:
        with pytest.raises(SSRFSecurityError):
            validate_provider_base_url(url)


# ---------------------------------------------------------------------------
# 13. Encryption configuration fails safely when required (Phase 6E.1.1 Scenario 9)
# ---------------------------------------------------------------------------
def test_encryption_configuration_fails_safely_when_required(api_client):
    client, _ = api_client
    session_factory = client.app.state.db_session_factory

    with session_factory() as session:
        provider = AIProvider(code="enc_fail_safe_provider", display_name="Enc Fail Safe")
        session.add(provider)
        session.commit()
        provider_id = provider.id

        _, super_token = create_user_with_role(
            session=session,
            settings=client.app.state.settings,
            email="super_enc_safe@example.com",
            role="super_admin",
        )

    # 1. Temporarily unset platform_key_encryption_secret on server settings
    client.app.state.settings.platform_key_encryption_secret = None

    headers = auth_header(super_token)
    res = client.post(
        "/api/v1/admin/ai/keys",
        headers=headers,
        json={
            "provider_id": str(provider_id),
            "api_key": "sk-test-fail-safe-key-123",
            "quota_group": "group",
        },
    )
    # Must fail safely with 500 rather than falling back or storing unencrypted
    assert res.status_code == 500
    assert "encryption secret" in res.json()["detail"].lower()

    # 2. Service level validation: passing empty or None secret raises AIProviderServiceError
    with session_factory() as session:
        with pytest.raises(AIProviderServiceError, match="encryption secret is not configured"):
            create_platform_key(
                session=session,
                payload=PlatformAIKeyCreate(
                    provider_id=provider_id,
                    api_key=SecretStr("sk-any"),
                    quota_group="qg",
                ),
                encryption_secret=None,
            )

    # 3. Settings validation: PLATFORM_KEY_ENCRYPTION_SECRET cannot reuse other secrets
    with pytest.raises(ValueError, match="PLATFORM_KEY_ENCRYPTION_SECRET must not reuse AUTH_SECRET_KEY"):
        Settings(
            DATABASE_URL="sqlite+pysqlite:///:memory:",
            AUTH_SECRET_KEY="test-auth-secret-key-0123456789abcdef",
            USER_KEY_ENCRYPTION_SECRET="test-user-secret-key-0123456789abcdef",
            PLATFORM_KEY_ENCRYPTION_SECRET="test-auth-secret-key-0123456789abcdef",
            GEMINI_API_KEY="test-gemini-key",
        )

    with pytest.raises(ValueError, match="PLATFORM_KEY_ENCRYPTION_SECRET must not reuse USER_KEY_ENCRYPTION_SECRET"):
        Settings(
            DATABASE_URL="sqlite+pysqlite:///:memory:",
            AUTH_SECRET_KEY="test-auth-secret-key-0123456789abcdef",
            USER_KEY_ENCRYPTION_SECRET="test-user-secret-key-0123456789abcdef",
            PLATFORM_KEY_ENCRYPTION_SECRET="test-user-secret-key-0123456789abcdef",
            GEMINI_API_KEY="test-gemini-key",
        )

    with pytest.raises(ValueError, match="PLATFORM_KEY_ENCRYPTION_SECRET must be at least 32 bytes"):
        Settings(
            DATABASE_URL="sqlite+pysqlite:///:memory:",
            AUTH_SECRET_KEY="test-auth-secret-key-0123456789abcdef",
            PLATFORM_KEY_ENCRYPTION_SECRET="short-secret",
            GEMINI_API_KEY="test-gemini-key",
        )


# ---------------------------------------------------------------------------
# 14. Personal encryption compatibility remains intact (Phase 6E.1.1 Scenario 10)
# ---------------------------------------------------------------------------
def test_personal_encryption_compatibility_remains_intact():
    personal_key = "test-only-fake-gemini-key-material-6789"
    user_ct = encrypt_user_key(personal_key, USER_TEST_SECRET)

    assert user_ct.startswith("v1:")
    assert decrypt_user_key(user_ct, USER_TEST_SECRET) == personal_key
    assert mask_user_key(personal_key) == "...6789"

    # Cross-domain decryption failure confirms strict domain separation
    with pytest.raises(KeyEncryptionError):
        decrypt_platform_key(user_ct, USER_TEST_SECRET)


# ---------------------------------------------------------------------------
# 15. Model creation and provider relationship
# ---------------------------------------------------------------------------
def test_model_creation_and_provider_relationship(api_client):
    client, _ = api_client
    session_factory = client.app.state.db_session_factory

    with session_factory() as session:
        provider = AIProvider(code="rel_test_provider", display_name="Rel Provider")
        session.add(provider)
        session.commit()

        model = AIModel(
            provider_id=provider.id,
            model_identifier="rel-model-1",
            display_name="Rel Model 1",
            supports_text=True,
            supports_vision=True,
            context_window=128000,
        )
        session.add(model)
        session.commit()

        session.refresh(provider)
        assert len(provider.models) == 1
        assert provider.models[0].model_identifier == "rel-model-1"
        assert model.provider.code == "rel_test_provider"

        # Cascade delete removes models
        session.delete(provider)
        session.commit()

        orphaned = session.get(AIModel, model.id)
        assert orphaned is None


# ---------------------------------------------------------------------------
# 16. Duplicate model identifier for same provider rejected
# ---------------------------------------------------------------------------
def test_duplicate_model_identifier_for_same_provider_rejected(api_client):
    client, _ = api_client
    session_factory = client.app.state.db_session_factory

    with session_factory() as session:
        p1 = AIProvider(code="p1_dup_test", display_name="Provider 1")
        p2 = AIProvider(code="p2_dup_test", display_name="Provider 2")
        session.add_all([p1, p2])
        session.commit()

        m1 = AIModel(provider_id=p1.id, model_identifier="shared-model", display_name="Model 1")
        session.add(m1)
        session.commit()

        # Same model_identifier for DIFFERENT provider is allowed
        m2 = AIModel(provider_id=p2.id, model_identifier="shared-model", display_name="Model 2")
        session.add(m2)
        session.commit()

        # Duplicate model_identifier for SAME provider is rejected
        m3 = AIModel(provider_id=p1.id, model_identifier="shared-model", display_name="Model 3")
        session.add(m3)
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()


# ---------------------------------------------------------------------------
# 17. Model access class enum validation
# ---------------------------------------------------------------------------
def test_model_access_class_enum_values(api_client):
    client, _ = api_client
    session_factory = client.app.state.db_session_factory

    with session_factory() as session:
        provider = AIProvider(code="access_class_provider", display_name="Provider")
        session.add(provider)
        session.commit()

        for ac in [
            ModelAccessClass.FREE_TIER.value,
            ModelAccessClass.PROMOTIONAL_CREDITS.value,
            ModelAccessClass.PAY_AS_YOU_GO.value,
            ModelAccessClass.SELF_HOSTED.value,
            ModelAccessClass.UNKNOWN.value,
        ]:
            m = AIModel(
                provider_id=provider.id,
                model_identifier=f"model-{ac.lower()}",
                display_name=f"Model {ac}",
                access_class=ac,
            )
            session.add(m)
        session.commit()

        # Invalid access class
        invalid = AIModel(
            provider_id=provider.id,
            model_identifier="invalid-class",
            display_name="Invalid",
            access_class="NOT_A_VALID_CLASS",
        )
        session.add(invalid)
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()


# ---------------------------------------------------------------------------
# 18. Model unverified pricing is null
# ---------------------------------------------------------------------------
def test_model_unverified_pricing_is_null(api_client):
    client, _ = api_client
    session_factory = client.app.state.db_session_factory

    with session_factory() as session:
        provider = AIProvider(code="pricing_provider", display_name="Provider")
        session.add(provider)
        session.commit()

        model = AIModel(
            provider_id=provider.id,
            model_identifier="unverified-pricing-model",
            display_name="Unverified Model",
            pricing_input_per_million=None,
            pricing_output_per_million=None,
            last_pricing_verified_at=None,
        )
        session.add(model)
        session.commit()
        session.refresh(model)

        assert model.pricing_input_per_million is None
        assert model.pricing_output_per_million is None
        assert model.last_pricing_verified_at is None


# ---------------------------------------------------------------------------
# 19. Platform key creation with mandatory quota_group
# ---------------------------------------------------------------------------
def test_platform_key_creation_with_mandatory_quota_group(api_client):
    client, _ = api_client
    session_factory = client.app.state.db_session_factory

    with session_factory() as session:
        provider = AIProvider(code="key_provider", display_name="Key Provider")
        session.add(provider)
        session.commit()

        key_record = create_platform_key(
            session=session,
            payload=PlatformAIKeyCreate(
                provider_id=provider.id,
                api_key=SecretStr("sk-test-company-secret-key-12345"),
                quota_group="tier-1-production-pool",
                key_label="Primary Production Key",
            ),
            encryption_secret=PLATFORM_TEST_SECRET,
        )
        session.commit()

        assert key_record.id is not None
        assert key_record.quota_group == "tier-1-production-pool"
        assert key_record.status == PlatformKeyStatus.UNVERIFIED.value
        assert key_record.key_hint == "...2345"
        assert "sk-test-company-secret-key-12345" not in key_record.encrypted_api_key


# ---------------------------------------------------------------------------
# 20. Platform key creation without quota_group rejected
# ---------------------------------------------------------------------------
def test_platform_key_creation_without_quota_group_rejected(api_client):
    client, _ = api_client
    session_factory = client.app.state.db_session_factory

    with session_factory() as session:
        provider = AIProvider(code="no_quota_provider", display_name="No Quota Provider")
        session.add(provider)
        session.commit()

        with pytest.raises(AIProviderServiceError, match="Quota group is mandatory"):
            create_platform_key(
                session=session,
                payload=PlatformAIKeyCreate(
                    provider_id=provider.id,
                    api_key=SecretStr("sk-any-key-1234"),
                    quota_group="   ",
                    key_label="Key Without Quota",
                ),
                encryption_secret=PLATFORM_TEST_SECRET,
            )


# ---------------------------------------------------------------------------
# 21. Platform key encryption with dedicated secret
# ---------------------------------------------------------------------------
def test_platform_key_encryption_with_dedicated_secret():
    raw_key = "platform-company-secret-token-abcdef123456"
    ciphertext = encrypt_platform_key(raw_key, PLATFORM_TEST_SECRET)

    assert ciphertext.startswith("v1:")
    assert raw_key not in ciphertext

    decrypted = decrypt_platform_key(ciphertext, PLATFORM_TEST_SECRET)
    assert decrypted == raw_key


# ---------------------------------------------------------------------------
# 22. Platform key domain separation from user key
# ---------------------------------------------------------------------------
def test_platform_key_domain_separation_from_user_key():
    raw_key = "shared-secret-plaintext-key-value-999"

    # Encrypt under platform domain
    platform_ct = encrypt_platform_key(raw_key, PLATFORM_TEST_SECRET)

    # Attempt to decrypt platform ciphertext using user key cipher with the SAME secret
    with pytest.raises(KeyEncryptionError, match="corrupted, tampered with, or used wrong secret"):
        decrypt_user_key(platform_ct, PLATFORM_TEST_SECRET)

    # Encrypt under user domain
    user_ct = encrypt_user_key(raw_key, PLATFORM_TEST_SECRET)

    # Attempt to decrypt user ciphertext using platform key cipher with the SAME secret
    with pytest.raises(KeyEncryptionError, match="corrupted, tampered with, or used wrong secret"):
        decrypt_platform_key(user_ct, PLATFORM_TEST_SECRET)


# ---------------------------------------------------------------------------
# 23. Platform key masking
# ---------------------------------------------------------------------------
def test_platform_key_masking():
    assert mask_platform_key("test-only-fake-gemini-key-material-cdef") == "...cdef"
    assert mask_platform_key("sk-test-live-key-9999") == "...9999"
    assert mask_platform_key("1234") == "****"
    assert mask_platform_key("12") == "****"
    assert mask_platform_key("") == "..."


# ---------------------------------------------------------------------------
# 24. Platform key decryption integrity and corruption handling
# ---------------------------------------------------------------------------
def test_platform_key_decryption_integrity_and_corruption_handling():
    raw_key = "sensitive-api-token-to-encrypt"
    ciphertext = encrypt_platform_key(raw_key, PLATFORM_TEST_SECRET)

    # Tampered token
    version, token = ciphertext.split(":", 1)
    tampered_token = token[:-4] + ("Z" if token[-4] != "Z" else "A") + token[-3:]
    tampered_ct = f"{version}:{tampered_token}"

    with pytest.raises(KeyEncryptionError, match="corrupted, tampered with, or used wrong secret"):
        decrypt_platform_key(tampered_ct, PLATFORM_TEST_SECRET)

    # Wrong secret
    with pytest.raises(KeyEncryptionError, match="corrupted, tampered with, or used wrong secret"):
        decrypt_platform_key(ciphertext, ALT_TEST_SECRET)


# ---------------------------------------------------------------------------
# 25. Admin can read providers, models, and platform keys
# ---------------------------------------------------------------------------
def test_admin_can_read_providers_and_models(api_client):
    client, _ = api_client
    session_factory = client.app.state.db_session_factory

    with session_factory() as session:
        seed_ai_provider_catalogue(session)
        _, admin_token = create_user_with_role(
            session=session,
            settings=client.app.state.settings,
            email="admin_read@example.com",
            role="admin",
        )

    headers = auth_header(admin_token)

    # 1. Read providers
    res_providers = client.get("/api/v1/admin/ai/providers", headers=headers)
    assert res_providers.status_code == 200
    providers = res_providers.json()
    assert len(providers) == 20

    # 2. Read models
    res_models = client.get("/api/v1/admin/ai/models", headers=headers)
    assert res_models.status_code == 200
    assert len(res_models.json()) >= 2

    # 3. Read keys
    res_keys = client.get("/api/v1/admin/ai/keys", headers=headers)
    assert res_keys.status_code == 200


# ---------------------------------------------------------------------------
# 26. Customer cannot access AI registry endpoints
# ---------------------------------------------------------------------------
def test_customer_cannot_access_ai_registry_endpoints(api_client):
    client, _ = api_client
    session_factory = client.app.state.db_session_factory

    with session_factory() as session:
        _, customer_token = create_user_with_role(
            session=session,
            settings=client.app.state.settings,
            email="customer_ai@example.com",
            role="customer",
        )

    headers = auth_header(customer_token)

    # Read should return 403
    res1 = client.get("/api/v1/admin/ai/providers", headers=headers)
    assert res1.status_code == 403

    res1_models = client.get("/api/v1/admin/ai/models", headers=headers)
    assert res1_models.status_code == 403

    # Mutation should return 403
    res2 = client.post(
        "/api/v1/admin/ai/providers",
        headers=headers,
        json={"code": "openai", "display_name": "Cust"},
    )
    assert res2.status_code == 403

    res3 = client.get("/api/v1/admin/ai/keys", headers=headers)
    assert res3.status_code == 403


# ---------------------------------------------------------------------------
# 26b. Unauthenticated request cannot access AI registry endpoints
# ---------------------------------------------------------------------------
def test_unauthenticated_cannot_access_ai_registry_endpoints(api_client):
    client, _ = api_client

    # Without Authorization header -> 401 Unauthorized
    assert client.get("/api/v1/admin/ai/providers").status_code == 401
    assert client.get("/api/v1/admin/ai/models").status_code == 401
    assert client.get("/api/v1/admin/ai/keys").status_code == 401
    assert client.post("/api/v1/admin/ai/catalogue/seed").status_code == 401
    assert client.post("/api/v1/admin/ai/keys", json={}).status_code == 401
    assert client.post("/api/v1/admin/ai/models", json={}).status_code == 401


# ---------------------------------------------------------------------------
# 27. Admin cannot mutate AI registry (requires super_admin)
# ---------------------------------------------------------------------------
def test_admin_cannot_mutate_ai_registry(api_client):
    client, _ = api_client
    session_factory = client.app.state.db_session_factory

    with session_factory() as session:
        _, admin_token = create_user_with_role(
            session=session,
            settings=client.app.state.settings,
            email="regular_admin_mutate@example.com",
            role="admin",
        )

    headers = auth_header(admin_token)
    dummy_uuid = str(uuid4())

    # Attempt POST provider -> 403 Forbidden
    res1 = client.post(
        "/api/v1/admin/ai/providers",
        headers=headers,
        json={
            "code": "openai",
            "display_name": "Unauthorized Provider",
            "base_url": "https://api.openai.com/v1",
        },
    )
    assert res1.status_code == 403

    # Attempt PATCH provider -> 403 Forbidden
    res1b = client.patch(
        f"/api/v1/admin/ai/providers/{dummy_uuid}",
        headers=headers,
        json={"display_name": "Tampered Provider"},
    )
    assert res1b.status_code == 403

    # Attempt POST seed -> 403 Forbidden
    res2 = client.post("/api/v1/admin/ai/catalogue/seed", headers=headers)
    assert res2.status_code == 403

    # Attempt POST key -> 403 Forbidden
    res3 = client.post(
        "/api/v1/admin/ai/keys",
        headers=headers,
        json={
            "provider_id": dummy_uuid,
            "api_key": "some-key",
            "quota_group": "group",
        },
    )
    assert res3.status_code == 403

    # Attempt PATCH key -> 403 Forbidden
    res3b = client.patch(
        f"/api/v1/admin/ai/keys/{dummy_uuid}",
        headers=headers,
        json={"key_label": "Hacked Label"},
    )
    assert res3b.status_code == 403

    # Attempt DELETE key -> 403 Forbidden
    res3c = client.delete(
        f"/api/v1/admin/ai/keys/{dummy_uuid}",
        headers=headers,
    )
    assert res3c.status_code == 403

    # Attempt POST model -> 403 Forbidden
    res4 = client.post(
        "/api/v1/admin/ai/models",
        headers=headers,
        json={
            "provider_id": dummy_uuid,
            "model_identifier": "test-model",
            "display_name": "Test Model",
        },
    )
    assert res4.status_code == 403

    # Attempt PATCH model -> 403 Forbidden
    res4b = client.patch(
        f"/api/v1/admin/ai/models/{dummy_uuid}",
        headers=headers,
        json={"is_enabled": False},
    )
    assert res4b.status_code == 403


# ---------------------------------------------------------------------------
# 28. Super admin can mutate AI registry
# ---------------------------------------------------------------------------
def test_super_admin_can_mutate_ai_registry(api_client):
    client, _ = api_client
    session_factory = client.app.state.db_session_factory

    with session_factory() as session:
        _, super_token = create_user_with_role(
            session=session,
            settings=client.app.state.settings,
            email="super_admin_mutate@example.com",
            role="super_admin",
        )

    headers = auth_header(super_token)

    # 1. Trigger catalogue seed
    seed_res = client.post("/api/v1/admin/ai/catalogue/seed", headers=headers)
    assert seed_res.status_code == 200
    assert seed_res.json()["providers_created"] == 20

    # 2. Get groq provider id
    providers_res = client.get("/api/v1/admin/ai/providers", headers=headers)
    groq_provider = [p for p in providers_res.json() if p["code"] == "groq"][0]
    provider_id = groq_provider["id"]

    # 3. Create custom model for groq
    create_m_res = client.post(
        "/api/v1/admin/ai/models",
        headers=headers,
        json={
            "provider_id": provider_id,
            "model_identifier": "custom-groq-admin-model",
            "display_name": "Custom Groq Admin Model",
            "access_class": "PAY_AS_YOU_GO",
            "context_window": 128000,
        },
    )
    assert create_m_res.status_code == 201

    # 4. Create platform key
    create_k_res = client.post(
        "/api/v1/admin/ai/keys",
        headers=headers,
        json={
            "provider_id": provider_id,
            "api_key": "gsk-super-secret-platform-key-7777",
            "quota_group": "super-quota-bucket",
            "key_label": "Super Admin Test Key",
        },
    )
    assert create_k_res.status_code == 201
    k_data = create_k_res.json()
    key_id = k_data["id"]
    assert k_data["key_hint"] == "...7777"

    # 5. Patch platform key
    patch_k_res = client.patch(
        f"/api/v1/admin/ai/keys/{key_id}",
        headers=headers,
        json={"key_label": "Renamed Super Key", "status": "ACTIVE"},
    )
    assert patch_k_res.status_code == 200
    assert patch_k_res.json()["key_label"] == "Renamed Super Key"
    assert patch_k_res.json()["status"] == "ACTIVE"

    # 6. Delete platform key
    del_k_res = client.delete(f"/api/v1/admin/ai/keys/{key_id}", headers=headers)
    assert del_k_res.status_code == 204


# ---------------------------------------------------------------------------
# 29. Platform key API never exposes plaintext or ciphertext
# ---------------------------------------------------------------------------
def test_platform_key_api_never_exposes_plaintext_or_ciphertext(api_client):
    client, _ = api_client
    session_factory = client.app.state.db_session_factory

    with session_factory() as session:
        provider = AIProvider(code="key_leak_check_provider", display_name="Leak Check")
        session.add(provider)
        session.commit()

        raw_secret_key = "sk-super-confidential-api-token-xyz-1234"
        key_record = create_platform_key(
            session=session,
            payload=PlatformAIKeyCreate(
                provider_id=provider.id,
                api_key=SecretStr(raw_secret_key),
                quota_group="leak-check-group",
                key_label="Confidential Key",
            ),
            encryption_secret=PLATFORM_TEST_SECRET,
        )
        session.commit()
        key_id = key_record.id

        _, admin_token = create_user_with_role(
            session=session,
            settings=client.app.state.settings,
            email="admin_leak_check@example.com",
            role="admin",
        )

    headers = auth_header(admin_token)

    # 1. Check list keys endpoint
    list_res = client.get("/api/v1/admin/ai/keys", headers=headers)
    assert list_res.status_code == 200
    list_text = list_res.text

    assert raw_secret_key not in list_text
    assert "gAAAAA" not in list_text  # Fernet token prefix
    assert "encrypted_api_key" not in list_text
    assert "api_key" not in list_text

    # 2. Check get key by ID endpoint
    single_res = client.get(f"/api/v1/admin/ai/keys/{key_id}", headers=headers)
    assert single_res.status_code == 200
    single_data = single_res.json()
    single_text = single_res.text

    assert raw_secret_key not in single_text
    assert "gAAAAA" not in single_text
    assert "encrypted_api_key" not in single_data
    assert "api_key" not in single_data
    assert single_data["key_hint"] == "...1234"


# ---------------------------------------------------------------------------
# 30. Audit log recorded for AI registry mutations without secrets
# ---------------------------------------------------------------------------
def test_audit_log_recorded_for_ai_registry_mutations_without_secrets(api_client):
    client, _ = api_client
    session_factory = client.app.state.db_session_factory

    with session_factory() as session:
        super_user, super_token = create_user_with_role(
            session=session,
            settings=client.app.state.settings,
            email="super_audit@example.com",
            role="super_admin",
        )

    headers = auth_header(super_token)
    secret_key_to_protect = "test-only-fake-gemini-key-material-audit-protect"

    # 1. Create provider
    p_res = client.post(
        "/api/v1/admin/ai/providers",
        headers=headers,
        json={
            "code": "groq",
            "display_name": "Audit Test Groq Provider",
            "base_url": "https://api.groq.com/openai/v1",
        },
    )
    assert p_res.status_code == 201
    provider_id = p_res.json()["id"]

    # 2. Create platform key
    k_res = client.post(
        "/api/v1/admin/ai/keys",
        headers=headers,
        json={
            "provider_id": provider_id,
            "api_key": secret_key_to_protect,
            "quota_group": "audit-quota-group",
            "key_label": "Audit Key",
        },
    )
    assert k_res.status_code == 201

    # Verify audit log entries
    with session_factory() as session:
        logs = session.scalars(
            select(AdminAuditLog)
            .where(AdminAuditLog.actor_user_id == super_user.id)
            .order_by(AdminAuditLog.created_at.asc())
        ).all()

        actions = [log.action for log in logs]
        assert "ai_provider.create" in actions
        assert "platform_key.create" in actions

        # Check metadata json for secrecy
        for log in logs:
            serialized_meta = json.dumps(log.metadata_json or {})
            assert secret_key_to_protect not in serialized_meta
            assert "gAAAAA" not in serialized_meta  # No Fernet token leaked


# ---------------------------------------------------------------------------
# 31. Gemini models free-tier and data privacy metadata (Phase 6E.1.3)
# ---------------------------------------------------------------------------
def test_gemini_models_free_tier_and_data_privacy_metadata(api_client):
    client, _ = api_client
    session_factory = client.app.state.db_session_factory

    with session_factory() as session:
        seed_ai_provider_catalogue(session)
        session.commit()

        gemini = session.scalar(select(AIProvider).where(AIProvider.code == "google_gemini"))
        assert gemini is not None

        models = {m.model_identifier: m for m in gemini.models}
        for model_id in ["gemini-3.8-flash", "gemini-3.5-flash-lite"]:
            m = models[model_id]

            # 1. Access class is FREE_TIER
            assert m.access_class == ModelAccessClass.FREE_TIER.value

            # 2. Free availability distinct from unlimited access
            meta = m.free_tier_limit_metadata
            assert meta is not None
            assert meta["is_free_tier_available"] is True
            assert meta["has_unlimited_access"] is False

            # 3. Rate limits documentation
            rate_limits = meta["rate_limits"]
            assert rate_limits["quota_enforcement"] == "per_project"
            assert "RPM" in rate_limits["limit_types"]
            assert "TPM" in rate_limits["limit_types"]
            assert "RPD" in rate_limits["limit_types"]
            assert "rate_limits_url" in rate_limits

            # 4. Data use conditions
            data_use = meta["data_use_conditions"]
            assert data_use["free_tier_training_use"] is True
            assert data_use["human_review_permitted"] is True
            assert data_use["paid_tier_training_use"] is False
            assert data_use["promotional_credits_permanent_free"] is False
            assert data_use["sensitive_workloads_allowed"] is False
            assert data_use["requires_policy_approval"] is True
            assert "summary" in data_use

            # 5. URLs
            assert m.terms_url == "https://ai.google.dev/gemini-api/terms"
            assert m.pricing_url == "https://ai.google.dev/pricing"

            # 6. Paid pricing remains separate and unverified prices remain NULL
            assert m.pricing_input_per_million is None
            assert m.pricing_output_per_million is None
            assert m.last_pricing_verified_at is None


# ---------------------------------------------------------------------------
# 32. Seed preserves admin settings while updating free-tier metadata (Phase 6E.1.3)
# ---------------------------------------------------------------------------
def test_seed_preserves_admin_settings_while_updating_free_tier_metadata(api_client):
    client, _ = api_client
    session_factory = client.app.state.db_session_factory

    with session_factory() as session:
        # Pre-seed a Gemini provider and model with older/legacy PAY_AS_YOU_GO access class
        gemini = AIProvider(
            code="google_gemini",
            display_name="Google Gemini",
            adapter_type="google_gemini",
            connection_status=ProviderConnectionStatus.AVAILABLE.value,
        )
        session.add(gemini)
        session.flush()

        model_38 = AIModel(
            provider_id=gemini.id,
            model_identifier="gemini-3.8-flash",
            display_name="Gemini 3.8 Flash (Custom Config)",
            access_class=ModelAccessClass.PAY_AS_YOU_GO.value,
            free_tier_limit_metadata=None,
            is_enabled=False,  # Admin deliberately disabled this model
            is_default=True,
        )
        session.add(model_38)
        session.commit()

        # Run catalogue seed
        stats = seed_ai_provider_catalogue(session)
        session.commit()

        session.refresh(model_38)

        # Access class and free-tier metadata are updated to accurate free-tier specification
        assert model_38.access_class == ModelAccessClass.FREE_TIER.value
        assert model_38.free_tier_limit_metadata is not None
        assert model_38.free_tier_limit_metadata["is_free_tier_available"] is True

        # Admin configuration choices MUST survive
        assert model_38.is_enabled is False  # Admin disabled state preserved!
        assert model_38.is_default is True  # Admin default selection preserved!

        # Repeated seeding is idempotent
        stats_repeat = seed_ai_provider_catalogue(session)
        session.commit()
        assert stats_repeat["models_created"] == 0


# ---------------------------------------------------------------------------
# 33. Paid pricing remains separate from free-tier eligibility (Phase 6E.1.3)
# ---------------------------------------------------------------------------
def test_paid_pricing_separate_from_free_tier_eligibility(api_client):
    client, _ = api_client
    session_factory = client.app.state.db_session_factory

    with session_factory() as session:
        seed_ai_provider_catalogue(session)
        session.commit()

        gemini = session.scalar(select(AIProvider).where(AIProvider.code == "google_gemini"))
        model = session.scalar(
            select(AIModel).where(
                AIModel.provider_id == gemini.id,
                AIModel.model_identifier == "gemini-3.8-flash",
            )
        )
        assert model is not None

        # Verify initial state: FREE_TIER with NULL paid pricing
        assert model.access_class == ModelAccessClass.FREE_TIER.value
        assert model.pricing_input_per_million is None
        assert model.pricing_output_per_million is None
        assert model.last_pricing_verified_at is None
        assert model.free_tier_limit_metadata is not None

        # Operator attaches verified paid-tier token pricing to this model
        verified_date = datetime(2026, 11, 24, 12, 0, 0, tzinfo=timezone.utc)
        model.pricing_input_per_million = Decimal("0.7500")
        model.pricing_output_per_million = Decimal("3.7500")
        model.last_pricing_verified_at = verified_date
        session.commit()

        session.refresh(model)
        # Paid pricing is populated without disrupting free-tier metadata or access classification
        assert model.pricing_input_per_million == Decimal("0.7500")
        assert model.pricing_output_per_million == Decimal("3.7500")
        assert model.last_pricing_verified_at is not None
        stored_dt = model.last_pricing_verified_at
        if stored_dt.tzinfo is None:
            stored_dt = stored_dt.replace(tzinfo=timezone.utc)
        assert stored_dt == verified_date
        assert model.access_class == ModelAccessClass.FREE_TIER.value
        assert model.free_tier_limit_metadata["is_free_tier_available"] is True
        assert model.free_tier_limit_metadata["data_use_conditions"]["paid_tier_training_use"] is False


def test_groq_enterprise_restricted_models_seeded_with_unknown_access_class(api_client) -> None:
    """Enterprise-restricted Groq models are seeded with UNKNOWN access class, disabled, and non-default."""
    client, _ = api_client
    session_factory = client.app.state.db_session_factory

    with session_factory() as session:
        seed_ai_provider_catalogue(session)
        session.commit()

        groq_provider = session.scalar(select(AIProvider).where(AIProvider.code == "groq"))
        assert groq_provider is not None
        assert groq_provider.connection_status == ProviderConnectionStatus.PLANNED.value

        groq_models = {
            m.model_identifier: m
            for m in session.scalars(select(AIModel).where(AIModel.provider_id == groq_provider.id)).all()
        }

        # Enterprise-restricted models
        for restricted_id in ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"]:
            assert restricted_id in groq_models
            model = groq_models[restricted_id]
            assert model.access_class == ModelAccessClass.UNKNOWN.value
            assert model.is_enabled is False
            assert model.is_default is False
            assert "[ENTERPRISE_RESTRICTED]" in model.description

        # Open weights candidate models maintain PAY_AS_YOU_GO
        assert groq_models["openai/gpt-oss-120b"].access_class == ModelAccessClass.PAY_AS_YOU_GO.value
        assert groq_models["openai/gpt-oss-20b"].access_class == ModelAccessClass.PAY_AS_YOU_GO.value

        # Gemini models maintain FREE_TIER
        gemini_provider = session.scalar(select(AIProvider).where(AIProvider.code == "google_gemini"))
        gemini_models = {
            m.model_identifier: m
            for m in session.scalars(select(AIModel).where(AIModel.provider_id == gemini_provider.id)).all()
        }
        assert gemini_models["gemini-3.8-flash"].access_class == ModelAccessClass.FREE_TIER.value
        assert gemini_models["gemini-3.5-flash-lite"].access_class == ModelAccessClass.FREE_TIER.value


def test_admin_direct_api_mutation_rejected_for_enterprise_restricted_models(api_client) -> None:
    """Super-admin and admin direct API requests attempting to activate or modify enterprise-restricted models are rejected."""
    client, _ = api_client
    session_factory = client.app.state.db_session_factory

    with session_factory() as session:
        _, super_token = create_user_with_role(
            session=session,
            settings=client.app.state.settings,
            email="super_admin_restrict_test@example.com",
            role="super_admin",
        )
        _, admin_token = create_user_with_role(
            session=session,
            settings=client.app.state.settings,
            email="regular_admin_restrict_test@example.com",
            role="admin",
        )

    super_headers = auth_header(super_token)
    admin_headers = auth_header(admin_token)

    # 1. Seed catalogue
    seed_res = client.post("/api/v1/admin/ai/catalogue/seed", headers=super_headers)
    assert seed_res.status_code == 200

    # 2. Retrieve models and find restricted ones
    models_res = client.get("/api/v1/admin/ai/models", headers=super_headers)
    assert models_res.status_code == 200
    models_by_identifier = {m["model_identifier"]: m for m in models_res.json()}

    restricted_identifiers = ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"]

    for model_id_name in restricted_identifiers:
        assert model_id_name in models_by_identifier
        target_model = models_by_identifier[model_id_name]
        uuid_str = target_model["id"]

        # Initial baseline verification
        assert target_model["is_enabled"] is False
        assert target_model["is_default"] is False
        assert target_model["access_class"] == "UNKNOWN"
        assert "[ENTERPRISE_RESTRICTED]" in target_model["description"]

        # a) Super-admin direct API request to enable model -> REJECTED (400)
        res_enable = client.patch(
            f"/api/v1/admin/ai/models/{uuid_str}",
            headers=super_headers,
            json={"is_enabled": True},
        )
        assert res_enable.status_code == 400
        assert "enterprise-restricted on Groq and cannot be enabled" in res_enable.json()["detail"]

        # b) Super-admin direct API request to set as default -> REJECTED (400)
        res_default = client.patch(
            f"/api/v1/admin/ai/models/{uuid_str}",
            headers=super_headers,
            json={"is_default": True},
        )
        assert res_default.status_code == 400
        assert "cannot be set as default" in res_default.json()["detail"]

        # c) Super-admin direct API request to change access_class to PAY_AS_YOU_GO -> REJECTED (400)
        res_access = client.patch(
            f"/api/v1/admin/ai/models/{uuid_str}",
            headers=super_headers,
            json={"access_class": "PAY_AS_YOU_GO"},
        )
        assert res_access.status_code == 400
        assert "access class cannot be changed" in res_access.json()["detail"]
        assert "must remain 'UNKNOWN'" in res_access.json()["detail"]

        # d) Super-admin direct API request to strip [ENTERPRISE_RESTRICTED] from description -> REJECTED (400)
        res_desc = client.patch(
            f"/api/v1/admin/ai/models/{uuid_str}",
            headers=super_headers,
            json={"description": "Unrestricted general availability Llama model"},
        )
        assert res_desc.status_code == 400
        assert "description must retain the '[ENTERPRISE_RESTRICTED]' designation" in res_desc.json()["detail"]

        # e) Super-admin combined bypass payload -> REJECTED (400)
        res_combined = client.patch(
            f"/api/v1/admin/ai/models/{uuid_str}",
            headers=super_headers,
            json={
                "is_enabled": True,
                "is_default": True,
                "access_class": "PAY_AS_YOU_GO",
                "description": "Clean unrestricted model description",
            },
        )
        assert res_combined.status_code == 400

        # f) Regular admin request to enable -> REJECTED by RBAC (403)
        res_admin_rbac = client.patch(
            f"/api/v1/admin/ai/models/{uuid_str}",
            headers=admin_headers,
            json={"is_enabled": True},
        )
        assert res_admin_rbac.status_code == 403

        # g) Verify state in database remained UNCHANGED (immutable and protected)
        get_res = client.get(f"/api/v1/admin/ai/models/{uuid_str}", headers=super_headers)
        assert get_res.status_code == 200
        verified = get_res.json()
        assert verified["is_enabled"] is False
        assert verified["is_default"] is False
        assert verified["access_class"] == "UNKNOWN"
        assert "[ENTERPRISE_RESTRICTED]" in verified["description"]


def test_unrestricted_models_administration_still_works(api_client) -> None:
    """Super-admin can manage ordinary unrestricted models without interference."""
    client, _ = api_client
    session_factory = client.app.state.db_session_factory

    with session_factory() as session:
        _, super_token = create_user_with_role(
            session=session,
            settings=client.app.state.settings,
            email="super_admin_unrestricted_test@example.com",
            role="super_admin",
        )

    super_headers = auth_header(super_token)

    # 1. Seed catalogue
    client.post("/api/v1/admin/ai/catalogue/seed", headers=super_headers)

    # 2. Retrieve models
    models_res = client.get("/api/v1/admin/ai/models", headers=super_headers)
    models = {m["model_identifier"]: m for m in models_res.json()}

    # 3. Patch an unrestricted Groq model (openai/gpt-oss-120b)
    gpt_120b = models["openai/gpt-oss-120b"]
    patch_res = client.patch(
        f"/api/v1/admin/ai/models/{gpt_120b['id']}",
        headers=super_headers,
        json={"context_window": 130000},
    )
    assert patch_res.status_code == 200
    assert patch_res.json()["context_window"] == 130000

    # 4. Patch an unrestricted Gemini model (gemini-3.5-flash-lite)
    gemini_lite = models["gemini-3.5-flash-lite"]
    # Disable it
    patch_disable = client.patch(
        f"/api/v1/admin/ai/models/{gemini_lite['id']}",
        headers=super_headers,
        json={"is_enabled": False},
    )
    assert patch_disable.status_code == 200
    assert patch_disable.json()["is_enabled"] is False

    # Re-enable it
    patch_enable = client.patch(
        f"/api/v1/admin/ai/models/{gemini_lite['id']}",
        headers=super_headers,
        json={"is_enabled": True},
    )
    assert patch_enable.status_code == 200
    assert patch_enable.json()["is_enabled"] is True
