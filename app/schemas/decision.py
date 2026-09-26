# app/schemas/decision.py
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class DecisionRequest(BaseModel):
    query: str = Field(min_length=1)
    domain_hint: str | None = None
    source_ids: list[uuid.UUID] | None = None


class DecisionRunRead(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    query: str
    domain_hint: str | None
    source_ids: list[str]
    intent: dict[str, Any]
    plan: list[dict[str, Any]]
    evidence: dict[str, Any]
    llm_provider: str | None
    llm_model: str | None
    llm_summary: str | None
    llm_claims: list[dict[str, Any]]
    llm_recommended_actions: list[dict[str, Any]]
    llm_tool_calls: list[dict[str, Any]]
    validated_claims: list[dict[str, Any]]
    dropped_claims: list[dict[str, Any]]
    duration_ms: int
    error: str | None
    started_at: datetime
    finished_at: datetime | None

    model_config = {"from_attributes": True}