from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any
from uuid import UUID

import httpx
from google import genai
from google.genai import errors, types
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.config import Settings
from backend.app.core.datetime_utils import utc_now
from backend.app.db.models import UserGeminiSettings
from backend.app.schemas.gemini_settings import (
    PersonalGeminiKeyResponse,
    ValidatePersonalGeminiKeyResponse,
)
from backend.app.security.key_encryption import (
    KeyEncryptionError,
    decrypt_user_key,
    encrypt_user_key,
    mask_user_key,
)

logger = logging.getLogger(__name__)

# Strict validation timeout: approximately 5 seconds
VALIDATION_TIMEOUT_MS = 5000


class GeminiKeyServiceError(RuntimeError):
    """Base error for personal key management and encryption operations."""


class GeminiKeyValidationError(GeminiKeyServiceError):
    """Raised when key validation against the Gemini API fails."""

    def __init__(
        self,
        message: str,
        status: str,
        masked_key: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.masked_key = masked_key


def validate_gemini_api_key(
    api_key: str | SecretStr,
    settings: Settings,
    client_factory: Callable[..., Any] | None = None,
) -> ValidatePersonalGeminiKeyResponse:
    """Validate a personal Gemini API key with a bounded, lightweight request.

    - Uses a strict 5-second timeout.
    - Limits token generation to 1 output token to avoid waste.
    - Never logs plaintext keys or raw provider error payloads.
    - Accurately distinguishes between:
      * Valid key (VALID)
      * Invalid/revoked credential (INVALID_KEY)
      * Quota exhaustion (QUOTA_EXHAUSTED)
      * Model unavailable (MODEL_UNAVAILABLE)
      * Temporary provider/network failure (TEMPORARY_FAILURE)
    """
    raw_key = api_key.get_secret_value() if isinstance(api_key, SecretStr) else api_key
    raw_key = raw_key.strip()
    masked = mask_user_key(raw_key)

    if not raw_key:
        return ValidatePersonalGeminiKeyResponse(
            is_valid=False,
            status="INVALID_KEY",
            message="API key must not be blank.",
            masked_key=masked,
        )

    factory = client_factory or genai.Client
    http_options = types.HttpOptions(
        timeout=VALIDATION_TIMEOUT_MS,
    )

    try:
        client = factory(api_key=raw_key, http_options=http_options)
        model_name = getattr(settings, "gemini_model", "gemini-3.8-flash")

        # Bounded ping request: 1 output token
        client.models.generate_content(
            model=model_name,
            contents="ping",
            config=types.GenerateContentConfig(max_output_tokens=1),
        )

        return ValidatePersonalGeminiKeyResponse(
            is_valid=True,
            status="VALID",
            message="Gemini API key verified successfully.",
            masked_key=masked,
        )

    except errors.APIError as exc:
        code = getattr(exc, "code", None)
        message_lower = str(exc.message or "").lower()

        # 1. Invalid or Revoked Credential
        if code in (400, 401, 403):
            # Check for API key invalid or permission denied
            if any(
                term in message_lower
                for term in ("api_key_invalid", "api key not valid", "invalid api key", "unauthenticated", "permission_denied")
            ) or code in (400, 401, 403):
                logger.info(
                    "Personal Gemini key validation rejected: invalid or revoked credential (code=%s)",
                    code,
                )
                return ValidatePersonalGeminiKeyResponse(
                    is_valid=False,
                    status="INVALID_KEY",
                    message="The provided Gemini API key is invalid or revoked.",
                    masked_key=masked,
                )

        # 2. Quota / Rate Limit Exhaustion
        if code == 429 or "resource_exhausted" in message_lower or "quota" in message_lower:
            logger.info("Personal Gemini key validation hit quota limit (code=%s)", code)
            return ValidatePersonalGeminiKeyResponse(
                is_valid=False,
                status="QUOTA_EXHAUSTED",
                message="The Gemini API key has exceeded its quota or rate limit. Please try again later.",
                masked_key=masked,
            )

        # 3. Model Unavailable
        if code == 404 or "not_found" in message_lower:
            logger.info("Personal Gemini key validation: model %s unavailable", model_name)
            return ValidatePersonalGeminiKeyResponse(
                is_valid=False,
                status="MODEL_UNAVAILABLE",
                message="The configured Gemini model is unavailable for this key.",
                masked_key=masked,
            )

        # 4. Temporary Provider Outages
        if code in (408, 500, 502, 503, 504):
            logger.warning("Personal Gemini key validation temporary provider error (code=%s)", code)
            return ValidatePersonalGeminiKeyResponse(
                is_valid=False,
                status="TEMPORARY_FAILURE",
                message="Gemini service is temporarily unavailable. Please try again later.",
                masked_key=masked,
            )

        logger.warning("Personal Gemini key validation unexpected API error (code=%s)", code)
        return ValidatePersonalGeminiKeyResponse(
            is_valid=False,
            status="TEMPORARY_FAILURE",
            message="Gemini service error occurred during validation. Please try again.",
            masked_key=masked,
        )

    except (httpx.TimeoutException, TimeoutError):
        logger.warning("Personal Gemini key validation timed out after %sms", VALIDATION_TIMEOUT_MS)
        return ValidatePersonalGeminiKeyResponse(
            is_valid=False,
            status="TEMPORARY_FAILURE",
            message="Gemini key validation request timed out. Please try again.",
            masked_key=masked,
        )

    except httpx.HTTPError:
        logger.warning("Personal Gemini key validation network transport error")
        return ValidatePersonalGeminiKeyResponse(
            is_valid=False,
            status="TEMPORARY_FAILURE",
            message="Network error communicating with Gemini. Please try again.",
            masked_key=masked,
        )

    except Exception:
        logger.exception("Unexpected error during personal Gemini key validation")
        return ValidatePersonalGeminiKeyResponse(
            is_valid=False,
            status="TEMPORARY_FAILURE",
            message="An unexpected error occurred while validating the API key.",
            masked_key=masked,
        )


def get_personal_key_status(session: Session, user_id: UUID) -> PersonalGeminiKeyResponse:
    """Retrieve safe metadata for a user's stored Gemini key without decrypting ciphertext."""
    record = session.scalar(
        select(UserGeminiSettings).where(UserGeminiSettings.user_id == user_id)
    )
    if record is None:
        return PersonalGeminiKeyResponse(
            has_key=False,
            is_enabled=False,
        )

    return PersonalGeminiKeyResponse(
        has_key=True,
        is_enabled=record.is_enabled,
        key_hint=record.key_hint,
        status=record.status,
        last_validated_at=record.last_validated_at,
        last_used_at=record.last_used_at,
        cooldown_until=record.cooldown_until,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def save_personal_key(
    session: Session,
    user_id: UUID,
    api_key: SecretStr,
    settings: Settings,
    client_factory: Callable[..., Any] | None = None,
) -> UserGeminiSettings:
    """Validate and persist an authenticated user's personal Gemini API key.

    - Validates with Gemini before saving.
    - If validation fails, raises GeminiKeyValidationError and leaves existing key intact.
    - Encrypts using dedicated HKDF-SHA256 Fernet secret.
    - Upserts one record per user.
    """
    validation = validate_gemini_api_key(api_key, settings, client_factory=client_factory)
    if not validation.is_valid:
        raise GeminiKeyValidationError(
            message=validation.message,
            status=validation.status,
            masked_key=validation.masked_key,
        )

    if settings.user_key_encryption_secret is None:
        raise GeminiKeyServiceError(
            "Personal Gemini key encryption secret is not configured on this environment."
        )

    raw_key = api_key.get_secret_value().strip()
    encrypted_ct = encrypt_user_key(raw_key, settings.user_key_encryption_secret)
    hint = mask_user_key(raw_key)
    now = utc_now()

    existing = session.scalar(
        select(UserGeminiSettings).where(UserGeminiSettings.user_id == user_id)
    )
    if existing is not None:
        existing.encrypted_api_key = encrypted_ct
        existing.key_hint = hint
        existing.key_version = 1
        existing.is_enabled = True
        existing.status = "ACTIVE"
        existing.last_validated_at = now
        existing.cooldown_until = None
        record = existing
    else:
        record = UserGeminiSettings(
            user_id=user_id,
            encrypted_api_key=encrypted_ct,
            key_hint=hint,
            key_version=1,
            is_enabled=True,
            status="ACTIVE",
            last_validated_at=now,
            cooldown_until=None,
        )
        session.add(record)

    session.commit()
    session.refresh(record)
    return record


def toggle_personal_key(
    session: Session,
    user_id: UUID,
    is_enabled: bool,
) -> UserGeminiSettings | None:
    """Enable or disable the authenticated user's personal key."""
    record = session.scalar(
        select(UserGeminiSettings).where(UserGeminiSettings.user_id == user_id)
    )
    if record is None:
        return None

    record.is_enabled = is_enabled
    session.commit()
    session.refresh(record)
    return record


def delete_personal_key(session: Session, user_id: UUID) -> bool:
    """Delete the authenticated user's stored personal key record."""
    record = session.scalar(
        select(UserGeminiSettings).where(UserGeminiSettings.user_id == user_id)
    )
    if record is None:
        return False

    session.delete(record)
    session.commit()
    return True


def decrypt_personal_key(
    session: Session,
    record: UserGeminiSettings,
    settings: Settings,
) -> str:
    """Decrypt the stored personal key for internal agent use.

    - Catches corruption and marks status as CORRUPTED.
    - Never leaks plaintext key or ciphertext in logs or exceptions.
    """
    if settings.user_key_encryption_secret is None:
        raise GeminiKeyServiceError("Personal key encryption secret is not configured.")

    try:
        return decrypt_user_key(record.encrypted_api_key, settings.user_key_encryption_secret)
    except KeyEncryptionError as exc:
        logger.warning(
            "Personal key decryption failure for user %s: ciphertext corrupted or wrong secret",
            record.user_id,
        )
        record.status = "CORRUPTED"
        session.commit()
        raise GeminiKeyServiceError("Stored personal API key is corrupted or unreadable.") from exc
