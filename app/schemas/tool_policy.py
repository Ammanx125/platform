# app/schemas/tool_policy.py
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class ToolPolicyRead(BaseModel):
    id: uuid.UUID
    tool_name: str
    policy_override: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ToolPolicySet(BaseModel):
    policy_override: Literal["auto", "require_approval", "disabled"]