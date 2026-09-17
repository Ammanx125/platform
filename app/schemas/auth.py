# app/schemas/auth.py
from __future__ import annotations

import re
import uuid

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.core.exceptions import AuthError


_PASSWORD_RE = re.compile(r"^(?=.*[A-Za-z])(?=.*\d).+$")


def _validate_password(v: str) -> str:
    if len(v) < 6:
        raise ValueError("password must be at least 6 characters")
    if not _PASSWORD_RE.match(v):
        raise ValueError("password must contain at least one letter and one number")
    return v


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1)


class CreateUserRequest(BaseModel):
    email: EmailStr
    password: str
    full_name: str | None = None
    role_names: list[str] = Field(default_factory=lambda: ["viewer"])

    @field_validator("password")
    @classmethod
    def _check_password(cls, v: str) -> str:
        return _validate_password(v)


class MeResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    email: EmailStr
    full_name: str | None
    is_active: bool
    roles: list[str]
    permissions: list[str]

    model_config = {"from_attributes": True}


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str