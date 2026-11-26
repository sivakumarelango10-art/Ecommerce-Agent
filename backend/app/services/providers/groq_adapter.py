from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
import json
import logging
import math
import re
import socket
import time
from typing import Any, Callable
from uuid import uuid4

import httpx
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.config import Settings
from backend.app.core.datetime_utils import utc_now
from backend.app.db.models import AIProvider, PlatformAIKey, PlatformKeyStatus
from backend.app.security.key_encryption import (
    KeyEncryptionError,
    decrypt_platform_key,
    mask_platform_key,
)
from backend.app.services.providers.base import (
    AIProviderAdapter,
    AIProviderAuthenticationError,
    AIProviderBadRequestError,
    AIProviderError,
    AIProviderQuotaExhaustedError,
    AIProviderRateLimitError,
    AIProviderResponseError,
    AIProviderTimeoutError,
    AIProviderTransientError,
    GenerationRequest,
    GenerationResponse,
    ProviderCapabilities,
    TokenUsage,
)
from backend.app.services.providers.security import (
    DEFAULT_OUTBOUND_TIMEOUT_SECONDS,
    MAX_SAFE_RESPONSE_BYTES,
    AIProviderSSRFError,
    build_safe_provider_http_client,
    is_trusted_provider_host,
    mask_sensitive_token,
    read_safe_response_body,
    validate_provider_endpoint,
)
from backend.app.services.providers.telemetry import (
    NormalizedTelemetryRecord,
    record_normalized_telemetry,
)

logger = logging.getLogger(__name__)

GROQ_PROVIDER_CODE = "groq"
GROQ_DISPLAY_NAME = "Groq"
DEFAULT_GROQ_BASE_URL = "https://api.groq.com/openai/v1"
# Groq adapter's candidate default model (Groq remains PLANNED; Gemini is the active production default)
DEFAULT_GROQ_MODEL = "openai/gpt-oss-120b"
GROQ_CHAT_COMPLETIONS_PATH = "/chat/completions"


# ---------------------------------------------------------------------------
# Official Groq Model Capabilities & Classification Registry
# Verified as of: November 25, 2026
# Sourced from official Groq documentation:
# - https://console.groq.com/docs/models
# - https://console.groq.com/docs/deprecations
# - https://console.groq.com/docs/api-reference
# - https://console.groq.com/docs/projects
# - https://console.groq.com/docs/rate-limits
# ---------------------------------------------------------------------------

class GroqModelTask(str, Enum):
    """Categorized task domain supported by a Groq model."""
    CHAT_COMPLETION = "chat_completion"
    AUDIO_TRANSCRIPTION = "audio_transcription"
    AUDIO_TRANSLATION = "audio_translation"
    MODERATION = "moderation"


class GroqModelStatus(str, Enum):
    """Lifecycle / production availability status of a Groq model."""
    PRODUCTION = "production"
    ENTERPRISE_RESTRICTED = "enterprise_restricted"
    PREVIEW = "preview"
    DEPRECATED = "deprecated"


@dataclass(frozen=True)
class GroqModelSpec:
    """Exact specification and capabilities of a documented Groq model."""
    model_id: str
    task: GroqModelTask
    status: GroqModelStatus
    context_window: int | None = None
    max_output_tokens: int | None = None
    access_class: str = "PAY_AS_YOU_GO"


# Official Groq Model Capability Registry
# Explicit allowlist: NO generic prefix matching is permitted.
GROQ_MODEL_REGISTRY: dict[str, GroqModelSpec] = {
    # --- Current Documented Production Chat Completion Models (Nov 2026) ---
    "openai/gpt-oss-120b": GroqModelSpec(
        model_id="openai/gpt-oss-120b",
        task=GroqModelTask.CHAT_COMPLETION,
        status=GroqModelStatus.PRODUCTION,
        context_window=131072,
        max_output_tokens=65536,
        access_class="PAY_AS_YOU_GO",
    ),
    "openai/gpt-oss-20b": GroqModelSpec(
        model_id="openai/gpt-oss-20b",
        task=GroqModelTask.CHAT_COMPLETION,
        status=GroqModelStatus.PRODUCTION,
        context_window=131072,
        max_output_tokens=65536,
        access_class="PAY_AS_YOU_GO",
    ),

    # --- Enterprise-Restricted Chat Models ---
    # Free/developer-tier deprecation effective August 16, 2026; requires enterprise tier entitlement
    "llama-3.3-70b-versatile": GroqModelSpec(
        model_id="llama-3.3-70b-versatile",
        task=GroqModelTask.CHAT_COMPLETION,
        status=GroqModelStatus.ENTERPRISE_RESTRICTED,
        context_window=131072,
        max_output_tokens=32768,
        access_class="UNKNOWN",
    ),
    "llama-3.1-8b-instant": GroqModelSpec(
        model_id="llama-3.1-8b-instant",
        task=GroqModelTask.CHAT_COMPLETION,
        status=GroqModelStatus.ENTERPRISE_RESTRICTED,
        context_window=131072,
        max_output_tokens=8192,
        access_class="UNKNOWN",
    ),

    # --- Active Production Audio Models (NOT /chat/completions) ---
    "whisper-large-v3": GroqModelSpec(
        model_id="whisper-large-v3",
        task=GroqModelTask.AUDIO_TRANSCRIPTION,
        status=GroqModelStatus.PRODUCTION,
        context_window=None,
        max_output_tokens=None,
        access_class="PAY_AS_YOU_GO",
    ),
    "whisper-large-v3-turbo": GroqModelSpec(
        model_id="whisper-large-v3-turbo",
        task=GroqModelTask.AUDIO_TRANSCRIPTION,
        status=GroqModelStatus.PRODUCTION,
        context_window=None,
        max_output_tokens=None,
        access_class="PAY_AS_YOU_GO",
    ),

    # --- Deprecated / Decommissioned Models ---
    # deepseek-r1-distill-llama-70b: Deprecated October 2, 2025 (official replacements: llama-3.3-70b-versatile or openai/gpt-oss-120b)
    "deepseek-r1-distill-llama-70b": GroqModelSpec(
        model_id="deepseek-r1-distill-llama-70b",
        task=GroqModelTask.CHAT_COMPLETION,
        status=GroqModelStatus.DEPRECATED,
        context_window=131072,
        max_output_tokens=None,
        access_class="DEPRECATED",
    ),
    # mixtral-8x7b-32768: Deprecated March 20, 2025
    "mixtral-8x7b-32768": GroqModelSpec(
        model_id="mixtral-8x7b-32768",
        task=GroqModelTask.CHAT_COMPLETION,
        status=GroqModelStatus.DEPRECATED,
        context_window=32768,
        max_output_tokens=32768,
        access_class="DEPRECATED",
    ),
    # gemma2-9b-it: Deprecated October 8, 2025
    "gemma2-9b-it": GroqModelSpec(
        model_id="gemma2-9b-it",
        task=GroqModelTask.CHAT_COMPLETION,
        status=GroqModelStatus.DEPRECATED,
        context_window=8192,
        max_output_tokens=8192,
        access_class="DEPRECATED",
    ),
    # qwen-qwq-32b: Decommissioned on Groq
    "qwen-qwq-32b": GroqModelSpec(
        model_id="qwen-qwq-32b",
        task=GroqModelTask.CHAT_COMPLETION,
        status=GroqModelStatus.DEPRECATED,
        context_window=131072,
        max_output_tokens=None,
        access_class="DEPRECATED",
    ),
    # distil-whisper-large-v3-en: Deprecated in favor of whisper-large-v3-turbo
    "distil-whisper-large-v3-en": GroqModelSpec(
        model_id="distil-whisper-large-v3-en",
        task=GroqModelTask.AUDIO_TRANSCRIPTION,
        status=GroqModelStatus.DEPRECATED,
        context_window=None,
        max_output_tokens=None,
        access_class="DEPRECATED",
    ),
    # llama-guard-3-8b: Deprecated June 6, 2025
    "llama-guard-3-8b": GroqModelSpec(
        model_id="llama-guard-3-8b",
        task=GroqModelTask.MODERATION,
        status=GroqModelStatus.DEPRECATED,
        context_window=8192,
        max_output_tokens=None,
        access_class="DEPRECATED",
    ),
    # Llama 3.2 preview models: Decommissioned April 14, 2025 (or earlier for 90b-text)
    "llama-3.2-1b-preview": GroqModelSpec(
        model_id="llama-3.2-1b-preview",
        task=GroqModelTask.CHAT_COMPLETION,
        status=GroqModelStatus.DEPRECATED,
        context_window=131072,
        max_output_tokens=8192,
        access_class="DEPRECATED",
    ),
    "llama-3.2-3b-preview": GroqModelSpec(
        model_id="llama-3.2-3b-preview",
        task=GroqModelTask.CHAT_COMPLETION,
        status=GroqModelStatus.DEPRECATED,
        context_window=131072,
        max_output_tokens=8192,
        access_class="DEPRECATED",
    ),
    "llama-3.2-11b-vision-preview": GroqModelSpec(
        model_id="llama-3.2-11b-vision-preview",
        task=GroqModelTask.CHAT_COMPLETION,
        status=GroqModelStatus.DEPRECATED,
        context_window=131072,
        max_output_tokens=8192,
        access_class="DEPRECATED",
    ),
    "llama-3.2-90b-vision-preview": GroqModelSpec(
        model_id="llama-3.2-90b-vision-preview",
        task=GroqModelTask.CHAT_COMPLETION,
        status=GroqModelStatus.DEPRECATED,
        context_window=131072,
        max_output_tokens=8192,
        access_class="DEPRECATED",
    ),
    "llama-3.2-90b-text-preview": GroqModelSpec(
        model_id="llama-3.2-90b-text-preview",
        task=GroqModelTask.CHAT_COMPLETION,
        status=GroqModelStatus.DEPRECATED,
        context_window=131072,
        max_output_tokens=8192,
        access_class="DEPRECATED",
    ),
    "llama-3.1-70b-versatile": GroqModelSpec(
        model_id="llama-3.1-70b-versatile",
        task=GroqModelTask.CHAT_COMPLETION,
        status=GroqModelStatus.DEPRECATED,
        context_window=131072,
        max_output_tokens=8000,
        access_class="DEPRECATED",
    ),
    "llama3-70b-8192": GroqModelSpec(
        model_id="llama3-70b-8192",
        task=GroqModelTask.CHAT_COMPLETION,
        status=GroqModelStatus.DEPRECATED,
        context_window=8192,
        max_output_tokens=8192,
        access_class="DEPRECATED",
    ),
    "llama3-8b-8192": GroqModelSpec(
        model_id="llama3-8b-8192",
        task=GroqModelTask.CHAT_COMPLETION,
        status=GroqModelStatus.DEPRECATED,
        context_window=8192,
        max_output_tokens=8192,
        access_class="DEPRECATED",
    ),
}

# Production chat models allowlist (exact match only; excludes enterprise-restricted, audio, preview, and deprecated)
SUPPORTED_GROQ_CHAT_MODELS: set[str] = {
    spec.model_id
    for spec in GROQ_MODEL_REGISTRY.values()
    if spec.task == GroqModelTask.CHAT_COMPLETION and spec.status == GroqModelStatus.PRODUCTION
}

# Backward compatibility alias
SUPPORTED_GROQ_MODELS: set[str] = SUPPORTED_GROQ_CHAT_MODELS


# ---------------------------------------------------------------------------
# Header & Rate Limit Parsing Helpers
# ---------------------------------------------------------------------------

def _parse_cooldown_header(val: str | None) -> float | None:
    """Parse retry duration from headers or text (e.g. '2', '2s', '1m30s', '500ms')."""
    if not val:
        return None
    clean = val.strip().lower()
    try:
        delay = float(clean)
        return max(0.1, min(86400.0, delay)) if math.isfinite(delay) and delay > 0 else None
    except ValueError:
        pass

    # Suffix ms
    if clean.endswith("ms"):
        try:
            ms_val = float(clean[:-2].strip())
            delay = ms_val / 1000.0
            return max(0.1, min(86400.0, delay)) if math.isfinite(delay) and delay > 0 else None
        except ValueError:
            pass

    # Suffix s (with optional minute part e.g. '1m30s')
    if clean.endswith("s") and not clean.endswith("ms"):
        clean_s = clean[:-1].strip()
        if "m" in clean_s:
            parts = clean_s.split("m", 1)
            try:
                mins = float(parts[0])
                secs = float(parts[1]) if parts[1] else 0.0
                delay = mins * 60.0 + secs
                return max(0.1, min(86400.0, delay)) if math.isfinite(delay) and delay > 0 else None
            except ValueError:
                pass
        try:
            delay = float(clean_s)
            return max(0.1, min(86400.0, delay)) if math.isfinite(delay) and delay > 0 else None
        except ValueError:
            pass

    # Suffix m (e.g. '2m')
    if clean.endswith("m"):
        try:
            delay = float(clean[:-1].strip()) * 60.0
            return max(0.1, min(86400.0, delay)) if math.isfinite(delay) and delay > 0 else None
        except ValueError:
            pass

    return None


def _extract_groq_cooldown_and_limit_type(
    response: httpx.Response,
    error_json: dict[str, Any] | None,
) -> tuple[float | None, bool]:
    """Extract cooldown duration and determine if 429 is a daily quota exhaustion.

    Official Groq documented headers:
    - `retry-after`: Provided on HTTP 429 errors (seconds to wait before retry).
    - `x-ratelimit-reset-requests`: Time remaining until Requests Per Day (RPD) resets (NOT RPM).
    - `x-ratelimit-reset-tokens`: Time remaining until Tokens Per Minute (TPM) window refills.
    Note: ITPM and OTPM are organization-level quota dimensions configured in console settings,
    not separate HTTP response reset headers.

    Returns (cooldown_seconds, is_daily_quota_exhaustion).
    """
    # 1. Retry-After header (primary on 429)
    retry_after = response.headers.get("retry-after")
    cooldown = _parse_cooldown_header(retry_after)

    # 2. Documented Rate Limit Reset Headers:
    # x-ratelimit-reset-requests: Requests Per Day (RPD)
    is_rpd = False
    if cooldown is None:
        reset_req = response.headers.get("x-ratelimit-reset-requests")
        if reset_req:
            cooldown = _parse_cooldown_header(reset_req)
            if cooldown is not None:
                is_rpd = True

    # x-ratelimit-reset-tokens: Tokens Per Minute (TPM)
    if cooldown is None:
        reset_tok = response.headers.get("x-ratelimit-reset-tokens")
        cooldown = _parse_cooldown_header(reset_tok)

    # 3. Inspect error message if available
    error_msg = ""
    error_code = ""
    error_type = ""
    if error_json and isinstance(error_json, dict):
        err_obj = error_json.get("error")
        if isinstance(err_obj, dict):
            error_msg = str(err_obj.get("message", "")).lower()
            error_code = str(err_obj.get("code", "")).lower()
            error_type = str(err_obj.get("type", "")).lower()

    if cooldown is None and error_msg:
        match = re.search(r"try\s+again\s+in\s+([0-9]+(?:\.[0-9]+)?(?:ms|s|m)?)", error_msg)
        if match:
            cooldown = _parse_cooldown_header(match.group(1))

    # Check if daily quota / exhaustion (RPD or daily tokens)
    is_daily = is_rpd or bool(
        "daily" in error_msg
        or "rpd" in error_msg
        or "tpd" in error_msg
        or "insufficient_quota" in error_type
        or "daily_limit_exceeded" in error_code
        or "quota exceeded" in error_msg
        or "credit balance" in error_msg
    )

    return cooldown, is_daily


def _sanitize_groq_error_message(raw_msg: str) -> str:
    """Sanitize upstream error messages by stripping internal IDs, URLs, and secrets."""
    if not raw_msg:
        return "Groq request failed."
    # Redact organization identifiers
    sanitized = re.sub(r"org_[a-zA-Z0-9]+", "org_***", raw_msg)
    # Redact project identifiers
    sanitized = re.sub(r"proj_[a-zA-Z0-9]+", "proj_***", sanitized)
    # Redact potential API keys
    sanitized = re.sub(r"gsk_[a-zA-Z0-9]+", "gsk_***", sanitized)
    # Redact documentation / console URLs
    sanitized = re.sub(r"https?://\S+", "", sanitized).strip()
    return sanitized or "Groq provider error."


@dataclass
class GroqKeySlot:
    """Represents a company platform API key slot with quota tracking and cooldown.

    Limitation note: In-memory cooldown tracking is scoped per application process.
    Cross-worker cooldown synchronization across distributed instances requires a
    shared Redis/cache or database lease backend.
    """

    key_id: str
    key_secret: SecretStr
    key_hint: str
    quota_group: str = "default"
    is_enabled: bool = True
    status: str = PlatformKeyStatus.ACTIVE.value
    cooldown_until: datetime | None = None
    last_used_at: datetime | None = None

    def is_active(self, now: datetime | None = None) -> bool:
        """Check if key slot is active, enabled, and outside any active cooldown period."""
        if not self.is_enabled:
            return False
        if self.status != PlatformKeyStatus.ACTIVE.value:
            return False
        if self.cooldown_until is not None:
            current_time = now or utc_now()
            cu = self.cooldown_until
            if cu.tzinfo is None:
                cu = cu.replace(tzinfo=timezone.utc)
            if cu > current_time:
                return False
        return True


class GroqProviderAdapter(AIProviderAdapter):
    """Production provider adapter wrapping Groq Cloud AI runtime.

    Key Features:
    - Implements AIProviderAdapter base contract.
    - Exact model allowlist: NO generic prefix matching. Rejects audio/preview/deprecated models in chat.
    - Uses hardened HTTPS transport (TLS verification, IP pinning, DNS rebinding prevention, 10MB body streaming limit, redirects blocked).
    - Platform key vault integration: decrypts PlatformAIKey using HKDF domain separation ('purpose=platform').
    - Disables personal-key selection for Groq in this phase to prevent Gemini credential cross-contamination.
    - Database platform key vault strictly takes precedence over static environment keys.
    - Multi-key rotation across quota groups with shared organization-level rate limit propagation.
    - Safe error sanitization and unexecuted structured tool argument validation.
    - Zero interference with existing Gemini default runtime.
    """

    def __init__(
        self,
        *,
        api_key: SecretStr | str | None = None,
        key_slots: Sequence[GroqKeySlot] | None = None,
        allow_static_fallback: bool = False,
        default_model: str = DEFAULT_GROQ_MODEL,
        base_url: str = DEFAULT_GROQ_BASE_URL,
        http_client: httpx.Client | None = None,
        default_timeout_seconds: float = DEFAULT_OUTBOUND_TIMEOUT_SECONDS,
        allow_localhost_for_testing: bool = False,
        enforce_trusted_hosts: bool = True,
        db_session: Session | None = None,
        on_key_status_change: Callable[[str, str, datetime | None], None] | None = None,
    ) -> None:
        self._default_model = default_model
        self._default_timeout = default_timeout_seconds
        self._allow_localhost_for_testing = allow_localhost_for_testing
        self._enforce_trusted_hosts = enforce_trusted_hosts
        self._db_session = db_session
        self._on_key_status_change = on_key_status_change
        self._allow_static_fallback = allow_static_fallback

        # Validate base endpoint
        validated_url = validate_provider_endpoint(
            base_url,
            allow_localhost_for_testing=allow_localhost_for_testing,
            enforce_trusted_hosts=enforce_trusted_hosts and not allow_localhost_for_testing,
            provider_code=GROQ_PROVIDER_CODE,
        )
        self._base_url = (validated_url or DEFAULT_GROQ_BASE_URL).rstrip("/")

        # Key storage: Database vault slots take precedence over static company key
        self._key_slots: list[GroqKeySlot] = list(key_slots or [])
        self._static_key: SecretStr | None = (
            SecretStr(api_key.get_secret_value() if isinstance(api_key, SecretStr) else api_key.strip())
            if api_key
            else None
        )

        # HTTP Client management
        if http_client is not None:
            self._client = http_client
            self._owns_client = False
        else:
            self._client = build_safe_provider_http_client(
                timeout_seconds=default_timeout_seconds,
                allow_localhost_for_testing=allow_localhost_for_testing,
            )
            self._owns_client = True

    @property
    def provider_code(self) -> str:
        return GROQ_PROVIDER_CODE

    @property
    def display_name(self) -> str:
        return GROQ_DISPLAY_NAME

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def default_model(self) -> str:
        return self._default_model

    def get_capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            supports_text=True,
            supports_structured_output=False,
            supports_function_calling=True,
            supports_streaming=False,
            supports_vision=False,
            max_context_window=131072,  # 128k context window
            max_output_tokens=65536,   # Up to 64k tokens for GPT-OSS
        )

    def supports_model(self, model: str) -> bool:
        """Check whether the model is in the exact documented production chat model allowlist."""
        clean = (model or "").strip().lower()
        if not clean:
            return False
        return clean in SUPPORTED_GROQ_CHAT_MODELS

    def _validate_model_request(
        self,
        model_name: str,
        request: GenerationRequest | None = None,
    ) -> str:
        """Validate model against the capability registry, rejecting audio, preview, and deprecated models."""
        clean = (model_name or "").strip().lower()
        if not clean:
            raise AIProviderBadRequestError(
                "Model identifier must not be empty.",
                provider_code=self.provider_code,
                model=model_name,
                status_code=400,
            )

        spec = GROQ_MODEL_REGISTRY.get(clean)
        if spec is None:
            raise AIProviderBadRequestError(
                f"Model '{clean}' is not registered under provider '{self.provider_code}'.",
                provider_code=self.provider_code,
                model=clean,
                status_code=400,
            )

        if spec.status == GroqModelStatus.DEPRECATED:
            raise AIProviderBadRequestError(
                f"Model '{clean}' was deprecated on Groq and cannot be selected.",
                provider_code=self.provider_code,
                model=clean,
                status_code=400,
            )

        if spec.task != GroqModelTask.CHAT_COMPLETION:
            raise AIProviderBadRequestError(
                f"Model '{clean}' is a {spec.task.value} model and cannot be invoked via chat completions endpoint.",
                provider_code=self.provider_code,
                model=clean,
                status_code=400,
            )

        if spec.status == GroqModelStatus.ENTERPRISE_RESTRICTED:
            # StoreFlow contains no authoritative, server-side committed-spend enterprise contract
            # verification system. Client-supplied metadata or request booleans
            # (e.g. metadata["is_enterprise_tier"]) MUST NOT be trusted to grant access.
            # Enterprise-restricted models strictly fail closed for all callers.
            raise AIProviderBadRequestError(
                f"Model '{clean}' is enterprise-restricted on Groq (developer tier deprecated Aug 16, 2026) "
                "and cannot be invoked because StoreFlow has no verified contractual committed-spend enterprise entitlement.",
                provider_code=self.provider_code,
                model=clean,
                status_code=400,
            )

        if spec.status == GroqModelStatus.PREVIEW:
            raise AIProviderBadRequestError(
                f"Model '{clean}' is a preview model and is not permitted in production chat.",
                provider_code=self.provider_code,
                model=clean,
                status_code=400,
            )

        return spec.model_id

    def _select_key(
        self, request: GenerationRequest
    ) -> tuple[SecretStr, str, str | None, GroqKeySlot | None]:
        """Select the highest-priority eligible key for the request.

        Policy & Precedence:
        1. Personal Keys: Disabled for Groq in this phase to prevent Gemini key cross-contamination.
           Any `request.user_key` is explicitly ignored.
        2. Database Key Vault: Active platform key slots (`_key_slots`) take absolute precedence.
        3. Fail-Closed Safety: If database vault slots exist but none are active, do NOT fall back
           to a static company key unless `allow_static_fallback=True`.
        4. Static Company Key: Used when database vault has no slots provisioned, or if fallback is explicitly allowed.
        """
        # 1. Guard against personal-key reuse from Gemini
        if request.user_key:
            logger.debug(
                "Personal user keys are disabled for Groq in this phase to prevent credential cross-contamination; using company key pool."
            )

        # 2. Database Platform Key Slots (Primary source of truth)
        if self._key_slots:
            now = utc_now()
            active_slots = [slot for slot in self._key_slots if slot.is_active(now)]

            if not active_slots:
                # If static fallback is explicitly allowed and static key is present, fallback with log
                if self._allow_static_fallback and self._static_key:
                    logger.warning(
                        "All vault key slots unavailable; using configured static fallback key."
                    )
                    return self._static_key, "COMPANY", "static_fallback_key", None

                # Otherwise FAIL CLOSED: do not silently fall back to static key
                # Check if all keys are currently cooling down
                cooldown_slots = [
                    s for s in self._key_slots
                    if s.is_enabled
                    and s.status == PlatformKeyStatus.ACTIVE.value
                    and s.cooldown_until is not None
                    and s.cooldown_until > now
                ]
                if cooldown_slots:
                    min_remaining = min(
                        (s.cooldown_until - now).total_seconds() for s in cooldown_slots if s.cooldown_until
                    )
                    raise AIProviderRateLimitError(
                        f"All {len(cooldown_slots)} active Groq API keys are currently cooling down.",
                        provider_code=self.provider_code,
                        retry_after_seconds=max(1.0, min_remaining),
                    )

                # Check if all keys are exhausted
                exhausted_slots = [
                    s for s in self._key_slots if s.status == PlatformKeyStatus.QUOTA_EXHAUSTED.value
                ]
                if exhausted_slots:
                    raise AIProviderQuotaExhaustedError(
                        "All configured Groq API keys have exhausted their quota.",
                        provider_code=self.provider_code,
                    )

                raise AIProviderAuthenticationError(
                    "No active or valid Groq platform keys available in database vault.",
                    provider_code=self.provider_code,
                )

            # Quota-group aware rotation: pick the slot with earliest last_used_at
            selected_slot = min(
                active_slots,
                key=lambda s: s.last_used_at or datetime.min.replace(tzinfo=timezone.utc),
            )
            selected_slot.last_used_at = now
            return selected_slot.key_secret, "PLATFORM_KEY", selected_slot.key_hint, selected_slot

        # 3. Static Company Key (Used ONLY when database vault has NO slots configured)
        if self._static_key:
            return self._static_key, "COMPANY", "static_company_key", None

        raise AIProviderAuthenticationError(
            "No Groq API keys configured.",
            provider_code=self.provider_code,
        )

    def _mark_slot_cooldown(
        self,
        slot: GroqKeySlot,
        delay_seconds: float,
        *,
        is_daily: bool = False,
    ) -> None:
        """Apply cooldown or quota exhaustion to key slot and its quota group."""
        now = utc_now()
        effective_delay = max(1.0, min(86400.0, delay_seconds))
        if is_daily:
            slot.status = PlatformKeyStatus.QUOTA_EXHAUSTED.value
            slot.cooldown_until = now + timedelta(days=1)
            # Shared organization/project quota exhaustion propagates to all keys in quota group
            for s in self._key_slots:
                if s.quota_group == slot.quota_group:
                    s.status = PlatformKeyStatus.QUOTA_EXHAUSTED.value
                    s.cooldown_until = slot.cooldown_until
        else:
            cooldown_time = now + timedelta(seconds=effective_delay)
            slot.cooldown_until = cooldown_time
            # Shared organization/project rate limit propagates to all keys in quota group
            for s in self._key_slots:
                if s.quota_group == slot.quota_group:
                    s.cooldown_until = cooldown_time

        logger.warning(
            "Applied Groq key slot status change",
            extra={
                "key_hint": slot.key_hint,
                "quota_group": slot.quota_group,
                "status": slot.status,
                "cooldown_seconds": effective_delay,
                "is_daily": is_daily,
            },
        )

        if self._on_key_status_change is not None:
            try:
                self._on_key_status_change(slot.key_id, slot.status, slot.cooldown_until)
            except Exception as exc:
                logger.warning("Error in key status callback: %s", exc)

    def _mark_slot_invalid(self, slot: GroqKeySlot) -> None:
        """Mark a key slot as invalid upon receiving 401/403 credential rejection."""
        slot.status = PlatformKeyStatus.INVALID.value
        logger.error(
            "Marked Groq key slot as INVALID due to authentication rejection",
            extra={"key_hint": slot.key_hint, "quota_group": slot.quota_group},
        )
        if self._on_key_status_change is not None:
            try:
                self._on_key_status_change(slot.key_id, slot.status, None)
            except Exception as exc:
                logger.warning("Error in key status callback: %s", exc)

    def _build_payload(self, request: GenerationRequest, model_name: str) -> dict[str, Any]:
        """Convert GenerationRequest into documented Groq chat completion request payload."""
        messages: list[dict[str, Any]] = []

        # System instruction
        if request.system_instruction:
            messages.append({"role": "system", "content": str(request.system_instruction)})

        # User contents or prompt
        if request.contents is not None:
            if isinstance(request.contents, str):
                messages.append({"role": "user", "content": request.contents})
            elif isinstance(request.contents, list):
                for item in request.contents:
                    if isinstance(item, dict) and "role" in item and "content" in item:
                        messages.append(item)
                    else:
                        messages.append({"role": "user", "content": str(item)})
            else:
                messages.append({"role": "user", "content": str(request.contents)})
        elif request.prompt:
            messages.append({"role": "user", "content": str(request.prompt)})
        else:
            raise AIProviderBadRequestError(
                "Either prompt or contents must be provided in GenerationRequest.",
                provider_code=self.provider_code,
                model=model_name,
                status_code=400,
            )

        # Only documented Groq request fields
        payload: dict[str, Any] = {
            "model": model_name,
            "messages": messages,
        }

        if request.temperature is not None:
            payload["temperature"] = max(0.0, min(2.0, float(request.temperature)))

        if request.max_tokens is not None:
            payload["max_tokens"] = int(request.max_tokens)

        if request.tools:
            # Map tools for OpenAI/Groq function calling format
            payload["tools"] = list(request.tools)
            if request.metadata and "tool_choice" in request.metadata:
                payload["tool_choice"] = request.metadata["tool_choice"]

        return payload

    def generate(self, request: GenerationRequest) -> GenerationResponse:
        """Synchronously execute chat completion against Groq API with hardened transport."""
        model_name = request.model or self._default_model
        if request.response_schema is not None:
            raise AIProviderBadRequestError(
                "Structured output is not supported by Groq provider adapter.",
                provider_code=self.provider_code,
                model=model_name,
                status_code=400,
            )
        # Exact model allowlist validation
        validated_model = self._validate_model_request(model_name, request=request)

        # 1. Select key (personal keys disabled; DB vault prioritised over static)
        api_key, key_source, key_hint, slot = self._select_key(request)

        # 2. Build payload with documented fields only
        payload = self._build_payload(request, validated_model)

        # 3. Build headers with secret token (never log headers)
        headers = {
            "Authorization": f"Bearer {api_key.get_secret_value()}",
            "Content-Type": "application/json",
            "User-Agent": "StoreFlow-AI/1.0",
        }

        endpoint_url = f"{self._base_url}{GROQ_CHAT_COMPLETIONS_PATH}"
        timeout_val = request.timeout_seconds if request.timeout_seconds > 0 else self._default_timeout

        max_retries = 2
        attempt = 0
        start_time = time.perf_counter()

        while attempt <= max_retries:
            attempt += 1
            try:
                http_req = self._client.build_request(
                    "POST",
                    endpoint_url,
                    json=payload,
                    headers=headers,
                    timeout=timeout_val,
                )
                response = self._client.send(http_req, stream=True)
                body_bytes = read_safe_response_body(response, max_bytes=MAX_SAFE_RESPONSE_BYTES)
                if response.status_code in (500, 502, 503, 504) and attempt <= max_retries:
                    retry_header = response.headers.get("retry-after")
                    backoff = _parse_cooldown_header(retry_header) or (0.05 * (2 ** (attempt - 1)))
                    time.sleep(min(backoff, 2.0))
                    continue
                break
            except (httpx.TimeoutException, socket.timeout) as exc:
                if attempt <= max_retries:
                    time.sleep(0.1 * (2 ** (attempt - 1)))
                    continue
                latency_ms = (time.perf_counter() - start_time) * 1000.0
                self._record_telemetry(
                    request=request,
                    model_name=validated_model,
                    key_source=key_source,
                    key_hint=key_hint,
                    status="FAILED",
                    latency_ms=latency_ms,
                    error_category="TIMEOUT",
                )
                raise AIProviderTimeoutError(
                    f"Groq request timed out after {timeout_val}s.",
                    provider_code=self.provider_code,
                    model=validated_model,
                ) from exc
            except AIProviderSSRFError:
                raise
            except AIProviderBadRequestError:
                raise
            except httpx.RequestError as exc:
                if attempt <= max_retries:
                    time.sleep(0.1 * (2 ** (attempt - 1)))
                    continue
                latency_ms = (time.perf_counter() - start_time) * 1000.0
                self._record_telemetry(
                    request=request,
                    model_name=validated_model,
                    key_source=key_source,
                    key_hint=key_hint,
                    status="FAILED",
                    latency_ms=latency_ms,
                    error_category="TRANSIENT_ERROR",
                )
                raise AIProviderTransientError(
                    "Network error connecting to Groq infrastructure.",
                    provider_code=self.provider_code,
                    model=validated_model,
                ) from exc

        latency_ms = (time.perf_counter() - start_time) * 1000.0
        status_code = response.status_code

        # Attempt to decode JSON payload safely
        data: dict[str, Any] | None = None
        if body_bytes:
            try:
                data = json.loads(body_bytes.decode("utf-8"))
            except Exception:
                data = None

        # 4. Handle HTTP Success (200)
        if status_code == 200:
            if not data or not isinstance(data, dict):
                raise AIProviderResponseError(
                    "Groq returned invalid or empty JSON response body.",
                    provider_code=self.provider_code,
                    model=validated_model,
                    status_code=status_code,
                )

            choices = data.get("choices")
            if not choices or not isinstance(choices, list) or len(choices) == 0:
                raise AIProviderResponseError(
                    "Groq response contained no completion choices.",
                    provider_code=self.provider_code,
                    model=validated_model,
                    status_code=status_code,
                )

            choice = choices[0]
            message = choice.get("message", {}) if isinstance(choice, dict) else {}
            content = message.get("content")
            raw_tool_calls = message.get("tool_calls") or []

            # Parse and validate tool call arguments as UNTRUSTED model output
            # Tools are NEVER executed inside this adapter
            parsed_tool_calls: list[dict[str, Any]] = []
            for tc in raw_tool_calls:
                if not isinstance(tc, dict):
                    continue
                func_obj = tc.get("function") or {}
                raw_args = func_obj.get("arguments", "")
                parsed_args: Any
                try:
                    if isinstance(raw_args, str) and raw_args.strip():
                        parsed_args = json.loads(raw_args)
                    else:
                        parsed_args = raw_args
                except Exception:
                    parsed_args = {"_raw_arguments": raw_args, "_parse_error": "Malformed JSON arguments from model"}

                parsed_tool_calls.append({
                    "id": tc.get("id") or str(uuid4()),
                    "type": "function",
                    "function": {
                        "name": str(func_obj.get("name") or "unknown_function"),
                        "arguments": parsed_args,
                    },
                })

            if content is None and not parsed_tool_calls:
                raise AIProviderResponseError(
                    "Groq response choice contained empty content.",
                    provider_code=self.provider_code,
                    model=validated_model,
                    status_code=status_code,
                )

            # Token usage parsing without fabrication
            usage_data = data.get("usage")
            usage_obj: TokenUsage | None = None
            if usage_data and isinstance(usage_data, dict):
                usage_obj = TokenUsage(
                    input_tokens=usage_data.get("prompt_tokens"),
                    output_tokens=usage_data.get("completion_tokens"),
                    total_tokens=usage_data.get("total_tokens"),
                )

            # Record telemetry
            self._record_telemetry(
                request=request,
                model_name=validated_model,
                key_source=key_source,
                key_hint=key_hint,
                status="SUCCESS",
                latency_ms=latency_ms,
                usage=usage_obj,
            )

            actual_model = str(data.get("model") or validated_model)
            return GenerationResponse(
                text=content,
                function_calls=tuple(parsed_tool_calls),
                raw_content=data,
                usage=usage_obj,
                model=actual_model,
                provider=self.provider_code,
                latency_ms=latency_ms,
            )

        # 5. Handle HTTP Errors (Non-200) with sanitized error messages
        raw_err_msg = ""
        err_type = ""
        err_code = ""
        if data and isinstance(data, dict):
            err_dict = data.get("error")
            if isinstance(err_dict, dict):
                raw_err_msg = str(err_dict.get("message", ""))
                err_type = str(err_dict.get("type", "")).lower()
                err_code = str(err_dict.get("code", "")).lower()

        sanitized_msg = _sanitize_groq_error_message(raw_err_msg)

        # 400 Bad Request
        if status_code == 400:
            self._record_telemetry(
                request=request,
                model_name=validated_model,
                key_source=key_source,
                key_hint=key_hint,
                status="FAILED",
                latency_ms=latency_ms,
                error_category="BAD_REQUEST",
            )
            raise AIProviderBadRequestError(
                f"Groq bad request: {sanitized_msg}",
                provider_code=self.provider_code,
                model=validated_model,
                status_code=400,
            )

        # 401 / 403 Authentication vs Permission Error
        if status_code in (401, 403):
            is_invalid_credential = (
                status_code == 401
                or "invalid_api_key" in err_code
                or "invalid api key" in raw_err_msg.lower()
                or "unauthorized" in err_type
            )

            if is_invalid_credential:
                if slot is not None:
                    self._mark_slot_invalid(slot)
                self._record_telemetry(
                    request=request,
                    model_name=validated_model,
                    key_source=key_source,
                    key_hint=key_hint,
                    status="FAILED",
                    latency_ms=latency_ms,
                    error_category="AUTHENTICATION_FAILED",
                )
                raise AIProviderAuthenticationError(
                    f"Groq authentication failed: {sanitized_msg}",
                    provider_code=self.provider_code,
                    model=validated_model,
                    status_code=status_code,
                )

            # 403 due to model permission or restricted access (do NOT disable key)
            self._record_telemetry(
                request=request,
                model_name=validated_model,
                key_source=key_source,
                key_hint=key_hint,
                status="FAILED",
                latency_ms=latency_ms,
                error_category="PERMISSION_DENIED",
            )
            raise AIProviderBadRequestError(
                f"Groq permission denied: {sanitized_msg}",
                provider_code=self.provider_code,
                model=validated_model,
                status_code=403,
            )

        # 429 Rate Limit / Quota Exhausted
        if status_code == 429:
            cooldown_seconds, is_daily = _extract_groq_cooldown_and_limit_type(response, data)
            delay = cooldown_seconds or 60.0

            if slot is not None:
                self._mark_slot_cooldown(slot, delay, is_daily=is_daily)

            if is_daily:
                self._record_telemetry(
                    request=request,
                    model_name=validated_model,
                    key_source=key_source,
                    key_hint=key_hint,
                    status="COOLDOWN",
                    latency_ms=latency_ms,
                    error_category="QUOTA_EXHAUSTED",
                )
                raise AIProviderQuotaExhaustedError(
                    f"Groq daily quota exhausted: {sanitized_msg}",
                    provider_code=self.provider_code,
                    model=validated_model,
                    status_code=429,
                    retry_after_seconds=delay,
                )

            self._record_telemetry(
                request=request,
                model_name=validated_model,
                key_source=key_source,
                key_hint=key_hint,
                status="COOLDOWN",
                latency_ms=latency_ms,
                error_category="RATE_LIMIT",
            )
            raise AIProviderRateLimitError(
                f"Groq rate limit exceeded: {sanitized_msg}",
                provider_code=self.provider_code,
                model=validated_model,
                status_code=429,
                retry_after_seconds=delay,
            )

        # 500, 502, 503, 504 Transient Server Errors
        if status_code in (500, 502, 503, 504):
            self._record_telemetry(
                request=request,
                model_name=validated_model,
                key_source=key_source,
                key_hint=key_hint,
                status="FAILED",
                latency_ms=latency_ms,
                error_category="TRANSIENT_ERROR",
            )
            raise AIProviderTransientError(
                f"Groq service temporarily unavailable (status={status_code}): {sanitized_msg}",
                provider_code=self.provider_code,
                model=validated_model,
                status_code=status_code,
            )

        # Generic Provider Error fallback
        self._record_telemetry(
            request=request,
            model_name=validated_model,
            key_source=key_source,
            key_hint=key_hint,
            status="FAILED",
            latency_ms=latency_ms,
            error_category="PROVIDER_ERROR",
        )
        raise AIProviderError(
            f"Groq provider error (status={status_code}): {sanitized_msg}",
            provider_code=self.provider_code,
            model=validated_model,
            status_code=status_code,
        )

    def generate_text(self, prompt: str, model: str | None = None, **kwargs: Any) -> str:
        """Execute text generation synchronously, returning plain response text."""
        request = GenerationRequest(
            prompt=prompt,
            model=model or self._default_model,
            **kwargs,
        )
        response = self.generate(request)
        return response.text or ""

    def health_check(self) -> dict[str, Any]:
        """Return provider runtime health status snapshot without exposing secrets."""
        now = utc_now()
        active_slots = [slot for slot in self._key_slots if slot.is_active(now)]
        quota_groups = sorted(list(set(slot.quota_group for slot in self._key_slots)))

        has_keys = bool(active_slots or self._static_key)
        status = "healthy" if has_keys else "degraded"

        return {
            "provider_code": self.provider_code,
            "display_name": self.display_name,
            "status": status,
            "base_url": self._base_url,
            "default_model": self._default_model,
            "total_slots": len(self._key_slots),
            "active_slots": len(active_slots),
            "quota_groups": quota_groups,
            "has_static_key": self._static_key is not None,
            "vault_prioritized": len(self._key_slots) > 0,
            "supported_models_count": len(SUPPORTED_GROQ_CHAT_MODELS),
            "supported_models": sorted(SUPPORTED_GROQ_CHAT_MODELS),
        }

    def _record_telemetry(
        self,
        *,
        request: GenerationRequest,
        model_name: str,
        key_source: str,
        key_hint: str | None,
        status: str,
        latency_ms: float | None = None,
        usage: TokenUsage | None = None,
        error_category: str | None = None,
        fallback_reason: str | None = None,
    ) -> None:
        """Persist normalized telemetry if a database session is available."""
        session = request.metadata.get("db_session") or self._db_session
        if session is None:
            return

        correlation_id = request.correlation_id or str(uuid4())
        record = NormalizedTelemetryRecord(
            correlation_id=correlation_id,
            provider=self.provider_code,
            model_name=model_name,
            key_source=key_source,
            key_slot=key_hint,
            status=status,
            input_tokens=usage.input_tokens if usage else None,
            output_tokens=usage.output_tokens if usage else None,
            total_tokens=usage.total_tokens if usage else None,
            error_category=error_category,
            fallback_reason=fallback_reason,
            latency_ms=latency_ms,
        )
        record_normalized_telemetry(session, record)

    @classmethod
    def from_platform_keys(
        cls,
        keys: Sequence[PlatformAIKey],
        secret: SecretStr,
        *,
        allow_static_fallback: bool = False,
        default_model: str = DEFAULT_GROQ_MODEL,
        base_url: str = DEFAULT_GROQ_BASE_URL,
        http_client: httpx.Client | None = None,
        allow_localhost_for_testing: bool = False,
        enforce_trusted_hosts: bool = True,
        db_session: Session | None = None,
        on_key_status_change: Callable[[str, str, datetime | None], None] | None = None,
    ) -> GroqProviderAdapter:
        """Construct GroqProviderAdapter by decrypting platform AI keys with domain separation.

        Rejects keys from other providers, unverified, revoked, invalid, or exhausted keys.
        """
        slots: list[GroqKeySlot] = []
        for k in keys:
            # Enforce provider isolation
            if hasattr(k, "provider") and k.provider is not None:
                if k.provider.code != GROQ_PROVIDER_CODE:
                    continue
            if not k.is_enabled:
                continue
            if k.status not in (PlatformKeyStatus.ACTIVE.value, PlatformKeyStatus.COOLDOWN.value):
                continue

            try:
                decrypted_key = decrypt_platform_key(k.encrypted_api_key, secret)
            except KeyEncryptionError as exc:
                logger.warning(
                    "Skipping undecryptable Groq key slot %s: %s",
                    k.key_hint,
                    type(exc).__name__,
                )
                continue

            slots.append(
                GroqKeySlot(
                    key_id=str(k.id),
                    key_secret=SecretStr(decrypted_key),
                    key_hint=k.key_hint,
                    quota_group=k.quota_group or "default",
                    is_enabled=k.is_enabled,
                    status=k.status,
                    cooldown_until=k.cooldown_until,
                    last_used_at=k.last_used_at,
                )
            )

        return cls(
            key_slots=slots,
            allow_static_fallback=allow_static_fallback,
            default_model=default_model,
            base_url=base_url,
            http_client=http_client,
            allow_localhost_for_testing=allow_localhost_for_testing,
            enforce_trusted_hosts=enforce_trusted_hosts,
            db_session=db_session,
            on_key_status_change=on_key_status_change,
        )

    @classmethod
    def from_db_session(
        cls,
        session: Session,
        settings: Settings,
        *,
        default_model: str = DEFAULT_GROQ_MODEL,
        base_url: str = DEFAULT_GROQ_BASE_URL,
        http_client: httpx.Client | None = None,
        allow_localhost_for_testing: bool = False,
    ) -> GroqProviderAdapter:
        """Load enabled Groq platform keys from database session and construct adapter."""
        stmt = (
            select(PlatformAIKey)
            .join(AIProvider, AIProvider.id == PlatformAIKey.provider_id)
            .where(
                AIProvider.code == GROQ_PROVIDER_CODE,
                PlatformAIKey.is_enabled.is_(True),
            )
        )
        keys = list(session.scalars(stmt).all())

        def _persist_status_change(key_id: str, new_status: str, cooldown_until: datetime | None) -> None:
            try:
                db_key = session.get(PlatformAIKey, key_id)
                if db_key is not None:
                    db_key.status = new_status
                    db_key.cooldown_until = cooldown_until
                    session.commit()
            except Exception as exc:
                logger.warning("Failed to persist key status update: %s", exc)
                try:
                    session.rollback()
                except Exception:
                    pass

        return cls.from_platform_keys(
            keys,
            secret=settings.encryption_secret,
            default_model=default_model,
            base_url=base_url,
            http_client=http_client,
            allow_localhost_for_testing=allow_localhost_for_testing,
            db_session=session,
            on_key_status_change=_persist_status_change,
        )

    def close(self) -> None:
        """Close the underlying HTTP client if owned by this adapter."""
        if self._owns_client and self._client is not None:
            self._client.close()

    def __enter__(self) -> GroqProviderAdapter:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()
