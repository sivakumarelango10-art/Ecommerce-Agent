from __future__ import annotations

import logging
import time
from typing import Any, Sequence, TypeVar
from uuid import UUID, uuid4

from google.genai import types
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.config import Settings
from backend.app.db.models import AIModel, AIProvider, User
from backend.app.db.seed_ai_catalogue import seed_ai_provider_catalogue
from backend.app.services.gemini_service import GeminiResult
from backend.app.services.providers.base import (
    AIProviderAdapter,
    AIProviderBadRequestError,
    AIProviderError,
    AIProviderResponseError,
    AIProviderSchemaError,
    AIProviderTimeoutError,
    AIProviderValidationError,
    GenerationRequest,
    GenerationResponse,
    ProviderCapabilities,
    TokenUsage,
    parse_and_validate_structured_output,
    validate_structured_schema,
)
from backend.app.services.providers.resolver import (
    DisabledModelError,
    DisabledProviderError,
    ProviderAdapterUnavailableError,
    ProviderRuntimeResolver,
    ProviderTrafficNotPermittedError,
    UnknownModelError,
    UnknownProviderError,
)
from backend.app.services.providers.telemetry import (
    NormalizedTelemetryRecord,
    record_normalized_telemetry,
)

logger = logging.getLogger(__name__)


def _ensure_catalogue_seeded(session: Session) -> None:
    """Ensure AI catalogue is seeded for test or fresh environments with raw schemas."""
    try:
        has_provider = session.scalar(select(AIProvider.id).limit(1))
        if has_provider is None:
            seed_ai_provider_catalogue(session)
            session.commit()
    except Exception:
        try:
            session.rollback()
        except Exception:
            pass


T = TypeVar("T", bound=BaseModel)


class AIProviderOrchestrator:
    """Unified AI provider orchestration service for StoreFlow applications.

    Coordinates model invocation across registered provider adapters while enforcing:
    1. Central metadata validation via ProviderRuntimeResolver (rejects unknown, disabled, or unpermitted providers/models).
    2. Strict customer-traffic permission enforcement (AVAILABLE status required; PLANNED providers like Groq fail closed with 403).
    3. Strict refusal of silent provider fallback: if an invoked provider fails or times out, the error is normalized and propagated immediately.
    4. Zero prompt or secret leakage into telemetry, audit trails, or exceptions.
    5. Customer personal-key precedence with seamless platform-key fallback via underlying adapter.
    """

    def __init__(
        self,
        *,
        resolver: ProviderRuntimeResolver,
        session: Session,
        default_provider: str = "google_gemini",
        default_model: str | None = None,
        user: User | None = None,
        correlation_id: str | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.resolver = resolver
        self.session = session
        self.default_provider = default_provider
        self.default_model = default_model
        self.user = user
        self.correlation_id = correlation_id
        self.settings = settings

    def generate(
        self,
        request: GenerationRequest,
        *,
        provider: str | None = None,
    ) -> GenerationResponse:
        """Execute a provider-independent generation request with strict orchestration rules."""
        # 1. Resolve target provider code (verified through database registry)
        target_provider = provider or self.default_provider

        # Ensure catalogue is seeded if running in unseeded test context
        _ensure_catalogue_seeded(self.session)

        # 2. Resolve adapter and model through central resolver (enforces AVAILABLE status and enablement)
        # If request explicitly specifies a model, resolver strictly validates it.
        # If request.model is None, resolver validates provider and retrieves default model.
        explicit_model = request.model
        adapter, model_record = self.resolver.resolve(
            self.session,
            target_provider,
            explicit_model,
        )

        if explicit_model and model_record:
            resolved_model_name = model_record.model_identifier
        elif self.default_model:
            resolved_model_name = self.default_model
        elif model_record:
            resolved_model_name = model_record.model_identifier
        else:
            resolved_model_name = getattr(adapter, "_default_model", "default")

        # 3. Validate structured schema and verify provider capability if structured output requested
        if request.response_schema is not None:
            validate_structured_schema(request.response_schema)
            capabilities = adapter.get_capabilities()
            if not capabilities.supports_structured_output:
                raise AIProviderBadRequestError(
                    f"Provider '{target_provider}' does not support structured output generation.",
                    provider_code=target_provider,
                    model=resolved_model_name,
                    status_code=400,
                )

        # 4. Attach correlation ID and model if not present
        effective_correlation_id = (
            request.correlation_id
            or self.correlation_id
            or str(uuid4())
        )
        request.correlation_id = effective_correlation_id
        if not request.model and resolved_model_name:
            request.model = resolved_model_name

        # 4. Check if underlying adapter/service already manages telemetry
        underlying = getattr(adapter, "underlying_service", None)
        adapter_handles_telemetry = (
            underlying is not None
            and (
                getattr(underlying, "_session_factory", None) is not None
                or getattr(underlying, "session_factory", None) is not None
            )
        )

        # 5. Execute generation with latency and telemetry tracking
        start_time = time.perf_counter()
        try:
            response = adapter.generate(request)
        except AIProviderError as exc:
            latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
            if not adapter_handles_telemetry:
                self._record_telemetry(
                    provider=target_provider,
                    model_name=resolved_model_name,
                    status="FAILED",
                    correlation_id=effective_correlation_id,
                    error_category=exc.error_category,
                    latency_ms=latency_ms,
                )
            # STRICT REFUSAL OF SILENT PROVIDER FALLBACK
            logger.warning(
                "Provider generation failed for %s/%s: %s (no fallback permitted)",
                target_provider,
                resolved_model_name,
                exc.message,
            )
            raise
        except Exception as exc:
            latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
            if not adapter_handles_telemetry:
                self._record_telemetry(
                    provider=target_provider,
                    model_name=resolved_model_name,
                    status="FAILED",
                    correlation_id=effective_correlation_id,
                    error_category="UNEXPECTED_ERROR",
                    latency_ms=latency_ms,
                )
            normalized = adapter._normalize_exception(exc, model=resolved_model_name)
            logger.warning(
                "Provider generation unexpected error for %s/%s: %s (no fallback permitted)",
                target_provider,
                resolved_model_name,
                type(exc).__name__,
            )
            raise normalized from exc

        # 6. Record successful normalized telemetry if not already handled by adapter
        if (
            not adapter_handles_telemetry
            and not response.metadata.get("telemetry_recorded")
            and not request.metadata.get("skip_orchestrator_telemetry")
        ):
            latency_ms = response.latency_ms or round((time.perf_counter() - start_time) * 1000, 2)
            key_source = response.metadata.get(
                "key_source",
                "PERSONAL" if request.user_key else "PLATFORM_KEY",
            )
            key_slot = response.metadata.get("key_slot")

            self._record_telemetry(
                provider=response.provider or target_provider,
                model_name=response.model or resolved_model_name,
                status="SUCCESS",
                correlation_id=effective_correlation_id,
                key_source=key_source,
                key_slot=key_slot,
                input_tokens=response.usage.input_tokens if response.usage else None,
                output_tokens=response.usage.output_tokens if response.usage else None,
                total_tokens=response.usage.total_tokens if response.usage else None,
                latency_ms=latency_ms,
            )

        # 7. Ensure parsed model is populated if response_schema was requested
        if request.response_schema is not None and response.parsed is None:
            response.parsed = parse_and_validate_structured_output(
                response.text,
                request.response_schema,
                strict=request.strict_schema,
                provider_code=target_provider,
                model=resolved_model_name,
            )

        return response

    def generate_structured(
        self,
        prompt: str,
        response_schema: type[T],
        *,
        provider: str | None = None,
        model: str | None = None,
        system_instruction: str | None = None,
        strict_schema: bool = True,
        timeout_seconds: float = 30.0,
        **kwargs: Any,
    ) -> T:
        """Execute structured output generation returning validated Pydantic model instance."""
        validated_cls = validate_structured_schema(response_schema)
        clean_prompt = (prompt or "").strip()
        if not clean_prompt:
            raise AIProviderBadRequestError(
                "Prompt must not be empty.",
                provider_code=provider or self.default_provider,
                model=model or self.default_model,
                status_code=400,
            )

        req = GenerationRequest(
            prompt=clean_prompt,
            model=model,
            system_instruction=system_instruction,
            response_schema=validated_cls,
            strict_schema=strict_schema,
            timeout_seconds=timeout_seconds,
            correlation_id=self.correlation_id,
            metadata=dict(kwargs),
        )
        res = self.generate(req, provider=provider)
        if res.parsed is not None and isinstance(res.parsed, validated_cls):
            return res.parsed
        return parse_and_validate_structured_output(
            res.text,
            validated_cls,
            strict=strict_schema,
            provider_code=res.provider or provider or self.default_provider,
            model=res.model or model,
        )

    def generate_text(
        self,
        prompt: str,
        *,
        provider: str | None = None,
        model: str | None = None,
        system_instruction: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout_seconds: float = 30.0,
        **kwargs: Any,
    ) -> str:
        """Execute text generation synchronously, returning normalized string output."""
        clean_prompt = (prompt or "").strip()
        if not clean_prompt:
            raise AIProviderBadRequestError(
                "Prompt must not be empty.",
                provider_code=provider or self.default_provider,
                model=model or self.default_model,
                status_code=400,
            )

        req = GenerationRequest(
            prompt=clean_prompt,
            model=model,
            system_instruction=system_instruction,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
            correlation_id=self.correlation_id,
            metadata=dict(kwargs),
        )
        res = self.generate(req, provider=provider)
        if not res.text or not res.text.strip():
            raise AIProviderResponseError(
                f"Provider '{res.provider or provider or self.default_provider}' returned an empty text response.",
                provider_code=res.provider or provider or self.default_provider,
                model=res.model or model,
            )
        return res.text

    def answer(self, question: str) -> str:
        """Protocol compatibility method for question-answering call sites."""
        return self.generate_text(question)

    def _record_telemetry(
        self,
        *,
        provider: str,
        model_name: str,
        status: str,
        correlation_id: str,
        key_source: str = "PLATFORM_KEY",
        key_slot: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        total_tokens: int | None = None,
        error_category: str | None = None,
        latency_ms: float | None = None,
    ) -> None:
        telemetry_record = NormalizedTelemetryRecord(
            correlation_id=correlation_id,
            provider=provider,
            model_name=model_name,
            key_source=key_source,
            key_slot=key_slot,
            status=status,
            user_id=self.user.id if self.user else None,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            error_category=error_category,
            latency_ms=latency_ms,
        )
        record_normalized_telemetry(self.session, telemetry_record)
