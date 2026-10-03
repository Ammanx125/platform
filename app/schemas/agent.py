# app/schemas/agent.py
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

# --- admin-facing ---

class AgentEnrollRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    source_id: uuid.UUID


class AgentEnrollResponse(BaseModel):
    agent_id: uuid.UUID
    enrollment_token: str
    expires_in_seconds: int


class AgentRead(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    source_id: uuid.UUID
    status: str
    registered_at: datetime | None
    last_seen_at: datetime | None
    agent_metadata: dict[str, Any]
    created_at: datetime

    model_config = {"from_attributes": True}


# --- agent-facing ---

class AgentRegisterRequest(BaseModel):
    enrollment_token: str
    agent_metadata: dict[str, Any] = Field(default_factory=dict)


class AgentRegisterResponse(BaseModel):
    agent_id: uuid.UUID
    credential: str
    source_id: uuid.UUID


class AgentHeartbeatRequest(BaseModel):
    agent_metadata: dict[str, Any] = Field(default_factory=dict)


class AgentSyncFile(BaseModel):
    path: str
    content_hash: str
    byte_size: int | None = None
    mtime: str | None = None
    ctime: str | None = None
    status: str = "new"  # new | changed | unchanged | deleted


class AgentSyncRequest(BaseModel):
    files: list[AgentSyncFile] = Field(default_factory=list)


class AgentSyncResponse(BaseModel):
    counts: dict[str, int]

class AgentConfigRead(BaseModel):
    """
    Advisory configuration the agent should observe. The agent may override
    any of these locally; it reports its effective values on heartbeat.
    """
    sync_interval_seconds: int
    job_poll_interval_seconds: int
    max_upload_bytes: int
    job_batch_size: int
    watch_root_must_be_absolute: bool = True


class AgentJobRead(BaseModel):
    id: uuid.UUID
    job_type: str
    params: dict[str, Any]
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


class AgentJobResultRequest(BaseModel):
    status: str  # "completed" | "rejected" | "failed"
    result: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class AgentJobResultResponse(BaseModel):
    id: uuid.UUID
    status: str


class ClassificationRead(BaseModel):
    counts: dict[str, int]
    ready: list[dict[str, Any]]
    needs_review: list[dict[str, Any]]
    unsupported: list[dict[str, Any]]


class AgentIngestResponse(BaseModel):
    ingestion_job_id: uuid.UUID
    agent_job_count: int
    classification: dict[str, Any]