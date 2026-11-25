"""StoreFlow AI Provider Runtime Architecture.

Provides a clean, provider-independent adapter foundation supporting:
- Abstract provider adapter contract (AIProviderAdapter)
- Normalized error hierarchy and retryability classification
- Google Gemini provider adapter (GeminiProviderAdapter) wrapping existing Gemini runtime
- Central runtime resolver (ProviderRuntimeResolver) with strict rejection rules
- Standardized provider telemetry contract without secret or prompt leakage
- Safe security defaults for outbound endpoints, redirects, and timeouts
"""

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
from backend.app.services.providers.gemini_adapter import GeminiProviderAdapter
from backend.app.services.providers.resolver import (
    DisabledModelError,
    DisabledProviderError,
    ProviderAdapterUnavailableError,
    ProviderRuntimeResolver,
    UnknownModelError,
    UnknownProviderError,
)
from backend.app.services.providers.security import (
    ALLOW_REDIRECTS,
    DEFAULT_OUTBOUND_TIMEOUT_SECONDS,
    DISALLOW_REDIRECTS,
    FOLLOW_REDIRECTS,
    MAX_SAFE_RESPONSE_BYTES,
    build_safe_provider_async_http_client,
    build_safe_provider_http_client,
    mask_sensitive_token,
    validate_provider_endpoint,
    verify_response_size,
)
from backend.app.services.providers.telemetry import (
    NormalizedTelemetryRecord,
    record_normalized_telemetry,
)

__all__ = [
    # Base contract & models
    "AIProviderAdapter",
    "ProviderCapabilities",
    "TokenUsage",
    "GenerationRequest",
    "GenerationResponse",
    # Error hierarchy
    "AIProviderError",
    "AIProviderTimeoutError",
    "AIProviderRateLimitError",
    "AIProviderQuotaExhaustedError",
    "AIProviderAuthenticationError",
    "AIProviderTransientError",
    "AIProviderBadRequestError",
    "AIProviderResponseError",
    # Gemini implementation
    "GeminiProviderAdapter",
    # Resolver & errors
    "ProviderRuntimeResolver",
    "UnknownProviderError",
    "DisabledProviderError",
    "UnknownModelError",
    "DisabledModelError",
    "ProviderAdapterUnavailableError",
    # Security helpers
    "validate_provider_endpoint",
    "mask_sensitive_token",
    "MAX_SAFE_RESPONSE_BYTES",
    "DEFAULT_OUTBOUND_TIMEOUT_SECONDS",
    "FOLLOW_REDIRECTS",
    "ALLOW_REDIRECTS",
    "DISALLOW_REDIRECTS",
    "build_safe_provider_http_client",
    "build_safe_provider_async_http_client",
    "verify_response_size",
    # Telemetry
    "NormalizedTelemetryRecord",
    "record_normalized_telemetry",
]
