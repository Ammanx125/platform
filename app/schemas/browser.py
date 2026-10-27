# app/schemas/browser.py
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

# ---------- agent job contract ----------

class BrowserExtractionReportSpec(BaseModel):
    """
    Declarative navigation + extraction instructions.

    NO executable code. NO arbitrary JavaScript. NO Python. The agent
    interprets this spec with its own fixed executor. Fields are
    deliberately simple: a named start page, a named report, a
    declarative filter spec, and a list of column selectors.
    """
    start_url_path: str = Field(min_length=1, max_length=500)
    report_nav: list[str] = Field(default_factory=list)
    date_range: dict[str, Any] | None = None
    column_selectors: dict[str, str] = Field(default_factory=dict)

    model_config = {"extra": "forbid"}


class BrowserExtractionPaginationSpec(BaseModel):
    """How the agent knows it has retrieved every page."""
    method: Literal["next_button", "page_numbers", "none"] = "next_button"
    max_pages: int = Field(default=1, ge=1, le=500)
    next_selector: str | None = Field(default=None, max_length=200)

    model_config = {"extra": "forbid"}


class BrowserExtractionJobParams(BaseModel):
    """
    The agent job's params for a browser_extract job.

    Server-validated at issue time; the agent re-validates before
    execution; the result endpoint re-validates the manifest against the
    specific job and profile.

    Security properties:
      - No credentials, cookies, or auth tokens. The agent uses its own
        locally-authorized browser session.
      - No executable code. report_spec is declarative only.
      - The agent's local policy is enforced independently; a server-
        issued job does not override the local allowlist.
    """
    schema_version: int = 1
    run_id: str = Field(min_length=1, max_length=128)
    profile_id: uuid.UUID
    profile_revision: int = Field(ge=1)
    source_id: uuid.UUID
    authorization_id: uuid.UUID
    approved_origin: str = Field(min_length=1, max_length=500)
    report_spec: BrowserExtractionReportSpec
    pagination_spec: BrowserExtractionPaginationSpec
    expected_columns: list[str] = Field(min_length=1)
    max_rows: int = Field(ge=1, le=1_000_000)
    max_pages: int = Field(ge=1, le=500)
    timeout_seconds: int = Field(ge=1, le=3600)
    output_format: Literal["csv"] = "csv"

    model_config = {"extra": "forbid"}


# ---------- profile ----------

class BrowserExtractionProfileCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    report_identifier: str = Field(min_length=1, max_length=500)
    authorization_id: uuid.UUID
    extraction_instructions: dict[str, Any] = Field(default_factory=dict)
    expected_columns: list[str] = Field(min_length=1)
    pagination: dict[str, Any] | None = None
    limits: dict[str, Any] = Field(default_factory=dict)

    @field_validator("expected_columns")
    @classmethod
    def _columns_non_empty_and_unique(cls, v: list[str]) -> list[str]:
        cleaned = [c.strip() for c in v]
        if any(not c for c in cleaned):
            raise ValueError("expected_columns must not contain empty strings")
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("expected_columns must be unique")
        return cleaned


class BrowserExtractionProfileRead(BaseModel):
    id: uuid.UUID
    source_id: uuid.UUID
    authorization_id: uuid.UUID
    name: str
    report_identifier: str
    revision: int
    extraction_instructions: dict[str, Any]
    expected_columns: list[str]
    pagination: dict[str, Any] | None
    limits: dict[str, Any]
    snapshot_mode: str
    active_ingestion_job_id: uuid.UUID | None
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ---------- run ----------

class BrowserExtractionRunRead(BaseModel):
    id: uuid.UUID
    profile_id: uuid.UUID
    agent_job_id: uuid.UUID
    ingestion_job_id: uuid.UUID | None
    run_id: str
    status: str
    expected_sha256: str | None
    actual_sha256: str | None
    expected_row_count: int | None
    actual_row_count: int | None
    completeness_report: dict[str, Any]
    error: str | None
    activated_at: datetime | None
    superseded_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ScheduleExtractionResponse(BaseModel):
    run: BrowserExtractionRunRead
    agent_job_id: uuid.UUID

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

# ---------- manifest (agent → server) ----------

class BrowserExtractionManifest(BaseModel):
    """
    The agent's report that it finished an extraction.

    Echoed back by the agent after executing a browser_extract job. The
    server validates this against the specific job, profile, and run
    before accepting the artifact.

    Integrity: run_id and sha256 are the two claims that matter. The
    server verifies:
      - run_id matches the run scheduled for this agent job
      - this agent job belongs to this tenant
      - the run's profile still exists and its authorization is active
      - once the CSV arrives, the actual bytes hash to sha256

    row_count and complete are claims, not evidence. The server parses
    the CSV and computes its own row_count; the completeness report in
    the run row records the discrepancy.
    """
    run_id: str = Field(min_length=1, max_length=128)
    sha256: str = Field(min_length=64, max_length=64)
    row_count: int = Field(ge=0)
    expected_columns: list[str] = Field(min_length=1)
    complete: bool
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator("sha256")
    @classmethod
    def _sha256_hex(cls, v: str) -> str:
        if not all(c in "0123456789abcdef" for c in v.lower()):
            raise ValueError("sha256 must be lowercase hex")
        return v.lower()


# ---------- activation result ----------

class BrowserActivationResult(BaseModel):
    """
    Returned by the activation helper. Describes what changed.
    """
    activated: bool
    run_id: uuid.UUID
    previous_active_run_id: uuid.UUID | None
    ingestion_job_id: uuid.UUID