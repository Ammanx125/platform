# app/services/ingestion/csv.py
from __future__ import annotations

import csv
import io
from typing import Any

from app.services.ingestion.base import (
    IngestionError,
    IngestionResult,
    ParsedRow,
    SourceMetadata,
)
from app.services.storage.local import storage


class CSVConnector:
    source_type = "csv"

    async def _read_text(self, *, path: str) -> str:
        raw = await storage.get(key=path)
        # Try utf-8-sig first (handles BOM from Excel), fall back to latin-1.
        for encoding in ("utf-8-sig", "utf-8", "latin-1"):
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                continue
        raise IngestionError("could not decode CSV file with supported encodings")

    async def inspect(self, *, path: str) -> SourceMetadata:
        text = await self._read_text(path=path)
        reader = csv.reader(io.StringIO(text))
        try:
            header = next(reader)
        except StopIteration as exc:
            raise IngestionError("CSV file is empty") from exc

        sample_rows: list[dict[str, Any]] = []
        for _ in range(5):
            try:
                row = next(reader)
            except StopIteration:
                break
            sample_rows.append(dict(zip(header, row, strict=False)))

        return SourceMetadata(
            columns=header,
            sample_rows=sample_rows,
            encoding="utf-8-sig",
        )

    async def ingest(self, *, path: str) -> IngestionResult:
        text = await self._read_text(path=path)
        reader = csv.reader(io.StringIO(text))
        try:
            header = next(reader)
        except StopIteration as exc:
            raise IngestionError("CSV file is empty") from exc

        rows: list[ParsedRow] = []
        errors: list[dict[str, Any]] = []

        for idx, row in enumerate(reader, start=2):
            if len(row) != len(header):
                errors.append({
                    "row": idx,
                    "error": "column count mismatch",
                    "expected": len(header),
                    "got": len(row),
                })
                continue
            rows.append(ParsedRow(row_number=idx, data=dict(zip(header, row, strict=True))))

        return IngestionResult(
            rows=rows,
            metadata=SourceMetadata(columns=header),
            errors=errors,
        )