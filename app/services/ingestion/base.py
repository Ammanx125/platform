# app/services/ingestion/base.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


class IngestionError(Exception):
    """Raised when a connector cannot proceed (bad format, missing file, ...)."""


@dataclass(frozen=True)
class SourceMetadata:
    """
    What a connector can tell you about a source *before* reading all rows.
    Used for previews and for the semantic-mapping UI in Step 5.
    """
    columns: list[str]
    sample_rows: list[dict[str, Any]] = field(default_factory=list)
    row_count_hint: int | None = None
    sheet_names: list[str] | None = None
    encoding: str | None = None
    delimiter: str | None = None


@dataclass(frozen=True)
class ParsedRow:
    """One row of parsed data, before semantic mapping."""
    row_number: int
    data: dict[str, Any]


@dataclass
class IngestionResult:
    """
    What a connector returns after reading a source.

    The service layer turns this into StagedRow rows and IngestionLineage.
    """
    rows: list[ParsedRow]
    metadata: SourceMetadata
    errors: list[dict[str, Any]] = field(default_factory=list)


class Connector(Protocol):
    source_type: str

    async def inspect(self, *, path: str) -> SourceMetadata: ...
    async def ingest(self, *, path: str) -> IngestionResult: ...