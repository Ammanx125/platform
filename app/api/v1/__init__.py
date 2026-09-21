from fastapi import APIRouter

from app.api.v1.actions import router as actions_router
from app.api.v1.analytics import router as analytics_router
from app.api.v1.auth import router as auth_router
from app.api.v1.dashboard import router as dashboard_router
from app.api.v1.datasets import router as datasets_router
from app.api.v1.decisions import router as decisions_router
from app.api.v1.knowledge import router as knowledge_router
from app.api.v1.mappings import router as mappings_router
from app.api.v1.tenants import router as tenants_router
from app.api.v1.users import router as users_router
from app.api.v1.webhooks import router as webhooks_router
from app.api.v1.workflows import router as workflows_router

api_router = APIRouter(prefix="/api/v1")

api_router.include_router(auth_router)
api_router.include_router(datasets_router)
api_router.include_router(webhooks_router)
api_router.include_router(tenants_router)
api_router.include_router(users_router)
api_router.include_router(knowledge_router)
api_router.include_router(mappings_router)
api_router.include_router(decisions_router)
api_router.include_router(actions_router)
api_router.include_router(analytics_router)
api_router.include_router(dashboard_router)
api_router.include_router(workflows_router)
