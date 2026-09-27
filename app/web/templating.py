# app/web/templating.py
"""
Jinja2 environment for the HTML dashboard.

Templates live in frontend/templates/, static files in frontend/static/.
Absolute paths, resolved at import time so cwd doesn't matter.
"""
from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates

from app.core.config import settings

_FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"
_TEMPLATES_DIR = _FRONTEND_DIR / "templates"

templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


def base_context() -> dict:
    """
    Context every template extends. Kept minimal; per-route handlers add
    what they need.
    """
    return {
        "app_name": settings.app_name,
        "csrf_cookie_name": settings.csrf_cookie_name,
        "csrf_header_name": settings.csrf_header_name,
    }