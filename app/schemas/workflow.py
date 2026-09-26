# app/schemas/workflow.py
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel


class WorkflowDefinitionRead(BaseModel):
    key: str
    display_name: str
    description: str
    domain: str
    step_count: int
    steps: list[dict[str, Any]]
    requirements: list[dict[str, Any]]
    trigger_keywords: list[str]


class WorkflowInstanceRead(BaseModel):
    id: uuid.UUID
    workflow_key: str
    status: str
    current_step: str | None
    step_states: list[dict[str, Any]]
    workflow_context: dict[str, Any]
    trigger_type: str
    trigger_metadata: dict[str, Any]
    decision_run_id: uuid.UUID | None
    pending_approval_step: str | None
    pending_action_id: uuid.UUID | None
    error: str | None
    started_at: datetime | None
    finished_at: datetime | None
    duration_ms: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class WorkflowStartRequest(BaseModel):
    source_ids: list[uuid.UUID] | None = None
    trigger_metadata: dict[str, Any] = {}


class WorkflowApproveRequest(BaseModel):
    note: str | None = None


class WorkflowRejectRequest(BaseModel):
    reason: str | None = None


class WorkflowResumeRequest(BaseModel):
    source_ids: list[uuid.UUID] | None = None