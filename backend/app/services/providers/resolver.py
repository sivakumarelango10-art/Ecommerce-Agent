from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from backend.app.db.models import AIModel, AIProvider
from backend.app.services.providers.base import (
    AIProviderAdapter,
    AIProviderError,
)

logger = logging.getLogger(__name__)


class UnknownProviderError(AIProviderError):
    """Raised when the requested provider code is not found in the database registry."""

    def __init__(self, provider_code: str) -> None:
        super().__init__(
            f"AI provider '{provider_code}' is not registered in the system.",
            provider_code=provider_code,
            status_code=404,
            is_retryable=False,
            error_category="UNKNOWN_PROVIDER",
        )


class DisabledProviderError(AIProviderError):
    """Raised when the requested provider is disabled in the database registry."""

    def __init__(self, provider_code: str) -> None:
        super().__init__(
            f"AI provider '{provider_code}' is currently disabled.",
            provider_code=provider_code,
            status_code=403,
            is_retryable=False,
            error_category="DISABLED_PROVIDER",
        )


class UnknownModelError(AIProviderError):
    """Raised when the requested model is not found under the specified provider."""

    def __init__(self, provider_code: str, model_name: str) -> None:
        super().__init__(
            f"Model '{model_name}' is not registered under provider '{provider_code}'.",
            provider_code=provider_code,
            model=model_name,
            status_code=404,
            is_retryable=False,
            error_category="UNKNOWN_MODEL",
        )


class DisabledModelError(AIProviderError):
    """Raised when the requested model is registered but currently disabled."""

    def __init__(self, provider_code: str, model_name: str) -> None:
        super().__init__(
            f"Model '{model_name}' under provider '{provider_code}' is currently disabled.",
            provider_code=provider_code,
            model=model_name,
            status_code=403,
            is_retryable=False,
            error_category="DISABLED_MODEL",
        )


class ProviderAdapterUnavailableError(AIProviderError):
    """Raised when a provider exists in the catalogue (e.g. PLANNED) but lacks an operational runtime adapter."""

    def __init__(self, provider_code: str, connection_status: str | None = None) -> None:
        msg = f"Operational runtime adapter for provider '{provider_code}' is unavailable"
        if connection_status:
            msg += f" (catalogue status: {connection_status})"
        msg += ". Only 'google_gemini' runtime adapter is operational in Phase 6F.1."
        super().__init__(
            msg,
            provider_code=provider_code,
            status_code=501,
            is_retryable=False,
            error_category="ADAPTER_UNAVAILABLE",
        )


class ProviderTrafficNotPermittedError(AIProviderError):
    """Raised when a provider has an operational adapter registered, but its catalogue status (e.g. PLANNED) does not permit ordinary customer traffic."""

    def __init__(self, provider_code: str, connection_status: str | None = None) -> None:
        msg = (
            f"AI provider '{provider_code}' (catalogue status: {connection_status or 'UNKNOWN'}) "
            "is not permitted for customer traffic. Only providers with 'AVAILABLE' status "
            "are permitted for production customer routing."
        )
        super().__init__(
            msg,
            provider_code=provider_code,
            status_code=403,
            is_retryable=False,
            error_category="TRAFFIC_NOT_PERMITTED",
        )


class ProviderRuntimeResolver:
    """Central resolver that verifies provider and model metadata before selecting an adapter.

    Enforces strict validation rules:
    - Rejects unknown providers (404)
    - Rejects disabled providers (403)
    - Rejects unknown models (404)
    - Rejects disabled models (403)
    - Rejects catalogue-only providers lacking an operational adapter (501)
    - Never silently falls back to an unrequested provider
    """

    def __init__(self) -> None:
        self._adapters: dict[str, AIProviderAdapter] = {}

    def register_adapter(self, adapter: AIProviderAdapter) -> None:
        """Register an operational provider adapter instance."""
        self._adapters[adapter.provider_code] = adapter
        logger.info("Registered AI provider runtime adapter: %s", adapter.provider_code)

    def unregister_adapter(self, provider_code: str) -> None:
        """Unregister an adapter instance."""
        self._adapters.pop(provider_code, None)

    def get_adapter(self, provider_code: str) -> AIProviderAdapter | None:
        """Retrieve registered adapter without database validation."""
        return self._adapters.get(provider_code)

    def has_adapter(self, provider_code: str) -> bool:
        """Check if an adapter is registered in memory."""
        return provider_code in self._adapters

    def list_registered_providers(self) -> list[str]:
        """List provider codes currently registered in memory."""
        return sorted(self._adapters.keys())

    def get_provider_lifecycle_state(
        self,
        session: Session,
        provider_code: str,
    ) -> dict[str, Any]:
        """Inspect and return distinct lifecycle states for a provider:
        1. adapter_registered: In-memory runtime adapter exists
        2. metadata_enabled: Database AIProvider is_enabled == True
        3. metadata_validated: Database AIProvider record exists and is valid
        4. is_operational: Adapter is registered AND metadata is enabled
        5. customer_traffic_permitted: is_operational AND connection_status == 'AVAILABLE'
        """
        provider = session.scalar(
            select(AIProvider).where(AIProvider.code == provider_code)
        )
        adapter_registered = self.has_adapter(provider_code)
        if provider is None:
            return {
                "provider_code": provider_code,
                "metadata_validated": False,
                "metadata_enabled": False,
                "connection_status": None,
                "adapter_registered": adapter_registered,
                "is_operational": False,
                "customer_traffic_permitted": False,
            }

        is_operational = adapter_registered and provider.is_enabled
        customer_traffic_permitted = is_operational and (provider.connection_status == "AVAILABLE")

        return {
            "provider_code": provider_code,
            "metadata_validated": True,
            "metadata_enabled": provider.is_enabled,
            "connection_status": provider.connection_status,
            "adapter_registered": adapter_registered,
            "is_operational": is_operational,
            "customer_traffic_permitted": customer_traffic_permitted,
        }

    def resolve(
        self,
        session: Session,
        provider_code: str,
        model_name: str | None = None,
    ) -> tuple[AIProviderAdapter, AIModel | None]:
        """Resolve adapter and model synchronously for production customer traffic.

        Strictly enforces that the provider has 'AVAILABLE' connection status in the
        catalogue. No bypass parameter is exposed on this production method.

        Returns (adapter, model_or_none).
        Raises UnknownProviderError, DisabledProviderError, UnknownModelError,
               DisabledModelError, ProviderAdapterUnavailableError, or
               ProviderTrafficNotPermittedError.
        """
        # 1. Verify Provider metadata in DB
        provider = session.scalar(
            select(AIProvider).where(AIProvider.code == provider_code)
        )
        if provider is None:
            raise UnknownProviderError(provider_code)

        if not provider.is_enabled:
            raise DisabledProviderError(provider_code)

        # 2. Verify Model metadata if specified
        model_record: AIModel | None = None
        if model_name:
            clean_model = model_name.strip()
            model_record = session.scalar(
                select(AIModel).where(
                    AIModel.provider_id == provider.id,
                    AIModel.model_identifier == clean_model,
                )
            )
            # If not found by identifier, check display_name
            if model_record is None:
                model_record = session.scalar(
                    select(AIModel).where(
                        AIModel.provider_id == provider.id,
                        AIModel.display_name == clean_model,
                    )
                )

            if model_record is None:
                raise UnknownModelError(provider_code, clean_model)

            if not model_record.is_enabled:
                raise DisabledModelError(provider_code, clean_model)

        # 3. Verify operational adapter exists in runtime
        adapter = self._adapters.get(provider_code)
        if adapter is None:
            raise ProviderAdapterUnavailableError(
                provider_code,
                connection_status=provider.connection_status,
            )

        # 4. Strictly enforce customer traffic permission gate (AVAILABLE only)
        if provider.connection_status != "AVAILABLE":
            raise ProviderTrafficNotPermittedError(
                provider_code,
                connection_status=provider.connection_status,
            )

        return adapter, model_record

    async def resolve_async(
        self,
        session: AsyncSession,
        provider_code: str,
        model_name: str | None = None,
    ) -> tuple[AIProviderAdapter, AIModel | None]:
        """Resolve adapter and model asynchronously for production customer traffic.

        Strictly enforces that the provider has 'AVAILABLE' connection status in the
        catalogue. No bypass parameter is exposed on this production method.
        """
        # 1. Verify Provider metadata in DB
        stmt_p = select(AIProvider).where(AIProvider.code == provider_code)
        res_p = await session.execute(stmt_p)
        provider = res_p.scalar_one_or_none()

        if provider is None:
            raise UnknownProviderError(provider_code)

        if not provider.is_enabled:
            raise DisabledProviderError(provider_code)

        # 2. Verify Model metadata if specified
        model_record: AIModel | None = None
        if model_name:
            clean_model = model_name.strip()
            stmt_m = select(AIModel).where(
                AIModel.provider_id == provider.id,
                AIModel.model_identifier == clean_model,
            )
            res_m = await session.execute(stmt_m)
            model_record = res_m.scalar_one_or_none()

            if model_record is None:
                stmt_m_dn = select(AIModel).where(
                    AIModel.provider_id == provider.id,
                    AIModel.display_name == clean_model,
                )
                res_m_dn = await session.execute(stmt_m_dn)
                model_record = res_m_dn.scalar_one_or_none()

            if model_record is None:
                raise UnknownModelError(provider_code, clean_model)

            if not model_record.is_enabled:
                raise DisabledModelError(provider_code, clean_model)

        # 3. Verify operational adapter exists in runtime
        adapter = self._adapters.get(provider_code)
        if adapter is None:
            raise ProviderAdapterUnavailableError(
                provider_code,
                connection_status=provider.connection_status,
            )

        # 4. Strictly enforce customer traffic permission gate (AVAILABLE only)
        if provider.connection_status != "AVAILABLE":
            raise ProviderTrafficNotPermittedError(
                provider_code,
                connection_status=provider.connection_status,
            )

        return adapter, model_record
