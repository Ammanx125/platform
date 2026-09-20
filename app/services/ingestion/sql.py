from __future__ import annotations

from typing import TYPE_CHECKING

import sqlglot
from sqlglot import exp

from app.services.ingestion.base import IngestionError, IngestionResult, SourceMetadata

if TYPE_CHECKING:
	from app.db.models.dataset import DataSource


def _validate_select_only(query: str) -> None:
	if not query.strip():
		raise IngestionError("SQL query must not be empty")

	try:
		statements = sqlglot.parse(query)
	except sqlglot.errors.ParseError as exc:
		raise IngestionError(f"invalid SQL query: {exc}") from exc

	if len(statements) != 1 or not isinstance(statements[0], exp.Select):
		raise IngestionError("only a single SELECT statement is allowed")


class SQLConnector:
	source_type = "sql"

	async def inspect(self, *, source: DataSource) -> SourceMetadata:
		raise IngestionError("SQL ingestion is not implemented")

	async def ingest(self, *, source: DataSource) -> IngestionResult:
		raise IngestionError("SQL ingestion is not implemented")
