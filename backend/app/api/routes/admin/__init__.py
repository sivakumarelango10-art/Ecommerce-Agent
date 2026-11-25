from __future__ import annotations

from fastapi import APIRouter

from backend.app.api.routes.admin.ai_registry import router as ai_registry_router
from backend.app.api.routes.admin.analytics import router as analytics_router
from backend.app.api.routes.admin.audit_logs import router as audit_logs_router
from backend.app.api.routes.admin.overview import router as overview_router
from backend.app.api.routes.admin.providers import router as providers_router
from backend.app.api.routes.admin.users import router as users_router

router = APIRouter(prefix="/admin", tags=["admin"])

router.include_router(overview_router)
router.include_router(analytics_router)
router.include_router(users_router)
router.include_router(providers_router)
router.include_router(audit_logs_router)
router.include_router(ai_registry_router)
