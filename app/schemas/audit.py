from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel


class AuditEventRead(BaseModel):
    id: uuid.UUID
    event_type: str
    subject_type: str | None
    subject_id: uuid.UUID | None
    actor_user_id: uuid.UUID | None
    event_metadata: dict[str, Any]
    message: str | None
    created_at: datetime

    model_config = {"from_attributes": True}
