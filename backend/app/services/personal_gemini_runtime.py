from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import timedelta
import logging
import time
from typing import Any
from uuid import UUID, uuid4

import httpx
from google import genai
from google.genai import errors, types
from pydantic import SecretStr
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from backend.app.core.config import Settings
from backend.app.core.constants import MAX_QUESTION_LENGTH
from backend.app.core.datetime_utils import ensure_utc, utc_now
from backend.app.db.models import GeminiUsageEvent, User, UserGeminiSettings
from backend.app.security.key_encryption import KeyEncryptionError, decrypt_user_key
from backend.app.services.gemini_service import (
    _SYSTEM_INSTRUCTION,
    GeminiProviderError,
    GeminiResponseError,
    GeminiResult,
    GeminiService,
    GeminiServiceError,
    GeminiTimeoutError,
    _extract_cooldown_seconds,
    _is_credential_invalid_error,
)

logger = logging.getLogger(__name__)


def record_gemini_usage(
    session: Session,
    *,
    correlation_id: str,
    user_id: UUID | None,
    provider: str = "google",
    model_name: str,
    key_source: str,  # "PERSONAL" or "COMPANY"
    key_slot: str | None = None,
    attempt_number: int = 1,
    status: str,  # "SUCCESS", "FAILED", "COOLDOWN", "FALLBACK"
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    total_tokens: int | None = None,
    error_category: str | None = None,
    fallback_reason: str | None = None,
    latency_ms: float | None = None,
) -> None:
    """Record durable telemetry for Gemini requests without storing prompts or secrets.

    Safely swallows exceptions so telemetry issues never break AI responses.
    """
    try:
        event = GeminiUsageEvent(
            id=uuid4(),
            correlation_id=correlation_id,
            user_id=user_id,
            provider=provider,
            model_name=model_name,
            key_source=key_source,
            key_slot=key_slot,
            attempt_number=attempt_number,
            status=status,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            error_category=error_category,
            fallback_reason=fallback_reason,
            latency_ms=latency_ms,
            created_at=utc_now(),
        )
        session.add(event)
        session.commit()
    except Exception as exc:
        logger.warning(
            "Failed to record Gemini usage telemetry event",
            extra={"event": "telemetry_persistence_failed", "error_type": type(exc).__name__},
        )
        try:
            session.rollback()
        except Exception:
            pass


def resolve_personal_key(
    session: Session,
    user_id: UUID | None,
    settings: Settings,
) -> tuple[UserGeminiSettings | None, str | None, str | None]:
    """Resolve an authenticated user's personal key in memory.

    Returns (record, decrypted_key_or_none, disqualification_reason).
    Never exposes decrypted keys in logs or exceptions.
    """
    if user_id is None:
        return None, None, "guest_request"

    record = session.scalar(
        select(UserGeminiSettings).where(UserGeminiSettings.user_id == user_id)
    )
    if record is None:
        return None, None, "no_personal_key"

    if not record.is_enabled:
        return record, None, "key_disabled"

    if record.status != "ACTIVE":
        return record, None, f"status_{record.status.lower()}"

    now = utc_now()
    if record.cooldown_until is not None:
        cooldown_utc = ensure_utc(record.cooldown_until)
        if cooldown_utc is not None and cooldown_utc > now:
            return record, None, "cooldown_active"
        # Cooldown expired, clear it safely without affecting non-active states
        session.execute(
            update(UserGeminiSettings)
            .where(
                UserGeminiSettings.user_id == user_id,
                UserGeminiSettings.status.notin_(["INVALID", "CORRUPTED"]),
            )
            .values(cooldown_until=None)
        )
        record.cooldown_until = None
        session.commit()

    if settings.user_key_encryption_secret is None:
        logger.warning("Personal key encryption secret is not configured on environment")
        return record, None, "encryption_secret_missing"

    try:
        decrypted = decrypt_user_key(record.encrypted_api_key, settings.user_key_encryption_secret)
        return record, decrypted, None
    except KeyEncryptionError:
        logger.warning("Decryption failed for user %s: marking status CORRUPTED", user_id)
        record.status = "CORRUPTED"
        session.commit()
        return record, None, "key_corrupted"


class RequestScopedGeminiService:
    """Request-scoped Gemini service providing personal-first execution with company pool fallback.

    Implements the same QuestionAnswerer / GeminiService interface across all agents.
    """

    def __init__(
        self,
        platform_service: GeminiService,
        settings: Settings,
        user: User | None = None,
        session_factory: Callable[[], Session] | None = None,
        correlation_id: str | None = None,
        client_factory: Callable[..., Any] | None = None,
    ) -> None:
        self._platform_service = platform_service
        self._settings = settings
        self._user = user
        self._session_factory = session_factory
        self._correlation_id = correlation_id or str(uuid4())
        self._client_factory = client_factory

    @property
    def platform_service(self) -> GeminiService:
        return self._platform_service

    def _safe_record_telemetry(self, **kwargs: Any) -> None:
        if not self._session_factory:
            return
        try:
            with self._session_factory() as session:
                record_gemini_usage(session, **kwargs)
        except Exception:
            logger.warning("Safe telemetry record encountered exception; swallowed.")

    def generate(
        self,
        contents: str | Sequence[types.Content] | None = None,
        *,
        question: str | None = None,
        tools: Sequence[types.Tool] | None = None,
        system_instruction: str | None = None,
        user_key: SecretStr | None = None,
        response_schema: Any = None,
        response_mime_type: str | None = None,
    ) -> GeminiResult:
        """Execute request using personal key first if eligible, falling back to company pool."""
        # 1. Validate inputs before attempting any key
        if contents is None:
            if question is not None:
                contents = question
            else:
                raise ValueError("question must not be blank")

        if isinstance(contents, str):
            contents_str = contents.strip()
            if not contents_str:
                raise ValueError("question must not be blank")
            if len(contents_str) > MAX_QUESTION_LENGTH:
                raise ValueError(
                    f"question must not exceed {MAX_QUESTION_LENGTH} characters"
                )
            payload_contents: str | list[types.Content] = contents_str
        elif isinstance(contents, Sequence):
            if not contents:
                raise ValueError("contents must not be empty")
            payload_contents = list(contents)
        else:
            payload_contents = contents

        effective_instruction = (
            system_instruction
            if system_instruction is not None
            else _SYSTEM_INSTRUCTION
        )

        user_id = self._user.id if self._user is not None else None
        personal_record: UserGeminiSettings | None = None
        personal_key_str: str | None = None
        disqualification_reason: str | None = None

        # 2. Check personal key eligibility
        if self._session_factory and user_id is not None:
            try:
                with self._session_factory() as session:
                    personal_record, personal_key_str, disqualification_reason = resolve_personal_key(
                        session, user_id, self._settings
                    )
            except Exception:
                logger.warning("Failed to resolve personal key; will proceed to company pool.")
                personal_key_str = None

        # 3. Attempt personal key if eligible
        if personal_key_str is not None:
            factory = (
                self._client_factory
                or getattr(self._platform_service, "_client_factory", None)
                or genai.Client
            )
            http_options = types.HttpOptions(
                timeout=self._settings.gemini_timeout_seconds * 1000
            )
            client = factory(api_key=personal_key_str, http_options=http_options)
            config_kwargs: dict[str, Any] = {
                "system_instruction": effective_instruction,
                "tools": list(tools) if tools else None,
                "automatic_function_calling": types.AutomaticFunctionCallingConfig(
                    disable=True
                ),
            }
            if response_mime_type:
                config_kwargs["response_mime_type"] = response_mime_type
            if response_schema is not None:
                config_kwargs["response_schema"] = response_schema
                if not response_mime_type:
                    config_kwargs["response_mime_type"] = "application/json"
            config = types.GenerateContentConfig(**config_kwargs)

            personal_start = time.time()
            personal_success = False
            response = None
            personal_error: Exception | None = None
            transient_retries = 0
            max_transient_retries = 1

            while True:
                try:
                    response = client.models.generate_content(
                        model=self._settings.gemini_model,
                        contents=payload_contents,
                        config=config,
                    )
                    personal_success = True
                    break
                except (httpx.TimeoutException, TimeoutError) as exc:
                    personal_error = exc
                    transient_retries += 1
                    if transient_retries <= max_transient_retries:
                        time.sleep(0.5)
                        continue
                    break
                except errors.APIError as exc:
                    personal_error = exc
                    if exc.code in (500, 502, 503, 504):
                        transient_retries += 1
                        if transient_retries <= max_transient_retries:
                            time.sleep(0.5)
                            continue
                    break
                except Exception as exc:
                    personal_error = exc
                    break

            latency_ms = (time.time() - personal_start) * 1000.0

            if personal_success and response is not None:
                # Update last_used_at safely without reactivating invalid or corrupted keys
                if self._session_factory and user_id is not None:
                    try:
                        with self._session_factory() as session:
                            session.execute(
                                update(UserGeminiSettings)
                                .where(
                                    UserGeminiSettings.user_id == user_id,
                                    UserGeminiSettings.status.notin_(["INVALID", "CORRUPTED"]),
                                )
                                .values(last_used_at=utc_now())
                            )
                            session.commit()
                    except Exception:
                        pass

                usage_meta = getattr(response, "usage_metadata", None)
                in_tok = getattr(usage_meta, "prompt_token_count", None) if usage_meta else None
                out_tok = getattr(usage_meta, "candidates_token_count", None) if usage_meta else None
                tot_tok = getattr(usage_meta, "total_token_count", None) if usage_meta else None

                self._safe_record_telemetry(
                    correlation_id=self._correlation_id,
                    user_id=user_id,
                    model_name=self._settings.gemini_model,
                    key_source="PERSONAL",
                    key_slot=personal_record.key_hint if personal_record else None,
                    attempt_number=1,
                    status="SUCCESS",
                    input_tokens=in_tok,
                    output_tokens=out_tok,
                    total_tokens=tot_tok,
                    latency_ms=latency_ms,
                )

                return self._format_gemini_response(response)

            # Personal key attempt failed -> classify and record health update
            if personal_error is not None:
                if isinstance(personal_error, errors.APIError):
                    # Quota Exhaustion (429)
                    if personal_error.code == 429 or getattr(personal_error, "status", None) == "RESOURCE_EXHAUSTED":
                        cooldown_secs, is_daily = _extract_cooldown_seconds(personal_error)
                        if self._session_factory and user_id is not None:
                            try:
                                with self._session_factory() as session:
                                    cooldown_timestamp = utc_now() + timedelta(seconds=cooldown_secs)
                                    session.execute(
                                        update(UserGeminiSettings)
                                        .where(
                                            UserGeminiSettings.user_id == user_id,
                                            UserGeminiSettings.status.notin_(["INVALID", "CORRUPTED"]),
                                        )
                                        .values(cooldown_until=cooldown_timestamp)
                                    )
                                    session.commit()
                            except Exception:
                                pass
                        logger.warning(
                            "Personal Gemini key 429 quota exhausted for user %s (cooldown: %.1fs, daily: %s). Falling back.",
                            user_id,
                            cooldown_secs,
                            is_daily,
                        )
                        self._safe_record_telemetry(
                            correlation_id=self._correlation_id,
                            user_id=user_id,
                            model_name=self._settings.gemini_model,
                            key_source="PERSONAL",
                            key_slot=personal_record.key_hint if personal_record else None,
                            attempt_number=1,
                            status="COOLDOWN",
                            error_category="QUOTA_EXHAUSTED",
                            fallback_reason="personal_key_quota_exhausted",
                            latency_ms=latency_ms,
                        )
                    # Invalid or Revoked Credentials (400/403)
                    elif personal_error.code in (400, 403) and _is_credential_invalid_error(personal_error):
                        if self._session_factory and user_id is not None:
                            try:
                                with self._session_factory() as session:
                                    session.execute(
                                        update(UserGeminiSettings)
                                        .where(UserGeminiSettings.user_id == user_id)
                                        .values(status="INVALID")
                                    )
                                    session.commit()
                            except Exception:
                                pass
                        logger.error(
                            "Personal Gemini key invalid/revoked for user %s. Disabling and falling back.",
                            user_id,
                        )
                        self._safe_record_telemetry(
                            correlation_id=self._correlation_id,
                            user_id=user_id,
                            model_name=self._settings.gemini_model,
                            key_source="PERSONAL",
                            key_slot=personal_record.key_hint if personal_record else None,
                            attempt_number=1,
                            status="FAILED",
                            error_category="INVALID_CREDENTIAL",
                            fallback_reason="personal_key_invalid",
                            latency_ms=latency_ms,
                        )
                    # Transient server errors (500, 502, 503, 504)
                    elif personal_error.code in (500, 502, 503, 504):
                        logger.warning(
                            "Personal Gemini key temporary error (%s) for user %s. Falling back.",
                            personal_error.code,
                            user_id,
                        )
                        self._safe_record_telemetry(
                            correlation_id=self._correlation_id,
                            user_id=user_id,
                            model_name=self._settings.gemini_model,
                            key_source="PERSONAL",
                            key_slot=personal_record.key_hint if personal_record else None,
                            attempt_number=1,
                            status="FAILED",
                            error_category="TEMPORARY_FAILURE",
                            fallback_reason="personal_key_transient_error",
                            latency_ms=latency_ms,
                        )
                    else:
                        self._safe_record_telemetry(
                            correlation_id=self._correlation_id,
                            user_id=user_id,
                            model_name=self._settings.gemini_model,
                            key_source="PERSONAL",
                            key_slot=personal_record.key_hint if personal_record else None,
                            attempt_number=1,
                            status="FAILED",
                            error_category="PROVIDER_ERROR",
                            fallback_reason="personal_key_provider_error",
                            latency_ms=latency_ms,
                        )
                elif isinstance(personal_error, (httpx.TimeoutException, TimeoutError)):
                    self._safe_record_telemetry(
                        correlation_id=self._correlation_id,
                        user_id=user_id,
                        model_name=self._settings.gemini_model,
                        key_source="PERSONAL",
                        key_slot=personal_record.key_hint if personal_record else None,
                        attempt_number=1,
                        status="FAILED",
                        error_category="TIMEOUT",
                        fallback_reason="personal_key_timeout",
                        latency_ms=latency_ms,
                    )
                else:
                    self._safe_record_telemetry(
                        correlation_id=self._correlation_id,
                        user_id=user_id,
                        model_name=self._settings.gemini_model,
                        key_source="PERSONAL",
                        key_slot=personal_record.key_hint if personal_record else None,
                        attempt_number=1,
                        status="FAILED",
                        error_category="PROVIDER_ERROR",
                        fallback_reason="personal_key_error",
                        latency_ms=latency_ms,
                    )

        # 4. Fallback to Company Key Pool
        company_attempt = 2 if (personal_key_str is not None) else 1
        company_start = time.time()
        try:
            result = self._platform_service.generate(
                contents=payload_contents,
                question=question,
                tools=tools,
                system_instruction=effective_instruction,
                response_schema=response_schema,
                response_mime_type=response_mime_type,
            )
            company_latency = (time.time() - company_start) * 1000.0

            usage_meta = getattr(result, "usage_metadata", None)
            in_tok = getattr(usage_meta, "prompt_token_count", None) if usage_meta else None
            out_tok = getattr(usage_meta, "candidates_token_count", None) if usage_meta else None
            tot_tok = getattr(usage_meta, "total_token_count", None) if usage_meta else None

            self._safe_record_telemetry(
                correlation_id=self._correlation_id,
                user_id=user_id,
                model_name=self._settings.gemini_model,
                key_source="COMPANY",
                attempt_number=company_attempt,
                status="SUCCESS",
                input_tokens=in_tok,
                output_tokens=out_tok,
                total_tokens=tot_tok,
                latency_ms=company_latency,
            )
            return result
        except Exception as exc:
            company_latency = (time.time() - company_start) * 1000.0
            error_cat = "TIMEOUT" if isinstance(exc, GeminiTimeoutError) else "PROVIDER_ERROR"
            self._safe_record_telemetry(
                correlation_id=self._correlation_id,
                user_id=user_id,
                model_name=self._settings.gemini_model,
                key_source="COMPANY",
                attempt_number=company_attempt,
                status="FAILED",
                error_category=error_cat,
                latency_ms=company_latency,
            )
            raise

    def answer(
        self,
        question: str,
        *,
        user_key: SecretStr | None = None,
    ) -> str:
        """Answer question with personal key first, falling back to company pool."""
        result = self.generate(question=question, user_key=user_key)
        if result.text is None:
            raise GeminiResponseError("Gemini requested a function call without text.")
        return result.text

    def close(self) -> None:
        pass

    def _format_gemini_response(self, response: Any) -> GeminiResult:
        """Format raw SDK response into standard GeminiResult."""
        response_text = getattr(response, "text", None)
        text = (
            response_text.strip()
            if isinstance(response_text, str) and response_text.strip()
            else None
        )
        function_calls = tuple(getattr(response, "function_calls", None) or ())
        if text is None and not function_calls:
            logger.warning(
                "Personal Gemini key returned no usable content",
                extra={"event": "gemini_empty_response", "model": self._settings.gemini_model},
            )
            raise GeminiResponseError("Gemini returned no usable content.")

        candidate_content: types.Content | None = None
        if getattr(response, "candidates", None) and len(response.candidates) > 0:
            candidate_content = getattr(response.candidates[0], "content", None)
        if candidate_content is None and function_calls:
            candidate_content = types.Content(
                role="model",
                parts=[
                    types.Part.from_function_call(name=fc.name, args=dict(fc.args or {}))
                    for fc in function_calls
                ],
            )
        elif candidate_content is None and text:
            candidate_content = types.Content(
                role="model",
                parts=[types.Part.from_text(text=text)],
            )

        return GeminiResult(
            text=text,
            function_calls=function_calls,
            candidate_content=candidate_content,
            usage_metadata=getattr(response, "usage_metadata", None),
        )
