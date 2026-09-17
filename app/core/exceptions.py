# app/core/exceptions.py
from __future__ import annotations


class SansaError(Exception):
    """Base class for all Sansa domain errors."""


class AuthError(SansaError):
    """Authentication failed (bad credentials, expired token, missing cookie)."""


class PermissionDenied(SansaError):
    """Authenticated principal lacks the required permission."""


class TenantIsolationError(SansaError):
    """A cross-tenant access attempt was detected. Always logged."""