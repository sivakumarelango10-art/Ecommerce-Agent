from __future__ import annotations

from datetime import datetime
import ipaddress
import logging
import re
from typing import Any
import urllib.parse
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.core.datetime_utils import utc_now
from backend.app.db.models import (
    AIModel,
    AIProvider,
    ModelAccessClass,
    PlatformAIKey,
    PlatformKeyStatus,
    ProviderConnectionStatus,
)
from backend.app.schemas.ai_provider import (
    AIModelCreate,
    AIModelUpdate,
    AIProviderCreate,
    AIProviderUpdate,
    PlatformAIKeyCreate,
    PlatformAIKeyUpdate,
)
from backend.app.security.key_encryption import (
    KeyEncryptionError,
    encrypt_platform_key,
    mask_platform_key,
)
from backend.app.services.admin_audit_service import record_admin_audit_event

logger = logging.getLogger(__name__)

# Disallowed internal, loopback, and cloud metadata destinations
RESTRICTED_ENTERPRISE_MODEL_IDENTIFIERS: frozenset[str] = frozenset({
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
})

_DISALLOWED_HOSTS = {
    "localhost",
    "127.0.0.1",
    "::1",
    "0.0.0.0",
    "169.254.169.254",
    "metadata.google.internal",
    "100.100.100.200",
    "instance-data",
}

# Strict official hostname allowlist per provider code
PROVIDER_APPROVED_HOSTS: dict[str, set[str]] = {
    "google_gemini": {"generativelanguage.googleapis.com"},
    "groq": {"api.groq.com"},
    "openrouter": {"openrouter.ai"},
    "sarvam": {"api.sarvam.ai"},
    "cloudflare_workers_ai": {"api.cloudflare.com"},
    "cerebras": {"api.cerebras.ai"},
    "huggingface": {"api-inference.huggingface.co", "router.huggingface.co"},
    "mistral": {"api.mistral.ai"},
    "deepseek": {"api.deepseek.com"},
    "nvidia_nim": {"integrate.api.nvidia.com"},
    "together": {"api.together.xyz"},
    "fireworks": {"api.fireworks.ai"},
    "sambanova": {"api.sambanova.ai"},
    "cohere": {"api.cohere.com"},
    "xai": {"api.x.ai"},
    "openai": {"api.openai.com"},
    "perplexity": {"api.perplexity.ai"},
    "github_models": {"models.inference.ai.azure.com"},
    "azure_ai_foundry": {"models.inference.ai.azure.com"},
}

AWS_BEDROCK_HOST_PATTERN = re.compile(
    r"^bedrock-runtime\.[a-z0-9\-]+\.amazonaws\.com$"
)
AZURE_AI_HOST_PATTERN = re.compile(
    r"^(?:models\.inference\.ai\.azure\.com|[a-z0-9\-]+\.services\.ai\.azure\.com|[a-z0-9\-]+\.openai\.azure\.com)$"
)


class AIProviderServiceError(Exception):
    """Base exception for AI provider registry service operations."""


class AIProviderNotFoundError(AIProviderServiceError):
    """Raised when an AI provider is not found."""


class AIModelNotFoundError(AIProviderServiceError):
    """Raised when an AI model is not found."""


class PlatformKeyNotFoundError(AIProviderServiceError):
    """Raised when a platform key is not found."""


class SSRFSecurityError(AIProviderServiceError):
    """Raised when a base URL fails SSRF security validation."""


def _is_host_approved_for_provider(
    hostname: str,
    provider_code: str | None,
    allow_custom_endpoint: bool = False,
    custom_allowed_hosts: set[str] | None = None,
) -> bool:
    """Evaluate whether hostname is strictly approved for the specified provider code."""
    lower_host = hostname.lower()

    # 1. Custom endpoint path: requires explicit super_admin configuration and host allowlist
    if allow_custom_endpoint:
        if custom_allowed_hosts and lower_host in {h.lower() for h in custom_allowed_hosts}:
            return True
        raise SSRFSecurityError(
            f"Custom endpoint host '{hostname}' is not in the approved custom host allowlist."
        )

    # 2. Provider-specific allowlist check
    if provider_code:
        code = provider_code.strip().lower()
        if code == "aws_bedrock":
            return bool(AWS_BEDROCK_HOST_PATTERN.match(lower_host))
        if code in ("azure_ai_foundry", "github_models"):
            if lower_host in PROVIDER_APPROVED_HOSTS.get(code, set()):
                return True
            return bool(AZURE_AI_HOST_PATTERN.match(lower_host))
        if code in PROVIDER_APPROVED_HOSTS:
            return lower_host in PROVIDER_APPROVED_HOSTS[code]
        raise SSRFSecurityError(
            f"Provider code '{provider_code}' is not a recognized provider. Custom endpoints require explicit super_admin configuration."
        )

    # 3. Generic check across all approved providers when provider_code is not specified
    for approved_set in PROVIDER_APPROVED_HOSTS.values():
        if lower_host in approved_set:
            return True
    if AWS_BEDROCK_HOST_PATTERN.match(lower_host):
        return True
    if AZURE_AI_HOST_PATTERN.match(lower_host):
        return True

    return False


def validate_provider_base_url(
    url: str | None,
    provider_code: str | None = None,
    allow_custom_endpoint: bool = False,
    custom_allowed_hosts: set[str] | None = None,
) -> str | None:
    """Strictly validate AI provider base URLs to prevent SSRF vulnerabilities.

    Enforces:
    - HTTPS scheme only (no HTTP or alternate schemes).
    - Provider-specific exact hostname or validated subdomain pattern allowlist.
    - Rejection of lookalike domains, credential embedding, and unexpected ports.
    - Rejection of private IP ranges, loopback addresses, link-local, and cloud metadata endpoints.

    HTTP ADAPTER ARCHITECTURE & SSRF DEFENSE IN DEPTH:
    When concrete HTTP client adapters are implemented for provider dispatch (e.g. via httpx, aiohttp, or requests):
    1. REDIRECT HANDLING:
       - HTTP clients MUST either disable redirects (`follow_redirects=False`) or install a strict redirect hook
         that re-validates the target `Location` URL using `validate_provider_base_url` before following.
       - Redirects must never downgrade from HTTPS to HTTP or navigate to arbitrary domains/internal endpoints.
    2. DNS RESOLUTION & REBINDING (TOCTOU) PREVENTION:
       - Modern SSRF attackers use DNS Rebinding: a domain initially resolves to a legitimate public IP, but during
         request execution resolves to a private IP (e.g. 127.0.0.1 or 169.254.169.254).
       - The HTTP adapter MUST perform DNS resolution at connection time, inspect every resolved socket IP address
         against private/loopback/metadata IP filters, and bind directly to the verified socket IP (DNS pinning).
    3. NETWORK RESTRICTIONS:
       - Production workers dispatching AI requests should run in network environments where egress to AWS/GCP/Azure
         instance metadata (169.254.169.254) is firewalled at the OS/VPC layer.
    """
    if not url:
        return None
    clean = url.strip()
    if not clean:
        return None

    try:
        parsed = urllib.parse.urlparse(clean)
    except Exception as exc:
        raise SSRFSecurityError(f"Malformed base URL: {exc}") from exc

    # 1. Scheme check: HTTPS only
    if parsed.scheme.lower() != "https":
        raise SSRFSecurityError("Provider base URL must strictly use HTTPS scheme.")

    # 2. Disallow embedded credentials (e.g. https://user:pass@domain.com)
    if parsed.username or parsed.password:
        raise SSRFSecurityError("Provider base URL must not contain embedded user credentials.")

    # 3. Disallow unexpected non-standard ports (port 443 or default only)
    if parsed.port is not None and parsed.port != 443:
        raise SSRFSecurityError(
            f"Provider base URL cannot specify unexpected port '{parsed.port}': only standard HTTPS (port 443) is permitted."
        )

    # 4. Hostname check
    hostname = parsed.hostname
    if not hostname:
        raise SSRFSecurityError("Provider base URL is missing a valid hostname.")

    lower_host = hostname.lower()

    # 5. Reject explicit metadata, loopback, and internal hosts
    if lower_host in _DISALLOWED_HOSTS:
        raise SSRFSecurityError(f"Provider base URL cannot target internal or metadata host '{hostname}'.")

    if (
        lower_host.endswith(".localhost")
        or lower_host.endswith(".local")
        or lower_host.endswith(".internal")
    ):
        raise SSRFSecurityError(f"Provider base URL cannot target internal domain '{hostname}'.")

    # 6. Evaluate raw IP addresses: all IP targets are forbidden (providers must use approved hostnames)
    try:
        ip = ipaddress.ip_address(lower_host)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            raise SSRFSecurityError(
                f"Provider base URL cannot target private, loopback, or reserved IP '{hostname}'."
            )
        raise SSRFSecurityError(
            f"Provider base URL cannot target raw IP addresses ('{hostname}'). Only approved domain names are permitted."
        )
    except ValueError:
        # Not a raw IP address
        pass

    # 7. Check against provider-specific approved hostname allowlist (prevents lookalike domains)
    if not _is_host_approved_for_provider(
        hostname=lower_host,
        provider_code=provider_code,
        allow_custom_endpoint=allow_custom_endpoint,
        custom_allowed_hosts=custom_allowed_hosts,
    ):
        if provider_code:
            raise SSRFSecurityError(
                f"Host '{hostname}' is not an authorized endpoint for provider '{provider_code}'."
            )
        raise SSRFSecurityError(
            f"Host '{hostname}' is not in the approved provider hostname allowlist."
        )

    return clean


# ---------------------------------------------------------------------------
# Provider Service Operations
# ---------------------------------------------------------------------------

def list_providers(
    session: Session,
    status: str | None = None,
    is_enabled: bool | None = None,
    adapter_type: str | None = None,
) -> list[AIProvider]:
    query = select(AIProvider).order_by(AIProvider.priority.asc(), AIProvider.display_name.asc())
    if status is not None:
        query = query.where(AIProvider.connection_status == status.upper())
    if is_enabled is not None:
        query = query.where(AIProvider.is_enabled == is_enabled)
    if adapter_type is not None:
        query = query.where(AIProvider.adapter_type == adapter_type.lower())
    return list(session.scalars(query).all())


def get_provider(session: Session, provider_id: UUID) -> AIProvider | None:
    return session.get(AIProvider, provider_id)


def get_provider_by_code(session: Session, code: str) -> AIProvider | None:
    clean_code = code.strip().lower()
    return session.scalar(select(AIProvider).where(func.lower(AIProvider.code) == clean_code))


def create_provider(
    session: Session,
    payload: AIProviderCreate,
    actor_user_id: UUID | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> AIProvider:
    clean_code = payload.code.strip().lower()
    existing = get_provider_by_code(session, clean_code)
    if existing:
        raise AIProviderServiceError(f"Provider with code '{clean_code}' already exists.")

    validated_base_url = validate_provider_base_url(payload.base_url, provider_code=clean_code)

    provider = AIProvider(
        code=clean_code,
        display_name=payload.display_name.strip(),
        adapter_type=payload.adapter_type.strip().lower(),
        base_url=validated_base_url,
        documentation_url=payload.documentation_url.strip() if payload.documentation_url else None,
        is_enabled=payload.is_enabled,
        priority=payload.priority,
        connection_status=payload.connection_status.upper(),
    )
    session.add(provider)
    session.flush()

    record_admin_audit_event(
        session=session,
        actor_user_id=actor_user_id,
        action="ai_provider.create",
        target_type="AIProvider",
        target_id=str(provider.id),
        metadata={
            "code": provider.code,
            "display_name": provider.display_name,
            "adapter_type": provider.adapter_type,
            "connection_status": provider.connection_status,
            "is_enabled": provider.is_enabled,
        },
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return provider


def update_provider(
    session: Session,
    provider_id: UUID,
    payload: AIProviderUpdate,
    actor_user_id: UUID | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> AIProvider:
    provider = get_provider(session, provider_id)
    if not provider:
        raise AIProviderNotFoundError(f"Provider with ID '{provider_id}' not found.")

    if payload.base_url is not None:
        provider.base_url = validate_provider_base_url(payload.base_url, provider_code=provider.code)
    if payload.display_name is not None:
        provider.display_name = payload.display_name.strip()
    if payload.adapter_type is not None:
        provider.adapter_type = payload.adapter_type.strip().lower()
    if payload.documentation_url is not None:
        provider.documentation_url = payload.documentation_url.strip() or None
    if payload.is_enabled is not None:
        provider.is_enabled = payload.is_enabled
    if payload.priority is not None:
        provider.priority = payload.priority
    if payload.connection_status is not None:
        provider.connection_status = payload.connection_status.upper()

    session.flush()

    record_admin_audit_event(
        session=session,
        actor_user_id=actor_user_id,
        action="ai_provider.update",
        target_type="AIProvider",
        target_id=str(provider.id),
        metadata={
            "code": provider.code,
            "connection_status": provider.connection_status,
            "is_enabled": provider.is_enabled,
            "priority": provider.priority,
        },
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return provider


# ---------------------------------------------------------------------------
# Model Service Operations
# ---------------------------------------------------------------------------

def list_models(
    session: Session,
    provider_id: UUID | None = None,
    access_class: str | None = None,
    is_enabled: bool | None = None,
) -> list[AIModel]:
    query = select(AIModel).order_by(AIModel.display_name.asc())
    if provider_id is not None:
        query = query.where(AIModel.provider_id == provider_id)
    if access_class is not None:
        query = query.where(AIModel.access_class == access_class.upper())
    if is_enabled is not None:
        query = query.where(AIModel.is_enabled == is_enabled)
    return list(session.scalars(query).all())


def get_model(session: Session, model_id: UUID) -> AIModel | None:
    return session.get(AIModel, model_id)


def create_model(
    session: Session,
    payload: AIModelCreate,
    actor_user_id: UUID | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> AIModel:
    provider = get_provider(session, payload.provider_id)
    if not provider:
        raise AIProviderNotFoundError(f"Provider with ID '{payload.provider_id}' not found.")

    clean_identifier = payload.model_identifier.strip()
    if clean_identifier.lower() in RESTRICTED_ENTERPRISE_MODEL_IDENTIFIERS:
        if payload.is_enabled:
            raise AIProviderServiceError(
                f"Model '{clean_identifier}' is enterprise-restricted on Groq and cannot be created as enabled "
                "because StoreFlow has no verified contractual committed-spend enterprise entitlement."
            )
        if payload.is_default:
            raise AIProviderServiceError(
                f"Model '{clean_identifier}' is enterprise-restricted on Groq and cannot be created as default."
            )
        if payload.access_class.strip().upper() != "UNKNOWN":
            raise AIProviderServiceError(
                f"Model '{clean_identifier}' is enterprise-restricted on Groq; access class cannot be "
                f"'{payload.access_class}'. Access class must remain 'UNKNOWN' without a verified enterprise contract."
            )
        if not payload.description or "[ENTERPRISE_RESTRICTED]" not in payload.description:
            raise AIProviderServiceError(
                f"Model '{clean_identifier}' is enterprise-restricted on Groq; description must contain "
                "'[ENTERPRISE_RESTRICTED]' designation."
            )

    existing = session.scalar(
        select(AIModel).where(
            AIModel.provider_id == payload.provider_id,
            AIModel.model_identifier == clean_identifier,
        )
    )
    if existing:
        raise AIProviderServiceError(
            f"Model identifier '{clean_identifier}' already exists for provider '{provider.code}'."
        )

    # If new model is default, clear previous default for this provider
    if payload.is_default:
        session.query(AIModel).filter(
            AIModel.provider_id == payload.provider_id,
            AIModel.is_default.is_(True),
        ).update({"is_default": False})

    model = AIModel(
        provider_id=payload.provider_id,
        model_identifier=clean_identifier,
        display_name=payload.display_name.strip(),
        description=payload.description.strip() if payload.description else None,
        supports_text=payload.supports_text,
        supports_vision=payload.supports_vision,
        supports_tools=payload.supports_tools,
        supports_streaming=payload.supports_streaming,
        supports_json_mode=payload.supports_json_mode,
        supports_embeddings=payload.supports_embeddings,
        supports_speech_input=payload.supports_speech_input,
        supports_speech_output=payload.supports_speech_output,
        context_window=payload.context_window,
        max_output_tokens=payload.max_output_tokens,
        pricing_input_per_million=payload.pricing_input_per_million,
        pricing_output_per_million=payload.pricing_output_per_million,
        pricing_currency=payload.pricing_currency.upper(),
        access_class=payload.access_class.upper(),
        free_tier_limit_metadata=payload.free_tier_limit_metadata,
        terms_url=payload.terms_url.strip() if payload.terms_url else None,
        pricing_url=payload.pricing_url.strip() if payload.pricing_url else None,
        is_enabled=payload.is_enabled,
        is_default=payload.is_default,
    )
    session.add(model)
    session.flush()

    record_admin_audit_event(
        session=session,
        actor_user_id=actor_user_id,
        action="ai_model.create",
        target_type="AIModel",
        target_id=str(model.id),
        metadata={
            "provider_code": provider.code,
            "model_identifier": model.model_identifier,
            "access_class": model.access_class,
            "is_default": model.is_default,
        },
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return model


def update_model(
    session: Session,
    model_id: UUID,
    payload: AIModelUpdate,
    actor_user_id: UUID | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> AIModel:
    model = get_model(session, model_id)
    if not model:
        raise AIModelNotFoundError(f"Model with ID '{model_id}' not found.")

    clean_id = (model.model_identifier or "").strip().lower()
    if clean_id in RESTRICTED_ENTERPRISE_MODEL_IDENTIFIERS:
        if payload.is_enabled is True:
            raise AIProviderServiceError(
                f"Model '{model.model_identifier}' is enterprise-restricted on Groq and cannot be enabled "
                "because StoreFlow has no verified contractual committed-spend enterprise entitlement."
            )
        if payload.is_default is True:
            raise AIProviderServiceError(
                f"Model '{model.model_identifier}' is enterprise-restricted on Groq and cannot be set as default."
            )
        if payload.access_class is not None and payload.access_class.strip().upper() != "UNKNOWN":
            raise AIProviderServiceError(
                f"Model '{model.model_identifier}' is enterprise-restricted on Groq; access class cannot be changed "
                f"to '{payload.access_class}'. Access class must remain 'UNKNOWN' without a verified enterprise contract."
            )
        if payload.description is not None and "[ENTERPRISE_RESTRICTED]" not in payload.description:
            raise AIProviderServiceError(
                f"Model '{model.model_identifier}' is enterprise-restricted on Groq; description must retain "
                "the '[ENTERPRISE_RESTRICTED]' designation."
            )

    if payload.is_default is True and not model.is_default:
        session.query(AIModel).filter(
            AIModel.provider_id == model.provider_id,
            AIModel.is_default.is_(True),
        ).update({"is_default": False})

    update_dict = payload.model_dump(exclude_unset=True)
    for k, v in update_dict.items():
        if k == "access_class" and v:
            setattr(model, k, v.upper())
        elif k == "pricing_currency" and v:
            setattr(model, k, v.upper())
        else:
            setattr(model, k, v)

    session.flush()

    record_admin_audit_event(
        session=session,
        actor_user_id=actor_user_id,
        action="ai_model.update",
        target_type="AIModel",
        target_id=str(model.id),
        metadata={
            "model_identifier": model.model_identifier,
            "access_class": model.access_class,
            "is_enabled": model.is_enabled,
            "is_default": model.is_default,
        },
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return model


# ---------------------------------------------------------------------------
# Platform AI Key Service Operations
# ---------------------------------------------------------------------------

def list_platform_keys(
    session: Session,
    provider_id: UUID | None = None,
    quota_group: str | None = None,
    status: str | None = None,
) -> list[PlatformAIKey]:
    query = select(PlatformAIKey).order_by(PlatformAIKey.created_at.desc())
    if provider_id is not None:
        query = query.where(PlatformAIKey.provider_id == provider_id)
    if quota_group is not None:
        query = query.where(PlatformAIKey.quota_group == quota_group.strip())
    if status is not None:
        query = query.where(PlatformAIKey.status == status.upper())
    return list(session.scalars(query).all())


def get_platform_key(session: Session, key_id: UUID) -> PlatformAIKey | None:
    return session.get(PlatformAIKey, key_id)


def create_platform_key(
    session: Session,
    payload: PlatformAIKeyCreate,
    encryption_secret: SecretStr,
    actor_user_id: UUID | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> PlatformAIKey:
    provider = get_provider(session, payload.provider_id)
    if not provider:
        raise AIProviderNotFoundError(f"Provider with ID '{payload.provider_id}' not found.")

    clean_quota_group = payload.quota_group.strip()
    if not clean_quota_group:
        raise AIProviderServiceError("Quota group is mandatory and cannot be empty.")

    if not encryption_secret or not isinstance(encryption_secret, SecretStr) or not encryption_secret.get_secret_value().strip():
        raise AIProviderServiceError("Platform key encryption secret is not configured or is empty.")

    raw_key = payload.api_key.get_secret_value().strip()
    if not raw_key:
        raise AIProviderServiceError("Cannot configure an empty API key.")

    encrypted_key = encrypt_platform_key(raw_key, secret=encryption_secret)
    key_hint = mask_platform_key(raw_key)

    key_record = PlatformAIKey(
        provider_id=payload.provider_id,
        encrypted_api_key=encrypted_key,
        key_hint=key_hint,
        quota_group=clean_quota_group,
        key_label=payload.key_label.strip(),
        is_enabled=payload.is_enabled,
        status=PlatformKeyStatus.UNVERIFIED.value,
    )
    session.add(key_record)
    session.flush()

    record_admin_audit_event(
        session=session,
        actor_user_id=actor_user_id,
        action="platform_key.create",
        target_type="PlatformAIKey",
        target_id=str(key_record.id),
        metadata={
            "provider_code": provider.code,
            "quota_group": key_record.quota_group,
            "key_hint": key_record.key_hint,
            "key_label": key_record.key_label,
            "status": key_record.status,
        },
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return key_record


def update_platform_key(
    session: Session,
    key_id: UUID,
    payload: PlatformAIKeyUpdate,
    actor_user_id: UUID | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> PlatformAIKey:
    key_record = get_platform_key(session, key_id)
    if not key_record:
        raise PlatformKeyNotFoundError(f"Platform key with ID '{key_id}' not found.")

    if payload.quota_group is not None:
        clean_qg = payload.quota_group.strip()
        if not clean_qg:
            raise AIProviderServiceError("Quota group cannot be set to empty.")
        key_record.quota_group = clean_qg
    if payload.key_label is not None:
        key_record.key_label = payload.key_label.strip()
    if payload.is_enabled is not None:
        key_record.is_enabled = payload.is_enabled
    if payload.status is not None:
        key_record.status = payload.status.upper()

    session.flush()

    record_admin_audit_event(
        session=session,
        actor_user_id=actor_user_id,
        action="platform_key.update",
        target_type="PlatformAIKey",
        target_id=str(key_record.id),
        metadata={
            "quota_group": key_record.quota_group,
            "status": key_record.status,
            "is_enabled": key_record.is_enabled,
        },
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return key_record


def delete_platform_key(
    session: Session,
    key_id: UUID,
    actor_user_id: UUID | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> bool:
    key_record = get_platform_key(session, key_id)
    if not key_record:
        raise PlatformKeyNotFoundError(f"Platform key with ID '{key_id}' not found.")

    meta = {
        "quota_group": key_record.quota_group,
        "key_hint": key_record.key_hint,
    }
    session.delete(key_record)
    session.flush()

    record_admin_audit_event(
        session=session,
        actor_user_id=actor_user_id,
        action="platform_key.delete",
        target_type="PlatformAIKey",
        target_id=str(key_id),
        metadata=meta,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return True
