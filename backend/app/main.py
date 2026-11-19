from collections.abc import Callable
from contextlib import asynccontextmanager
import logging
from typing import Protocol

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from backend.app.api.routes.auth import router as auth_router
from backend.app.api.routes.catalog import router as catalog_router
from backend.app.api.routes.customers import router as customers_router
from backend.app.api.routes.health import router as health_router
from backend.app.api.routes.questions import router as questions_router
from backend.app.core.config import Settings, load_settings
from backend.app.core.logging import configure_logging
from backend.app.db.session import create_database_engine, create_session_factory
from backend.app.services.gemini_service import GeminiService

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
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        configure_logging()
        settings = settings_loader()
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
        try:
            yield
        finally:
            try:
                service.close()
            finally:
                engine.dispose()

    application = FastAPI(
        title="Ecommerce Agent API",
        version="0.2.0",
        lifespan=lifespan,
    )
    application.include_router(health_router)
    application.include_router(auth_router, prefix="/api/v1")
    application.include_router(customers_router, prefix="/api/v1")
    application.include_router(catalog_router, prefix="/api/v1")
    application.include_router(questions_router, prefix="/api/v1")

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
