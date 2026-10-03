# app/core/rate_limit.py
"""
Rate limiting via slowapi, backed by Redis when configured.
"""
from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import settings


def _create_limiter(redis_url: str | None) -> Limiter:
    return Limiter(
        key_func=get_remote_address,
        default_limits=[],
        headers_enabled=True,
        storage_uri=redis_url or "memory://",
    )


limiter = _create_limiter(settings.redis_url)