from collections.abc import Callable
from contextlib import asynccontextmanager
import logging
from typing import Protocol

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from backend.app.api.routes.admin import router as admin_router
from backend.app.api.routes.auth import router as auth_router
from backend.app.api.routes.cart import router as cart_router
from backend.app.api.routes.catalog import router as catalog_router
from backend.app.api.routes.checkout import router as checkout_router
from backend.app.api.routes.customers import router as customers_router
from backend.app.api.routes.gemini_settings import router as gemini_settings_router
from backend.app.api.routes.health import router as health_router
from backend.app.api.routes.orders import router as orders_router
from backend.app.api.routes.questions import router as questions_router
from backend.app.api.routes.shopping import router as shopping_router
from backend.app.core.config import Settings, load_settings
from backend.app.core.logging import configure_logging
from backend.app.core.security_headers import SecurityHeadersMiddleware
from backend.app.db.session import create_database_engine, create_session_factory
from backend.app.services.email_service import EmailDeliveryProvider
from backend.app.services.gemini_service import GeminiService
from backend.app.services.outbox_worker import GuestEmailOutboxWorker
from backend.app.services.shipping_tax_policy import PolicyConfigurationError

logger = logging.getLogger(__name__)


class CloseableService(Protocol):
    def close(self) -> None: ...


SettingsLoader = Callable[[], Settings]
ServiceFactory = Callable[[Settings], CloseableService]
EngineFactory = Callable[[str], Engine]


def create_app(
    settings_loader: SettingsLoader = load_settings,
    service_factory: ServiceFactory = GeminiService,
    engine_factory: EngineFactory = create_database_engine,
    email_provider: EmailDeliveryProvider | None = None,
    outbox_worker_enabled: bool | None = None,
) -> FastAPI:
    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]
    try:
        initial_settings = settings_loader()
        cors_origins = list(initial_settings.allowed_origins)
    except Exception:
        pass

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        configure_logging()
        settings = settings_loader()
        cors_origins.clear()
        cors_origins.extend(settings.allowed_origins)

        engine = engine_factory(settings.database_url.get_secret_value())
        try:
            service = service_factory(settings)
        except Exception:
            engine.dispose()
            raise
        app.state.settings = settings
        app.state.db_engine = engine
        app.state.db_session_factory = create_session_factory(engine)
        app.state.gemini_service = service

        worker = None
        worker_enabled = (
            outbox_worker_enabled
            if outbox_worker_enabled is not None
            else settings.outbox_worker_enabled
        )
        if worker_enabled:
            worker = GuestEmailOutboxWorker(
                session_factory=app.state.db_session_factory,
                settings=settings,
                email_provider=email_provider,
                poll_interval_seconds=settings.outbox_poll_interval_seconds,
                batch_size=settings.outbox_batch_size,
                lease_seconds=settings.outbox_lease_seconds,
            )
            app.state.outbox_worker = worker
            await worker.start()

        try:
            yield
        finally:
            if worker is not None:
                await worker.stop()
            try:
                service.close()
            finally:
                engine.dispose()

    application = FastAPI(
        title="Ecommerce Agent API",
        version="0.2.0",
        lifespan=lifespan,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD", "PATCH"],
        allow_headers=["*"],
        expose_headers=["Idempotent-Replay", "Retry-After"],
    )
    application.add_middleware(SecurityHeadersMiddleware)

    application.include_router(health_router)
    application.include_router(auth_router, prefix="/api/v1")
    application.include_router(admin_router, prefix="/api/v1")
    application.include_router(customers_router, prefix="/api/v1")
    application.include_router(gemini_settings_router, prefix="/api/v1")
    application.include_router(catalog_router, prefix="/api/v1")
    application.include_router(cart_router, prefix="/api/v1")
    application.include_router(checkout_router, prefix="/api/v1")
    application.include_router(orders_router, prefix="/api/v1")
    application.include_router(questions_router, prefix="/api/v1")
    application.include_router(shopping_router, prefix="/api/v1")

    @application.exception_handler(PolicyConfigurationError)
    async def policy_configuration_error_handler(_request, exc: PolicyConfigurationError) -> JSONResponse:
        logger.error(
            "Store policy configuration error",
            extra={"event": "policy_config_missing", "error_type": type(exc).__name__},
        )
        return JSONResponse(
            status_code=503,
            content={"detail": "Checkout service is temporarily unavailable due to unconfigured store policies."},
        )

    @application.exception_handler(SQLAlchemyError)
    async def database_error_handler(_request, exc: SQLAlchemyError) -> JSONResponse:
        logger.error(
            "Database operation failed",
            extra={"event": "database_error", "error_type": type(exc).__name__},
        )
        return JSONResponse(
            status_code=503,
            content={"detail": "Database service is temporarily unavailable."},
        )

    return application


app = create_app()
