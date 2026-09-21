# app/core/rate_limit.py
"""
Rate limiting via slowapi.

Backend is in-process memory by default. This is fine for a single-worker
dev setup but DOES NOT work across multiple workers or hosts — each worker
has its own counter. Before production, swap to a Redis backend:

    from slowapi import Limiter
    from slowapi.util import get_remote_address
    limiter = Limiter(key_func=get_remote_address, storage_uri="redis://...")

See slowapi docs. The decorator call sites don't change.
"""
from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[],           # opt-in per-route, not global
    headers_enabled=True,        # send X-RateLimit-* headers on responses
)