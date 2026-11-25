"""StoreFlow AI Provider Runtime Architecture.

Provides a clean, provider-independent adapter foundation supporting:
- Abstract provider adapter contract (AIProviderAdapter)
- Normalized error hierarchy and retryability classification
- Google Gemini provider adapter (GeminiProviderAdapter) wrapping existing Gemini runtime
- Central runtime resolver (ProviderRuntimeResolver) with strict rejection rules
- Standardized provider telemetry contract without secret or prompt leakage
- Safe security defaults for outbound endpoints, redirects, timeouts, and DNS-rebinding prevention
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
from backend.app.services.providers.groq_adapter import (
    DEFAULT_GROQ_BASE_URL,
    DEFAULT_GROQ_MODEL,
    GROQ_MODEL_REGISTRY,
    GROQ_PROVIDER_CODE,
    SUPPORTED_GROQ_CHAT_MODELS,
    SUPPORTED_GROQ_MODELS,
    GroqKeySlot,
    GroqModelSpec,
    GroqModelStatus,
    GroqModelTask,
    GroqProviderAdapter,
)
from backend.app.services.providers.orchestrator import AIProviderOrchestrator
from backend.app.services.providers.resolver import (
    DisabledModelError,
    DisabledProviderError,
    ProviderAdapterUnavailableError,
    ProviderRuntimeResolver,
    ProviderTrafficNotPermittedError,
    UnknownModelError,
    UnknownProviderError,
)
from backend.app.services.providers.security import (
    DEFAULT_OUTBOUND_TIMEOUT_SECONDS,
    FOLLOW_REDIRECTS,
    MAX_SAFE_RESPONSE_BYTES,
    TRUSTED_PROVIDER_HOSTS,
    AIProviderSSRFError,
    HardenedAsyncHTTPTransport,
    HardenedHTTPTransport,
    aread_safe_response_body,
    build_safe_provider_async_http_client,
    build_safe_provider_http_client,
    is_trusted_provider_host,
    mask_sensitive_token,
    read_safe_response_body,
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
    "AIProviderSSRFError",
    "AIProviderResponseError",
    # Gemini implementation
    "GeminiProviderAdapter",
    # Orchestrator
    "AIProviderOrchestrator",
    # Groq implementation
    "GroqProviderAdapter",
    "GroqKeySlot",
    "GROQ_PROVIDER_CODE",
    "DEFAULT_GROQ_MODEL",
    "DEFAULT_GROQ_BASE_URL",
    "SUPPORTED_GROQ_CHAT_MODELS",
    "SUPPORTED_GROQ_MODELS",
    "GROQ_MODEL_REGISTRY",
    "GroqModelSpec",
    "GroqModelTask",
    "GroqModelStatus",
    # Resolver & errors
    "ProviderRuntimeResolver",
    "UnknownProviderError",
    "DisabledProviderError",
    "UnknownModelError",
    "DisabledModelError",
    "ProviderAdapterUnavailableError",
    "ProviderTrafficNotPermittedError",
    # Security helpers & transports
    "validate_provider_endpoint",
    "mask_sensitive_token",
    "MAX_SAFE_RESPONSE_BYTES",
    "DEFAULT_OUTBOUND_TIMEOUT_SECONDS",
    "FOLLOW_REDIRECTS",
    "HardenedHTTPTransport",
    "HardenedAsyncHTTPTransport",
    "build_safe_provider_http_client",
    "build_safe_provider_async_http_client",
    "read_safe_response_body",
    "aread_safe_response_body",
    "verify_response_size",
    "TRUSTED_PROVIDER_HOSTS",
    "is_trusted_provider_host",
    # Telemetry
    "NormalizedTelemetryRecord",
    "record_normalized_telemetry",
]
