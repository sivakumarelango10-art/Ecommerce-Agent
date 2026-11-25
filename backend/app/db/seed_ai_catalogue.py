from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db.models import (
    AIModel,
    AIProvider,
    ModelAccessClass,
    ProviderConnectionStatus,
)
from backend.app.services.admin_audit_service import record_admin_audit_event

logger = logging.getLogger(__name__)

# Official Gemini documentation references
GEMINI_DOCUMENTATION_URL = "https://ai.google.dev/gemini-api/docs/models"
GEMINI_PRICING_URL = "https://ai.google.dev/pricing"
GEMINI_TERMS_URL = "https://ai.google.dev/gemini-api/terms"
GEMINI_RATE_LIMITS_URL = "https://ai.google.dev/gemini-api/docs/rate-limits"

# Documented Gemini Free Tier limitations, rate limit structures, and data-use privacy policy
GEMINI_FREE_TIER_LIMIT_METADATA: dict[str, Any] = {
    "is_free_tier_available": True,
    "has_unlimited_access": False,
    "rate_limits": {
        "quota_enforcement": "per_project",
        "limit_types": ["RPM", "TPM", "RPD"],
        "rate_limits_url": GEMINI_RATE_LIMITS_URL,
        "notes": (
            "Free-tier quotas are enforced dynamically per Google Cloud / AI Studio project "
            "across Requests Per Minute (RPM), Tokens Per Minute (TPM), and Requests Per Day (RPD). "
            "Exact numerical rate limits are managed in the Google AI Studio project console."
        ),
    },
    "data_use_conditions": {
        "free_tier_training_use": True,
        "human_review_permitted": True,
        "paid_tier_training_use": False,
        "promotional_credits_permanent_free": False,
        "sensitive_workloads_allowed": False,
        "requires_policy_approval": True,
        "summary": (
            "Data submitted under the Google Gemini Free Tier (prompts, inputs, and generated content) "
            "may be used by Google to review, refine, and improve products, services, and machine learning models, "
            "and may be inspected by human reviewers. Paid-tier usage does not use customer content to train models. "
            "Promotional credits provide temporary billing relief and do not constitute permanent free-tier access. "
            "Workloads handling sensitive customer data, orders, payment details, credentials, or PII require a "
            "verified paid tier or explicit data-use policy approval before routing."
        ),
    },
    "documentation": {
        "pricing_url": GEMINI_PRICING_URL,
        "terms_url": GEMINI_TERMS_URL,
        "rate_limits_url": GEMINI_RATE_LIMITS_URL,
    },
}

# Retired / deprecated model identifiers to deactivate if found in existing databases
RETIRED_GEMINI_MODEL_IDENTIFIERS: set[str] = {
    # 2.5 series
    "gemini-2.5-flash",
    "gemini-2.5-pro",
    # 2.0 series
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
    "gemini-2.0-pro",
    # 1.5 series
    "gemini-1.5-pro",
    "gemini-1.5-flash",
    "gemini-1.5-flash-8b",
    # 1.0 series
    "gemini-1.0-pro",
}

INITIAL_PROVIDERS_DATA: list[dict[str, Any]] = [
    {
        "code": "google_gemini",
        "display_name": "Google Gemini",
        "adapter_type": "google_gemini",
        "base_url": "https://generativelanguage.googleapis.com",
        "documentation_url": "https://ai.google.dev/docs",
        "connection_status": ProviderConnectionStatus.AVAILABLE.value,
        "priority": 10,
        "is_enabled": True,
    },
    {
        "code": "groq",
        "display_name": "Groq",
        "adapter_type": "groq",
        "base_url": "https://api.groq.com/openai/v1",
        "documentation_url": "https://console.groq.com/docs",
        "connection_status": ProviderConnectionStatus.PLANNED.value,
        "priority": 20,
        "is_enabled": True,
    },
    {
        "code": "openrouter",
        "display_name": "OpenRouter",
        "adapter_type": "openrouter",
        "base_url": "https://openrouter.ai/api/v1",
        "documentation_url": "https://openrouter.ai/docs",
        "connection_status": ProviderConnectionStatus.PLANNED.value,
        "priority": 30,
        "is_enabled": True,
    },
    {
        "code": "sarvam",
        "display_name": "Sarvam AI",
        "adapter_type": "sarvam",
        "base_url": "https://api.sarvam.ai",
        "documentation_url": "https://docs.sarvam.ai",
        "connection_status": ProviderConnectionStatus.PLANNED.value,
        "priority": 40,
        "is_enabled": True,
    },
    {
        "code": "cloudflare_workers_ai",
        "display_name": "Cloudflare Workers AI",
        "adapter_type": "cloudflare",
        "base_url": "https://api.cloudflare.com/client/v4",
        "documentation_url": "https://developers.cloudflare.com/workers-ai",
        "connection_status": ProviderConnectionStatus.PLANNED.value,
        "priority": 50,
        "is_enabled": True,
    },
    {
        "code": "cerebras",
        "display_name": "Cerebras",
        "adapter_type": "cerebras",
        "base_url": "https://api.cerebras.ai/v1",
        "documentation_url": "https://inference-docs.cerebras.ai",
        "connection_status": ProviderConnectionStatus.PLANNED.value,
        "priority": 60,
        "is_enabled": True,
    },
    {
        "code": "huggingface",
        "display_name": "Hugging Face Inference",
        "adapter_type": "huggingface",
        "base_url": "https://api-inference.huggingface.co",
        "documentation_url": "https://huggingface.co/docs/api-inference",
        "connection_status": ProviderConnectionStatus.PLANNED.value,
        "priority": 70,
        "is_enabled": True,
    },
    {
        "code": "mistral",
        "display_name": "Mistral AI",
        "adapter_type": "mistral",
        "base_url": "https://api.mistral.ai/v1",
        "documentation_url": "https://docs.mistral.ai",
        "connection_status": ProviderConnectionStatus.PLANNED.value,
        "priority": 80,
        "is_enabled": True,
    },
    {
        "code": "deepseek",
        "display_name": "DeepSeek",
        "adapter_type": "deepseek",
        "base_url": "https://api.deepseek.com/v1",
        "documentation_url": "https://platform.deepseek.com/docs",
        "connection_status": ProviderConnectionStatus.PLANNED.value,
        "priority": 90,
        "is_enabled": True,
    },
    {
        "code": "nvidia_nim",
        "display_name": "NVIDIA NIM",
        "adapter_type": "nvidia_nim",
        "base_url": "https://integrate.api.nvidia.com/v1",
        "documentation_url": "https://docs.api.nvidia.com",
        "connection_status": ProviderConnectionStatus.PLANNED.value,
        "priority": 100,
        "is_enabled": True,
    },
    {
        "code": "together",
        "display_name": "Together AI",
        "adapter_type": "together",
        "base_url": "https://api.together.xyz/v1",
        "documentation_url": "https://docs.together.ai",
        "connection_status": ProviderConnectionStatus.PLANNED.value,
        "priority": 110,
        "is_enabled": True,
    },
    {
        "code": "fireworks",
        "display_name": "Fireworks AI",
        "adapter_type": "fireworks",
        "base_url": "https://api.fireworks.ai/inference/v1",
        "documentation_url": "https://readme.fireworks.ai",
        "connection_status": ProviderConnectionStatus.PLANNED.value,
        "priority": 120,
        "is_enabled": True,
    },
    {
        "code": "sambanova",
        "display_name": "SambaNova",
        "adapter_type": "sambanova",
        "base_url": "https://api.sambanova.ai/v1",
        "documentation_url": "https://docs.sambanova.ai",
        "connection_status": ProviderConnectionStatus.PLANNED.value,
        "priority": 130,
        "is_enabled": True,
    },
    {
        "code": "cohere",
        "display_name": "Cohere",
        "adapter_type": "cohere",
        "base_url": "https://api.cohere.com/v2",
        "documentation_url": "https://docs.cohere.com",
        "connection_status": ProviderConnectionStatus.PLANNED.value,
        "priority": 140,
        "is_enabled": True,
    },
    {
        "code": "xai",
        "display_name": "xAI",
        "adapter_type": "xai",
        "base_url": "https://api.x.ai/v1",
        "documentation_url": "https://docs.x.ai",
        "connection_status": ProviderConnectionStatus.PLANNED.value,
        "priority": 150,
        "is_enabled": True,
    },
    {
        "code": "openai",
        "display_name": "OpenAI",
        "adapter_type": "openai",
        "base_url": "https://api.openai.com/v1",
        "documentation_url": "https://platform.openai.com/docs",
        "connection_status": ProviderConnectionStatus.PLANNED.value,
        "priority": 160,
        "is_enabled": True,
    },
    {
        "code": "perplexity",
        "display_name": "Perplexity",
        "adapter_type": "perplexity",
        "base_url": "https://api.perplexity.ai",
        "documentation_url": "https://docs.perplexity.ai",
        "connection_status": ProviderConnectionStatus.PLANNED.value,
        "priority": 170,
        "is_enabled": True,
    },
    {
        "code": "github_models",
        "display_name": "GitHub Models",
        "adapter_type": "github_models",
        "base_url": "https://models.inference.ai.azure.com",
        "documentation_url": "https://docs.github.com/en/github-models",
        "connection_status": ProviderConnectionStatus.PLANNED.value,
        "priority": 180,
        "is_enabled": True,
    },
    {
        "code": "aws_bedrock",
        "display_name": "AWS Bedrock",
        "adapter_type": "aws_bedrock",
        "base_url": "https://bedrock-runtime.us-east-1.amazonaws.com",
        "documentation_url": "https://docs.aws.amazon.com/bedrock",
        "connection_status": ProviderConnectionStatus.PLANNED.value,
        "priority": 190,
        "is_enabled": True,
    },
    {
        "code": "azure_ai_foundry",
        "display_name": "Azure AI Foundry",
        "adapter_type": "azure_ai_foundry",
        "base_url": "https://models.inference.ai.azure.com",
        "documentation_url": "https://learn.microsoft.com/azure/ai-studio",
        "connection_status": ProviderConnectionStatus.PLANNED.value,
        "priority": 200,
        "is_enabled": True,
    },
]

INITIAL_GEMINI_MODELS: list[dict[str, Any]] = [
    {
        "model_identifier": "gemini-3.8-flash",
        "display_name": "Gemini 3.8 Flash",
        "description": "Current stable multimodal workhorse model optimized for adaptive reasoning, high throughput, and low latency.",
        "supports_text": True,
        "supports_vision": True,
        "supports_tools": True,
        "supports_streaming": True,
        "supports_json_mode": True,
        "supports_embeddings": False,
        "supports_speech_input": False,
        "supports_speech_output": False,
        "context_window": 1048576,
        "max_output_tokens": 8192,
        "access_class": ModelAccessClass.FREE_TIER.value,
        "free_tier_limit_metadata": dict(GEMINI_FREE_TIER_LIMIT_METADATA),
        "terms_url": GEMINI_TERMS_URL,
        "pricing_url": GEMINI_PRICING_URL,
        "last_pricing_verified_at": None,
        "is_default": True,
        "is_enabled": True,
    },
    {
        "model_identifier": "gemini-3.5-flash-lite",
        "display_name": "Gemini 3.5 Flash-Lite",
        "description": "High-efficiency, lightweight multimodal model optimized for cost-sensitive, high-volume tasks and low latency.",
        "supports_text": True,
        "supports_vision": True,
        "supports_tools": True,
        "supports_streaming": True,
        "supports_json_mode": True,
        "supports_embeddings": False,
        "supports_speech_input": False,
        "supports_speech_output": False,
        "context_window": 1048576,
        "max_output_tokens": 8192,
        "access_class": ModelAccessClass.FREE_TIER.value,
        "free_tier_limit_metadata": dict(GEMINI_FREE_TIER_LIMIT_METADATA),
        "terms_url": GEMINI_TERMS_URL,
        "pricing_url": GEMINI_PRICING_URL,
        "last_pricing_verified_at": None,
        "is_default": False,
        "is_enabled": True,
    },
]


# Retired / deprecated Groq model identifiers to deactivate if found in existing databases
RETIRED_GROQ_MODEL_IDENTIFIERS: set[str] = {
    "mixtral-8x7b-32768",
    "gemma2-9b-it",
    "llama-3.1-70b-versatile",
    "llama3-70b-8192",
    "llama3-8b-8192",
    "llama-3.2-1b-preview",
    "llama-3.2-3b-preview",
    "llama-3.2-11b-vision-preview",
    "llama-3.2-90b-vision-preview",
    "llama-3.2-90b-text-preview",
    "qwen-qwq-32b",
    "llama-guard-3-8b",
    "distil-whisper-large-v3-en",
    "deepseek-r1-distill-llama-70b",
}

INITIAL_GROQ_MODELS: list[dict[str, Any]] = [
    {
        "model_identifier": "openai/gpt-oss-120b",
        "display_name": "GPT-OSS 120B (Groq)",
        "description": "Candidate default open-weights MoE reasoning model on Groq hardware. High throughput, low latency.",
        "supports_text": True,
        "supports_vision": False,
        "supports_tools": True,
        "supports_streaming": False,
        "supports_json_mode": True,
        "supports_embeddings": False,
        "supports_speech_input": False,
        "supports_speech_output": False,
        "context_window": 131072,
        "max_output_tokens": 65536,
        "access_class": ModelAccessClass.PAY_AS_YOU_GO.value,
        "free_tier_limit_metadata": None,
        "terms_url": "https://groq.com/terms-of-use",
        "pricing_url": "https://groq.com/pricing",
        "last_pricing_verified_at": None,
        "is_default": True,
        "is_enabled": True,
    },
    {
        "model_identifier": "openai/gpt-oss-20b",
        "display_name": "GPT-OSS 20B (Groq)",
        "description": "High-efficiency, lightweight open-weights MoE model on Groq hardware.",
        "supports_text": True,
        "supports_vision": False,
        "supports_tools": True,
        "supports_streaming": False,
        "supports_json_mode": True,
        "supports_embeddings": False,
        "supports_speech_input": False,
        "supports_speech_output": False,
        "context_window": 131072,
        "max_output_tokens": 65536,
        "access_class": ModelAccessClass.PAY_AS_YOU_GO.value,
        "free_tier_limit_metadata": None,
        "terms_url": "https://groq.com/terms-of-use",
        "pricing_url": "https://groq.com/pricing",
        "last_pricing_verified_at": None,
        "is_default": False,
        "is_enabled": True,
    },
    {
        "model_identifier": "llama-3.3-70b-versatile",
        "display_name": "Llama 3.3 70B Versatile (Groq)",
        "description": "[ENTERPRISE_RESTRICTED] High-performance Llama 3.3 on Groq. Free/developer tier deprecated Aug 16, 2026; requires enterprise tier entitlement.",
        "supports_text": True,
        "supports_vision": False,
        "supports_tools": True,
        "supports_streaming": False,
        "supports_json_mode": True,
        "supports_embeddings": False,
        "supports_speech_input": False,
        "supports_speech_output": False,
        "context_window": 131072,
        "max_output_tokens": 32768,
        "access_class": ModelAccessClass.UNKNOWN.value,
        "free_tier_limit_metadata": None,
        "terms_url": "https://groq.com/terms-of-use",
        "pricing_url": "https://groq.com/pricing",
        "last_pricing_verified_at": None,
        "is_default": False,
        "is_enabled": False,
    },
    {
        "model_identifier": "llama-3.1-8b-instant",
        "display_name": "Llama 3.1 8B Instant (Groq)",
        "description": "[ENTERPRISE_RESTRICTED] Fast, low-latency Llama 3.1 on Groq. Free/developer tier deprecated Aug 16, 2026; requires enterprise tier entitlement.",
        "supports_text": True,
        "supports_vision": False,
        "supports_tools": True,
        "supports_streaming": False,
        "supports_json_mode": True,
        "supports_embeddings": False,
        "supports_speech_input": False,
        "supports_speech_output": False,
        "context_window": 131072,
        "max_output_tokens": 8192,
        "access_class": ModelAccessClass.UNKNOWN.value,
        "free_tier_limit_metadata": None,
        "terms_url": "https://groq.com/terms-of-use",
        "pricing_url": "https://groq.com/pricing",
        "last_pricing_verified_at": None,
        "is_default": False,
        "is_enabled": False,
    },
]


def seed_ai_provider_catalogue(
    session: Session,
    actor_user_id: UUID | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> dict[str, int]:
    """Deterministically and idempotently seed the AI provider catalogue and baseline models.

    No external network calls are made.
    """
    providers_created = 0
    providers_updated = 0
    models_created = 0

    gemini_provider_id: UUID | None = None
    groq_provider_id: UUID | None = None

    for item in INITIAL_PROVIDERS_DATA:
        code = item["code"]
        existing = session.scalar(select(AIProvider).where(AIProvider.code == code))
        if existing:
            # Update metadata if needed
            existing.display_name = item["display_name"]
            existing.adapter_type = item["adapter_type"]
            existing.base_url = item["base_url"]
            existing.documentation_url = item["documentation_url"]
            existing.priority = item["priority"]
            if code == "google_gemini":
                gemini_provider_id = existing.id
            elif code == "groq":
                groq_provider_id = existing.id
            providers_updated += 1
        else:
            provider = AIProvider(
                code=code,
                display_name=item["display_name"],
                adapter_type=item["adapter_type"],
                base_url=item["base_url"],
                documentation_url=item["documentation_url"],
                connection_status=item["connection_status"],
                priority=item["priority"],
                is_enabled=item["is_enabled"],
            )
            session.add(provider)
            session.flush()
            if code == "google_gemini":
                gemini_provider_id = provider.id
            elif code == "groq":
                groq_provider_id = provider.id
            providers_created += 1

    session.flush()

    # Deactivate retired models if present in existing database
    if gemini_provider_id:
        retired_models = session.scalars(
            select(AIModel).where(
                AIModel.provider_id == gemini_provider_id,
                AIModel.model_identifier.in_(RETIRED_GEMINI_MODEL_IDENTIFIERS),
            )
        ).all()
        for rm in retired_models:
            if rm.is_enabled:
                rm.is_enabled = False
            if rm.is_default:
                rm.is_default = False
            if "[RETIRED" not in (rm.description or ""):
                rm.description = f"{rm.description or ''} [RETIRED / DEPRECATED]".strip()

    if groq_provider_id:
        retired_groq = session.scalars(
            select(AIModel).where(
                AIModel.provider_id == groq_provider_id,
                AIModel.model_identifier.in_(RETIRED_GROQ_MODEL_IDENTIFIERS),
            )
        ).all()
        for rm in retired_groq:
            if rm.is_enabled:
                rm.is_enabled = False
            if rm.is_default:
                rm.is_default = False
            if "[RETIRED" not in (rm.description or ""):
                rm.description = f"{rm.description or ''} [RETIRED / DEPRECATED]".strip()

    # Seed baseline Gemini models
    if gemini_provider_id:
        has_current_default = session.scalar(
            select(AIModel.id).where(
                AIModel.provider_id == gemini_provider_id,
                AIModel.is_default.is_(True),
            ).limit(1)
        ) is not None

        for model_data in INITIAL_GEMINI_MODELS:
            identifier = model_data["model_identifier"]
            existing_model = session.scalar(
                select(AIModel).where(
                    AIModel.provider_id == gemini_provider_id,
                    AIModel.model_identifier == identifier,
                )
            )
            if existing_model:
                # Idempotent update: refresh metadata but preserve admin configurations
                # (do NOT overwrite is_enabled, do NOT overwrite is_default)
                existing_model.display_name = model_data["display_name"]
                existing_model.description = model_data["description"]
                existing_model.supports_text = model_data["supports_text"]
                existing_model.supports_vision = model_data["supports_vision"]
                existing_model.supports_tools = model_data["supports_tools"]
                existing_model.supports_streaming = model_data["supports_streaming"]
                existing_model.supports_json_mode = model_data["supports_json_mode"]
                existing_model.supports_embeddings = model_data["supports_embeddings"]
                existing_model.supports_speech_input = model_data["supports_speech_input"]
                existing_model.supports_speech_output = model_data["supports_speech_output"]
                existing_model.context_window = model_data["context_window"]
                existing_model.max_output_tokens = model_data["max_output_tokens"]
                existing_model.access_class = model_data["access_class"]
                existing_model.free_tier_limit_metadata = model_data.get("free_tier_limit_metadata")
                existing_model.terms_url = model_data["terms_url"]
                existing_model.pricing_url = model_data["pricing_url"]
                existing_model.last_pricing_verified_at = model_data["last_pricing_verified_at"]
            else:
                should_be_default = model_data["is_default"] and not has_current_default
                model = AIModel(
                    provider_id=gemini_provider_id,
                    model_identifier=identifier,
                    display_name=model_data["display_name"],
                    description=model_data["description"],
                    supports_text=model_data["supports_text"],
                    supports_vision=model_data["supports_vision"],
                    supports_tools=model_data["supports_tools"],
                    supports_streaming=model_data["supports_streaming"],
                    supports_json_mode=model_data["supports_json_mode"],
                    supports_embeddings=model_data["supports_embeddings"],
                    supports_speech_input=model_data["supports_speech_input"],
                    supports_speech_output=model_data["supports_speech_output"],
                    context_window=model_data["context_window"],
                    max_output_tokens=model_data["max_output_tokens"],
                    pricing_input_per_million=None,
                    pricing_output_per_million=None,
                    access_class=model_data["access_class"],
                    free_tier_limit_metadata=model_data.get("free_tier_limit_metadata"),
                    is_default=should_be_default,
                    is_enabled=model_data["is_enabled"],
                    terms_url=model_data["terms_url"],
                    pricing_url=model_data["pricing_url"],
                    last_pricing_verified_at=model_data["last_pricing_verified_at"],
                )
                session.add(model)
                models_created += 1
                if should_be_default:
                    has_current_default = True

    # Seed baseline Groq models (candidate catalogue, provider remains PLANNED)
    if groq_provider_id:
        has_current_groq_default = session.scalar(
            select(AIModel.id).where(
                AIModel.provider_id == groq_provider_id,
                AIModel.is_default.is_(True),
            ).limit(1)
        ) is not None

        for model_data in INITIAL_GROQ_MODELS:
            identifier = model_data["model_identifier"]
            existing_model = session.scalar(
                select(AIModel).where(
                    AIModel.provider_id == groq_provider_id,
                    AIModel.model_identifier == identifier,
                )
            )
            if existing_model:
                existing_model.display_name = model_data["display_name"]
                existing_model.description = model_data["description"]
                existing_model.supports_text = model_data["supports_text"]
                existing_model.supports_vision = model_data["supports_vision"]
                existing_model.supports_tools = model_data["supports_tools"]
                existing_model.supports_streaming = model_data["supports_streaming"]
                existing_model.supports_json_mode = model_data["supports_json_mode"]
                existing_model.supports_embeddings = model_data["supports_embeddings"]
                existing_model.supports_speech_input = model_data["supports_speech_input"]
                existing_model.supports_speech_output = model_data["supports_speech_output"]
                existing_model.context_window = model_data["context_window"]
                existing_model.max_output_tokens = model_data["max_output_tokens"]
                existing_model.access_class = model_data["access_class"]
                existing_model.free_tier_limit_metadata = model_data.get("free_tier_limit_metadata")
                existing_model.terms_url = model_data["terms_url"]
                existing_model.pricing_url = model_data["pricing_url"]
                existing_model.last_pricing_verified_at = model_data["last_pricing_verified_at"]
                if identifier in {"llama-3.3-70b-versatile", "llama-3.1-8b-instant"}:
                    existing_model.is_enabled = False
                    existing_model.is_default = False
                    existing_model.access_class = ModelAccessClass.UNKNOWN.value
                    if "[ENTERPRISE_RESTRICTED]" not in (existing_model.description or ""):
                        existing_model.description = f"[ENTERPRISE_RESTRICTED] {existing_model.description or ''}".strip()
            else:
                should_be_default = model_data["is_default"] and not has_current_groq_default
                model = AIModel(
                    provider_id=groq_provider_id,
                    model_identifier=identifier,
                    display_name=model_data["display_name"],
                    description=model_data["description"],
                    supports_text=model_data["supports_text"],
                    supports_vision=model_data["supports_vision"],
                    supports_tools=model_data["supports_tools"],
                    supports_streaming=model_data["supports_streaming"],
                    supports_json_mode=model_data["supports_json_mode"],
                    supports_embeddings=model_data["supports_embeddings"],
                    supports_speech_input=model_data["supports_speech_input"],
                    supports_speech_output=model_data["supports_speech_output"],
                    context_window=model_data["context_window"],
                    max_output_tokens=model_data["max_output_tokens"],
                    pricing_input_per_million=None,
                    pricing_output_per_million=None,
                    access_class=model_data["access_class"],
                    free_tier_limit_metadata=model_data.get("free_tier_limit_metadata"),
                    is_default=should_be_default,
                    is_enabled=model_data["is_enabled"],
                    terms_url=model_data["terms_url"],
                    pricing_url=model_data["pricing_url"],
                    last_pricing_verified_at=model_data["last_pricing_verified_at"],
                )
                session.add(model)
                models_created += 1
                if should_be_default:
                    has_current_groq_default = True

    session.flush()

    record_admin_audit_event(
        session=session,
        actor_user_id=actor_user_id,
        action="ai_catalogue.seed",
        target_type="AICatalogue",
        target_id="initial_20_providers",
        metadata={
            "providers_created": providers_created,
            "providers_updated": providers_updated,
            "models_created": models_created,
        },
        ip_address=ip_address,
        user_agent=user_agent,
    )

    return {
        "providers_created": providers_created,
        "providers_updated": providers_updated,
        "models_created": models_created,
    }
