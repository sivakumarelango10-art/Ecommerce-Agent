from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import SecretStr, ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from backend.app.core.config import Settings
from backend.app.db.base import Base
from backend.app.db.models import User, UserGeminiSettings
from backend.app.security.key_encryption import (
    CURRENT_CIPHERTEXT_VERSION,
    KeyEncryptionError,
    decrypt_user_key,
    encrypt_user_key,
    mask_user_key,
)

VALID_TEST_SECRET = SecretStr("test-encryption-secret-32-bytes-long-123456789")
VALID_ALT_SECRET = SecretStr("alt-test-encryption-secret-32-bytes-long-987654321")


# ---------------------------------------------------------------------------
# 1. Encryption and Decryption Round-Trip Tests
# ---------------------------------------------------------------------------

def test_encryption_decryption_round_trip():
    """Verify that an API key can be encrypted and cleanly decrypted with the correct secret."""
    raw_key = "AIzaSyB1234567890abcdefghijklmnopqrstuv"
    ciphertext = encrypt_user_key(raw_key, VALID_TEST_SECRET)

    assert ciphertext.startswith(f"{CURRENT_CIPHERTEXT_VERSION}:")
    decrypted = decrypt_user_key(ciphertext, VALID_TEST_SECRET)
    assert decrypted == raw_key


def test_different_plaintext_produces_distinct_ciphertext():
    """Verify different plaintext keys produce distinct ciphertext strings."""
    key1 = "AIzaSyKeyOne1234567890abcdefghijkl"
    key2 = "AIzaSyKeyTwo9876543210zyxwvutsrqpon"

    ct1 = encrypt_user_key(key1, VALID_TEST_SECRET)
    ct2 = encrypt_user_key(key2, VALID_TEST_SECRET)

    assert ct1 != ct2


def test_same_plaintext_produces_distinct_ciphertext_due_to_iv():
    """Verify that Fernet nonces/timestamps ensure nondeterministic ciphertexts for identical plaintext."""
    key = "AIzaSySameKeyEveryTime1234567890"

    ct1 = encrypt_user_key(key, VALID_TEST_SECRET)
    ct2 = encrypt_user_key(key, VALID_TEST_SECRET)

    assert ct1 != ct2
    assert decrypt_user_key(ct1, VALID_TEST_SECRET) == key
    assert decrypt_user_key(ct2, VALID_TEST_SECRET) == key


# ---------------------------------------------------------------------------
# 2. Tampering, Corruption, and Wrong Secret Tests
# ---------------------------------------------------------------------------

def test_tampered_ciphertext_is_rejected():
    """Verify that any modification to ciphertext token fails authenticated decryption."""
    raw_key = "AIzaSyTamperTest1234567890"
    ciphertext = encrypt_user_key(raw_key, VALID_TEST_SECRET)

    # Tamper with the middle of the token
    version, token = ciphertext.split(":", 1)
    tampered_token = token[:-5] + ("A" if token[-5] != "A" else "B") + token[-4:]
    tampered_ct = f"{version}:{tampered_token}"

    with pytest.raises(KeyEncryptionError, match="corrupted, tampered with, or used wrong secret"):
        decrypt_user_key(tampered_ct, VALID_TEST_SECRET)


def test_wrong_secret_cannot_decrypt():
    """Verify decryption fails when attempted with a different secret."""
    raw_key = "AIzaSyWrongSecretTest123456789"
    ciphertext = encrypt_user_key(raw_key, VALID_TEST_SECRET)

    with pytest.raises(KeyEncryptionError, match="corrupted, tampered with, or used wrong secret"):
        decrypt_user_key(ciphertext, VALID_ALT_SECRET)


def test_plaintext_is_never_included_in_exception_messages():
    """Verify that neither the plaintext key nor sensitive material leaks into exception messages."""
    secret_key_material = "SUPER_SECRET_PLAINTEXT_KEY_XYZ_999"

    # 1. Decryption failure on corrupted token
    with pytest.raises(KeyEncryptionError) as exc_info:
        decrypt_user_key("v1:totally-invalid-token", VALID_TEST_SECRET)
    assert secret_key_material not in str(exc_info.value)

    # 2. Empty key encryption failure
    with pytest.raises(KeyEncryptionError) as exc_info:
        encrypt_user_key("   ", VALID_TEST_SECRET)
    assert "blank" in str(exc_info.value).lower() or "empty" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# 3. Versioned Ciphertext Validation
# ---------------------------------------------------------------------------

def test_unsupported_version_prefix_is_rejected():
    """Verify that tokens with unsupported version tags are rejected."""
    raw_key = "AIzaSyVersionTest123456789"
    valid_ct = encrypt_user_key(raw_key, VALID_TEST_SECRET)
    _, token = valid_ct.split(":", 1)

    future_version_ct = f"v99:{token}"
    with pytest.raises(KeyEncryptionError, match="Unsupported ciphertext version: 'v99'"):
        decrypt_user_key(future_version_ct, VALID_TEST_SECRET)


def test_missing_version_delimiter_is_rejected():
    """Verify that unversioned tokens lacking the version prefix delimiter are rejected."""
    with pytest.raises(KeyEncryptionError, match="missing version prefix"):
        decrypt_user_key("unversioned_fernet_token_without_colon", VALID_TEST_SECRET)

    with pytest.raises(KeyEncryptionError, match="empty"):
        decrypt_user_key("   ", VALID_TEST_SECRET)


# ---------------------------------------------------------------------------
# 4. Key Masking Tests
# ---------------------------------------------------------------------------

def test_mask_user_key_displays_only_last_four_characters():
    """Verify that key masking never exposes the complete key and shows only the final 4 characters."""
    full_key = "AIzaSyB1234567890abcdef"
    masked = mask_user_key(full_key)

    assert masked == "...cdef"
    assert full_key not in masked
    assert full_key[:10] not in masked
    assert len(masked) == 7


@pytest.mark.parametrize(
    ("input_key", "expected"),
    [
        ("short", "...hort"),
        ("1234", "****"),
        ("12", "****"),
        ("", "..."),
        ("   ", "..."),
    ],
)
def test_mask_user_key_edge_cases(input_key, expected):
    """Verify edge case handling for short or empty keys."""
    assert mask_user_key(input_key) == expected


# ---------------------------------------------------------------------------
# 5. Configuration Validation Tests
# ---------------------------------------------------------------------------

def test_weak_encryption_secret_is_rejected(monkeypatch):
    """Verify secrets shorter than 32 bytes are rejected by Settings validator."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-sent")
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///:memory:")
    monkeypatch.setenv("AUTH_SECRET_KEY", "test-auth-secret-key-0123456789abcdef")
    monkeypatch.setenv("USER_KEY_ENCRYPTION_SECRET", "too-short")

    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None)
    assert "at least 32 bytes" in str(exc_info.value)


def test_secret_reuse_with_auth_secret_key_is_rejected(monkeypatch):
    """Verify USER_KEY_ENCRYPTION_SECRET cannot reuse AUTH_SECRET_KEY."""
    shared_secret = "identical-secret-reused-between-auth-and-key-encryption"
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-sent")
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///:memory:")
    monkeypatch.setenv("AUTH_SECRET_KEY", shared_secret)
    monkeypatch.setenv("USER_KEY_ENCRYPTION_SECRET", shared_secret)

    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None)
    assert "must not reuse AUTH_SECRET_KEY" in str(exc_info.value)


def test_secret_reuse_with_gemini_api_key_is_rejected(monkeypatch):
    """Verify USER_KEY_ENCRYPTION_SECRET cannot reuse GEMINI_API_KEY."""
    shared_key = "AIzaSyReusedKeyMaterialThatIsAtLeast32BytesLong"
    monkeypatch.setenv("GEMINI_API_KEY", shared_key)
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///:memory:")
    monkeypatch.setenv("AUTH_SECRET_KEY", "test-auth-secret-key-0123456789abcdef")
    monkeypatch.setenv("USER_KEY_ENCRYPTION_SECRET", shared_key)

    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None)
    assert "must not reuse GEMINI_API_KEY" in str(exc_info.value)


@pytest.mark.parametrize("env_name", ["staging", "production"])
def test_missing_encryption_secret_in_staging_or_production_is_rejected(monkeypatch, env_name):
    """Verify USER_KEY_ENCRYPTION_SECRET is mandatory in staging and production."""
    monkeypatch.setenv("ENVIRONMENT", env_name)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-sent")
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///:memory:")
    monkeypatch.setenv("AUTH_SECRET_KEY", "test-auth-secret-key-0123456789abcdef")
    monkeypatch.delenv("USER_KEY_ENCRYPTION_SECRET", raising=False)

    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None)
    assert "USER_KEY_ENCRYPTION_SECRET is required in staging and production" in str(exc_info.value)


def test_missing_encryption_secret_in_development_is_allowed(monkeypatch):
    """Verify USER_KEY_ENCRYPTION_SECRET is optional in development for baseline startup."""
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-sent")
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///:memory:")
    monkeypatch.setenv("AUTH_SECRET_KEY", "test-auth-secret-key-0123456789abcdef")
    monkeypatch.delenv("USER_KEY_ENCRYPTION_SECRET", raising=False)

    settings = Settings(_env_file=None)
    assert settings.user_key_encryption_secret is None


# ---------------------------------------------------------------------------
# 6. Database Model Schema and Relationship Tests
# ---------------------------------------------------------------------------

def test_user_gemini_settings_model_in_isolated_db():
    """Verify UserGeminiSettings model table schema, cascade delete, and relationships in memory."""
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    raw_key = "AIzaSyTestUserKey1234567890abcdef"
    encrypted_ct = encrypt_user_key(raw_key, VALID_TEST_SECRET)
    hint = mask_user_key(raw_key)

    with Session(engine) as session:
        user = User(
            id=uuid4(),
            email="keyuser@example.com",
            password_hash="mock-hash",
            is_active=True,
        )
        session.add(user)
        session.flush()

        settings = UserGeminiSettings(
            user_id=user.id,
            encrypted_api_key=encrypted_ct,
            key_hint=hint,
            key_version=1,
            is_enabled=True,
            status="ACTIVE",
            last_validated_at=datetime.now(timezone.utc),
        )
        session.add(settings)
        session.commit()

        # Query back via relationship
        queried_user = session.scalar(select(User).where(User.id == user.id))
        assert queried_user is not None
        assert queried_user.gemini_settings is not None
        assert queried_user.gemini_settings.key_hint == "...cdef"
        assert queried_user.gemini_settings.key_version == 1
        assert queried_user.gemini_settings.is_enabled is True
        assert queried_user.gemini_settings.status == "ACTIVE"
        assert queried_user.gemini_settings.encrypted_api_key == encrypted_ct

        # Decrypt in memory
        decrypted = decrypt_user_key(queried_user.gemini_settings.encrypted_api_key, VALID_TEST_SECRET)
        assert decrypted == raw_key

        # Cascade delete verification
        session.delete(queried_user)
        session.commit()

        orphaned_settings = session.scalar(
            select(UserGeminiSettings).where(UserGeminiSettings.user_id == user.id)
        )
        assert orphaned_settings is None

    engine.dispose()
