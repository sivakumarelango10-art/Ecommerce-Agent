from __future__ import annotations

import logging
import re
import time
from typing import Any
from pydantic import SecretStr

from google.genai import errors, types

from backend.app.core.config import Settings
from backend.app.services.gemini_service import (
    GeminiProviderError,
    GeminiResponseError,
    GeminiResult,
    GeminiService,
    GeminiTimeoutError,
    _extract_cooldown_seconds,
    _is_credential_invalid_error,
    _is_daily_quota_exhaustion,
)
from backend.app.services.personal_gemini_runtime import RequestScopedGeminiService
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

logger = logging.getLogger(__name__)


class GeminiProviderAdapter(AIProviderAdapter):
    """Provider adapter wrapping Google Gemini runtime.

    Preserves 100% of existing Gemini behavior:
    - Personal-key priority and fallback to environment key pool.
    - Quota-group cooldowns and slot-level tracking.
    - 429 extraction and invalid-key handling.
    - In-flight telemetry recording without secret exposure.
    - Zero activation of database-stored PlatformAIKey credentials.
    """

    def __init__(
        self,
        service: GeminiService | RequestScopedGeminiService,
        *,
        settings: Settings | None = None,
        default_model: str = "gemini-3.8-flash",
    ) -> None:
        self._service = service
        self._settings = settings
        self._default_model = default_model

    @property
    def provider_code(self) -> str:
        return "google_gemini"

    @property
    def display_name(self) -> str:
        return "Google Gemini"

    @property
    def underlying_service(self) -> GeminiService | RequestScopedGeminiService:
        return self._service

    def get_capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            supports_text=True,
            supports_structured_output=True,
            supports_function_calling=True,
            supports_streaming=False,
            supports_vision=True,
            max_context_window=1048576,
            max_output_tokens=8192,
        )

    def supports_model(self, model: str) -> bool:
        clean = (model or "").strip().lower()
        return clean.startswith("gemini-")

    def _normalize_exception(
        self,
        exc: Exception,
        *,
        model: str | None = None,
    ) -> AIProviderError:
        """Map Gemini-specific exceptions into normalized AIProviderError hierarchy."""
        model_name = model or self._default_model

        if isinstance(exc, GeminiTimeoutError):
            return AIProviderTimeoutError(
                str(exc),
                provider_code=self.provider_code,
                model=model_name,
            )

        if isinstance(exc, GeminiResponseError):
            return AIProviderResponseError(
                str(exc),
                provider_code=self.provider_code,
                model=model_name,
            )

        if isinstance(exc, ValueError):
            return AIProviderBadRequestError(
                str(exc),
                provider_code=self.provider_code,
                model=model_name,
                status_code=400,
            )

        # Inspect Google GenAI APIError if present
        if isinstance(exc, errors.APIError):
            code = getattr(exc, "code", None)
            cooldown_seconds, is_daily = _extract_cooldown_seconds(exc)

            if code == 429:
                if is_daily:
                    return AIProviderQuotaExhaustedError(
                        str(exc),
                        provider_code=self.provider_code,
                        model=model_name,
                        status_code=429,
                        retry_after_seconds=cooldown_seconds,
                    )
                return AIProviderRateLimitError(
                    str(exc),
                    provider_code=self.provider_code,
                    model=model_name,
                    status_code=429,
                    retry_after_seconds=cooldown_seconds,
                )

            if _is_credential_invalid_error(exc) or code in (401, 403):
                return AIProviderAuthenticationError(
                    str(exc),
                    provider_code=self.provider_code,
                    model=model_name,
                    status_code=code or 401,
                )

            if code in (500, 502, 503, 504):
                return AIProviderTransientError(
                    str(exc),
                    provider_code=self.provider_code,
                    model=model_name,
                    status_code=code,
                )

            if code == 400:
                return AIProviderBadRequestError(
                    str(exc),
                    provider_code=self.provider_code,
                    model=model_name,
                    status_code=400,
                )

            return AIProviderError(
                str(exc),
                provider_code=self.provider_code,
                model=model_name,
                status_code=code,
            )

        # Inspect GeminiProviderError or generic RuntimeError
        msg = str(exc).lower()
        if "429" in msg or "quota" in msg or "rate limit" in msg or "cooldown" in msg:
            is_daily = _is_daily_quota_exhaustion(None, msg)
            if is_daily:
                return AIProviderQuotaExhaustedError(
                    str(exc),
                    provider_code=self.provider_code,
                    model=model_name,
                    status_code=429,
                )
            # Try to extract retry seconds from text
            match = re.search(r"retry\s+(?:after|in)\s+([0-9]+(?:\.[0-9]+)?)\s*s?", msg)
            delay = float(match.group(1)) if match else None
            return AIProviderRateLimitError(
                str(exc),
                provider_code=self.provider_code,
                model=model_name,
                status_code=429,
                retry_after_seconds=delay,
            )

        if "invalid" in msg and ("key" in msg or "credential" in msg or "token" in msg):
            return AIProviderAuthenticationError(
                str(exc),
                provider_code=self.provider_code,
                model=model_name,
                status_code=401,
            )

        if "timeout" in msg or "timed out" in msg:
            return AIProviderTimeoutError(
                str(exc),
                provider_code=self.provider_code,
                model=model_name,
            )

        if "unavailable" in msg or "503" in msg or "500" in msg or "transient" in msg:
            return AIProviderTransientError(
                str(exc),
                provider_code=self.provider_code,
                model=model_name,
                status_code=503,
            )

        return AIProviderError(
            str(exc),
            provider_code=self.provider_code,
            model=model_name,
            error_category="PROVIDER_ERROR",
        )

    def generate(self, request: GenerationRequest) -> GenerationResponse:
        """Execute a generation request synchronously using the underlying Gemini runtime."""
        model_name = request.model or self._default_model

        # 1. Resolve payload contents
        payload = request.contents if request.contents is not None else request.prompt
        if payload is None:
            raise AIProviderBadRequestError(
                "Either prompt or contents must be provided in GenerationRequest.",
                provider_code=self.provider_code,
                model=model_name,
                status_code=400,
            )

        user_key = SecretStr(request.user_key) if request.user_key else None

        start_time = time.perf_counter()
        try:
            result = self._service.generate(
                contents=payload,
                tools=request.tools,
                system_instruction=request.system_instruction,
                user_key=user_key,
            )
        except Exception as exc:
            raise self._normalize_exception(exc, model=model_name) from exc

        latency_ms = round((time.perf_counter() - start_time) * 1000, 2)

        # 2. Extract token usage without fabrication
        usage: TokenUsage | None = None
        if result.usage_metadata is not None:
            prompt_tokens = getattr(result.usage_metadata, "prompt_token_count", None)
            candidate_tokens = getattr(result.usage_metadata, "candidates_token_count", None)
            total_tokens = getattr(result.usage_metadata, "total_token_count", None)
            if (
                prompt_tokens is not None
                or candidate_tokens is not None
                or total_tokens is not None
            ):
                usage = TokenUsage(
                    input_tokens=prompt_tokens,
                    output_tokens=candidate_tokens,
                    total_tokens=total_tokens,
                )

        resp_metadata = dict(request.metadata)
        if isinstance(self._service, RequestScopedGeminiService) and (
            getattr(self._service, "_session_factory", None) is not None
            or getattr(self._service, "session_factory", None) is not None
        ):
            resp_metadata["telemetry_recorded"] = True

        return GenerationResponse(
            text=result.text,
            function_calls=result.function_calls,
            raw_content=result.candidate_content,
            usage=usage,
            model=model_name,
            provider=self.provider_code,
            latency_ms=latency_ms,
            metadata=resp_metadata,
        )

    def generate_text(self, prompt: str, model: str | None = None, **kwargs: Any) -> str:
        """Execute text generation synchronously, returning plain response text."""
        model_name = model or self._default_model
        clean_prompt = (prompt or "").strip()
        if not clean_prompt:
            raise AIProviderBadRequestError(
                "Prompt must not be empty.",
                provider_code=self.provider_code,
                model=model_name,
                status_code=400,
            )

        # If simple text answer is supported directly on the underlying service
        if hasattr(self._service, "answer") and not kwargs:
            try:
                answer = self._service.answer(clean_prompt)
                if not answer or not answer.strip():
                    raise AIProviderResponseError(
                        "Gemini returned an empty text response.",
                        provider_code=self.provider_code,
                        model=model_name,
                    )
                return answer
            except Exception as exc:
                raise self._normalize_exception(exc, model=model_name) from exc

        # Fallback through generate()
        req = GenerationRequest(
            prompt=clean_prompt,
            model=model_name,
            **kwargs,
        )
        res = self.generate(req)
        if not res.text or not res.text.strip():
            raise AIProviderResponseError(
                "Gemini returned an empty text response.",
                provider_code=self.provider_code,
                model=model_name,
            )
        return res.text

    def health_check(self) -> dict[str, Any]:
        """Return provider runtime health status snapshot."""
        snapshot: dict[str, Any] = {}
        if hasattr(self._service, "get_health_snapshot"):
            try:
                snapshot = self._service.get_health_snapshot()
            except Exception as exc:
                snapshot = {"error": str(exc), "status": "unhealthy"}

        return {
            "provider_code": self.provider_code,
            "display_name": self.display_name,
            "operational_status": "AVAILABLE",
            "runtime_adapter": "GeminiProviderAdapter",
            "details": snapshot,
        }
