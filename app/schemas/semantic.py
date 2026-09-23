# app/schemas/semantic.py
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class CanonicalConceptRead(BaseModel):
    key: str
    display_name: str
    description: str | None
    domain: str
    kind: str
    value_type: str
    synonyms: list[str]

    model_config = {"from_attributes": True}


class SemanticMappingRead(BaseModel):
    id: uuid.UUID
    source_id: uuid.UUID
    source_column: str
    canonical_concept_key: str
    status: str
    confidence: float | None
    rationale: dict[str, Any]
    confirmed_by_user_id: uuid.UUID | None
    confirmed_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SemanticMappingCreate(BaseModel):
    """Manually create a mapping (human-authored, no matcher involved)."""
    source_column: str = Field(min_length=1, max_length=200)
    canonical_concept_key: str = Field(min_length=1, max_length=120)


class SemanticMappingPatch(BaseModel):
    """Confirm, reject, or reassign a mapping."""
    status: Literal["proposed", "confirmed", "rejected"] | None = None
    canonical_concept_key: str | None = Field(default=None, max_length=120)


class ProposeMappingsResponse(BaseModel):
    created: int
    mappings: list[SemanticMappingRead]


class ConceptRelationshipRead(BaseModel):
    id: uuid.UUID
    from_key: str
    to_key: str
    kind: str
    cardinality: str
    join_hint: dict[str, Any] | None
    description: str | None

    model_config = {"from_attributes": True}


class ConceptWithRelationships(BaseModel):
    concept: CanonicalConceptRead
    outgoing: list[ConceptRelationshipRead]
    incoming: list[ConceptRelationshipRead]


class ConceptNeighbor(BaseModel):
    key: str
    display_name: str
    domain: str
    kind: str
    depth: int
    via: ConceptRelationshipRead


class IndustryPackRead(BaseModel):
    key: str
    display_name: str
    description: str | None
    version: str
    concept_count: int
    relationship_count: int

    model_config = {"from_attributes": True}


class TenantIndustryPackRead(BaseModel):
    pack_key: str
    display_name: str
    version: str
    enabled_at: datetime


class InstallPackResponse(BaseModel):
    pack_key: str
    enabled_at: datetime
    already_installed: bool