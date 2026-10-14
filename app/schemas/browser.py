# app/schemas/browser.py
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator


# ---------- authorization ----------

class BrowserAuthorizationCreate(BaseModel):
    origin: str = Field(min_length=1, max_length=500)
    approved_purpose: str = Field(min_length=1, max_length=2000)
    scope: dict[str, Any] = Field(default_factory=dict)
    evidence_ref: str | None = Field(default=None, max_length=500)

    @field_validator("origin")
    @classmethod
    def _origin_must_be_absolute(cls, v: str) -> str:
        from urllib.parse import urlsplit

        parts = urlsplit(v)
        if parts.scheme not in {"http", "https"} or not parts.netloc:
            raise ValueError("origin must be an absolute http(s) URL")
        # Reject a URL with a path/query/fragment: an origin is scheme+host+port.
        if parts.path not in ("", "/") or parts.query or parts.fragment:
            raise ValueError("origin must not include a path, query, or fragment")
        return f"{parts.scheme}://{parts.netloc}"


class BrowserAuthorizationRead(BaseModel):
    id: uuid.UUID
    source_id: uuid.UUID
    authorized_by_user_id: uuid.UUID | None
    revoked_by_user_id: uuid.UUID | None
    origin: str
    approved_purpose: str
    scope: dict[str, Any]
    evidence_ref: str | None
    status: str
    revoked_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}