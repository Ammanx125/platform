# app/web/router.py
from __future__ import annotations

from fastapi import APIRouter

from app.web.routes import (
    approvals,
    ask,
    audit,
    auth,
    datasets,
    decisions,
    overview,
    workflows,
    email,
)

web_router = APIRouter()

web_router.include_router(auth.router)
web_router.include_router(overview.router)
web_router.include_router(ask.router)
web_router.include_router(decisions.router)
web_router.include_router(approvals.router)
web_router.include_router(datasets.router)
web_router.include_router(audit.router)
web_router.include_router(workflows.router)
web_router.include_router(email.router)