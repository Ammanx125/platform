# app/api/v1/__init__.py
from fastapi import APIRouter

from app.api.v1 import auth, datasets, mappings

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(datasets.router)
api_router.include_router(mappings.router)