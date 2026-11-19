from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Protocol

from fastapi import FastAPI

from backend.app.api.routes.health import router as health_router
from backend.app.api.routes.questions import router as questions_router
from backend.app.core.config import Settings, load_settings
from backend.app.core.logging import configure_logging
from backend.app.services.gemini_service import GeminiService


class CloseableService(Protocol):
    def close(self) -> None: ...


SettingsLoader = Callable[[], Settings]
ServiceFactory = Callable[[Settings], CloseableService]


def create_app(
    settings_loader: SettingsLoader = load_settings,
    service_factory: ServiceFactory = GeminiService,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        configure_logging()
        settings = settings_loader()
        service = service_factory(settings)
        app.state.gemini_service = service
        try:
            yield
        finally:
            service.close()

    application = FastAPI(
        title="Ecommerce Agent API",
        version="0.2.0",
        lifespan=lifespan,
    )
    application.include_router(health_router)
    application.include_router(questions_router, prefix="/api/v1")
    return application


app = create_app()
