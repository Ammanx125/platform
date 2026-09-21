# app/schemas/knowledge.py
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class DocumentCreate(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    content_type: Literal["text", "markdown", "html", "json"] = "text"
    raw_text: str = Field(min_length=1)
    source_id: uuid.UUID | None = None
    doc_metadata: dict[str, Any] = Field(default_factory=dict)


class DocumentRead(BaseModel):
    id: uuid.UUID
    source_id: uuid.UUID | None
    title: str
    content_type: str
    status: str
    error_message: str | None
    chunk_count: int
    token_count: int
    doc_metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ChunkRead(BaseModel):
    id: uuid.UUID
    document_id: uuid.UUID
    ordinal: int
    text: str
    token_count: int
    chunk_metadata: dict[str, Any]

    model_config = {"from_attributes": True}


class DocumentWithChunks(DocumentRead):
    chunks: list[ChunkRead]


class RetrievalFiltersIn(BaseModel):
    document_ids: list[uuid.UUID] = Field(default_factory=list)
    source_ids: list[uuid.UUID] = Field(default_factory=list)
    content_types: list[str] = Field(default_factory=list)


class SearchRequest(BaseModel):
    query: str = Field(min_length=1)
    strategy: Literal["vector", "keyword", "sql", "hybrid"] = "hybrid"
    top_k: int = Field(default=10, ge=1, le=100)
    filters: RetrievalFiltersIn = Field(default_factory=RetrievalFiltersIn)


class RetrievedItemRead(BaseModel):
    kind: str
    id: uuid.UUID
    score: float
    content: str
    metadata: dict[str, Any]
    score_components: dict[str, float]


class SearchResponse(BaseModel):
    query: str
    strategy: str
    items: list[RetrievedItemRead]