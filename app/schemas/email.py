# app/schemas/email.py
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


# ---------- connect flow ----------

class EmailConnectRequest(BaseModel):
    """
    Starts an OAuth flow. The client provides a display name and a
    provider; Sansa creates (or reuses) a DataSource and returns an
    authorize URL the browser must visit.
    """
    name: str = Field(min_length=1, max_length=200)
    provider: Literal["gmail"] = "gmail"
    # When provided, bind to an existing DataSource rather than creating
    # a fresh one. Useful when re-authorizing after a token was revoked.
    source_id: uuid.UUID | None = None


class EmailConnectResponse(BaseModel):
    source_id: uuid.UUID
    authorize_url: str
    expires_at: datetime


# ---------- account listing ----------

class EmailAccountRead(BaseModel):
    id: uuid.UUID
    source_id: uuid.UUID
    provider: str
    email_address: str
    display_name: str | None
    status: str
    last_sync_at: datetime | None
    last_error: str | None
    connected_by_user_id: uuid.UUID | None
    created_at: datetime
    account_metadata: dict[str, Any]

    model_config = {"from_attributes": True}


# ---------- messages ----------

class EmailMessageRead(BaseModel):
    document_id: uuid.UUID
    message_id: str | None
    thread_id: str | None
    subject: str
    from_address: str | None
    from_name: str | None
    received_at: datetime | None
    intent: str | None
    urgency: str | None
    snippet: str | None


# ---------- sync ----------

class EmailSyncResponse(BaseModel):
    ingestion_job_id: uuid.UUID
    status: str