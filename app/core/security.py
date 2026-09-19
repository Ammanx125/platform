# app/core/security.py
from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from pwdlib import PasswordHash
from pwdlib.exceptions import UnknownHashError

from app.core.config import settings
from app.core.exceptions import AuthError

password_hash = PasswordHash.recommended()


# ---------- passwords ----------

def hash_password(plain: str) -> str:
    return password_hash.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return password_hash.verify(plain, hashed)
    except (TypeError, ValueError, UnknownHashError):
        return False


# ---------- access tokens (JWT) ----------

def create_access_token(*, user_id: uuid.UUID, tenant_id: uuid.UUID) -> str:
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "tid": str(tenant_id),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.access_token_minutes)).timestamp()),
        "type": "access",
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict[str, Any]:
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthError("access token expired") from exc
    except jwt.PyJWTError as exc:
        raise AuthError("invalid access token") from exc

    if payload.get("type") != "access":
        raise AuthError("wrong token type")
    return payload


# ---------- refresh tokens ----------

def generate_refresh_token() -> str:
    """Raw refresh token returned to the client (cookie)."""
    return secrets.token_urlsafe(48)


def hash_refresh_token(raw: str) -> str:
    """Only the hash is stored; a DB leak cannot be replayed."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ---------- CSRF ----------

def generate_csrf_token() -> str:
    return secrets.token_urlsafe(32)