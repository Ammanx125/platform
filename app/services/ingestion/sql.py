from __future__ import annotations

import asyncio
import base64
import math
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

import sqlglot
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlglot import exp
from sqlglot.errors import ParseError

from app.core.config import settings
from app.core.secrets import resolve
from app.schemas.dataset import SQLSourceConfig
from app.services.ingestion.base import (
    IngestionError,
    IngestionResult,
    ParsedRow,
    SourceMetadata,
)

if TYPE_CHECKING:
    from app.db.models.dataset import DataSource


_FORBIDDEN_SQL_NODES = {
    "alter",
    "command",
    "create",
    "delete",
    "drop",
    "insert",
    "into",
    "lock",
    "merge",
    "truncate",
    "update",
}


def _validate_select_only(query: str, *, dialect: str | None = None) -> None:
    if not query.strip():
        raise IngestionError("SQL query must not be empty")

    try:
        statements = sqlglot.parse(query, read=dialect)
    except ParseError as exc:
        raise IngestionError("invalid SQL query") from exc

    if len(statements) != 1 or not isinstance(statements[0], exp.Query):
        raise IngestionError("only a single SELECT statement is allowed")
    if any(node.key in _FORBIDDEN_SQL_NODES for node in statements[0].walk()):
        raise IngestionError("SQL query contains a forbidden operation")


def _connection_url(value: str) -> tuple[URL, str]:
    try:
        url = make_url(value)
    except (SQLAlchemyError, ValueError) as exc:
        raise IngestionError("SQL credential is not a valid connection URL") from exc

    if url.drivername in {"postgres", "postgresql"}:
        url = url.set(drivername="postgresql+asyncpg")
    elif url.drivername == "sqlite":
        url = url.set(drivername="sqlite+aiosqlite")

    if url.drivername == "postgresql+asyncpg":
        return url, "postgres"
    if url.drivername == "sqlite+aiosqlite":
        if url.query.get("mode") != "ro" or url.query.get("uri") != "true":
            raise IngestionError(
                "SQLite SQL sources must use mode=ro&uri=true"
            )
        return url, "sqlite"
    raise IngestionError(
        "SQL sources support PostgreSQL (asyncpg) and read-only SQLite (aiosqlite)"
    )


def _json_safe(value: object) -> object:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return {"base64": base64.b64encode(value).decode("ascii")}
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return str(value)


class SQLConnector:
    source_type = "sql"

    def _source_config(self, source: DataSource) -> tuple[SQLSourceConfig, URL, str]:
        try:
            config = SQLSourceConfig.model_validate(source.config)
        except ValidationError as exc:
            raise IngestionError("invalid SQL source configuration") from exc
        url, dialect = _connection_url(resolve(config.credential_ref))
        _validate_select_only(config.query, dialect=dialect)
        return config, url, dialect

    async def _read(
        self, *, source: DataSource, limit: int, sample: bool
    ) -> tuple[list[dict[str, object]], list[str], bool]:
        config, url, dialect = self._source_config(source)
        row_limit = min(config.row_limit, settings.sql_row_limit)
        timeout = min(config.timeout_seconds, settings.sql_timeout_seconds)
        if sample:
            row_limit = min(row_limit, limit)

        engine = create_async_engine(url, pool_pre_ping=True)
        rows: list[dict[str, object]] = []
        truncated = False
        try:
            async with asyncio.timeout(timeout):
                async with engine.connect() as connection:
                    async with connection.begin():
                        if dialect == "postgres":
                            await connection.exec_driver_sql(
                                "SET TRANSACTION READ ONLY"
                            )
                            await connection.exec_driver_sql(
                                f"SET LOCAL statement_timeout = '{timeout * 1000}ms'"
                            )
                        else:
                            await connection.exec_driver_sql("PRAGMA query_only = ON")

                        result = await connection.stream(
                            text(config.query), config.parameters
                        )
                        try:
                            columns = list(result.keys())
                            batch_size = min(
                                max(1, settings.sql_stream_batch_size),
                                row_limit + 1,
                            )
                            async for batch in result.partitions(batch_size):
                                for row in batch:
                                    if len(rows) >= row_limit:
                                        truncated = True
                                        break
                                    rows.append({
                                        str(key): _json_safe(value)
                                        for key, value in row._mapping.items()
                                    })
                                if truncated:
                                    break
                        finally:
                            await result.close()
        except TimeoutError as exc:
            raise IngestionError("SQL source query timed out") from exc
        except SQLAlchemyError as exc:
            raise IngestionError("SQL source query failed") from exc
        finally:
            await engine.dispose()
        return rows, columns, truncated

    async def inspect(self, *, source: DataSource) -> SourceMetadata:
        rows, columns, _ = await self._read(source=source, limit=5, sample=True)
        return SourceMetadata(columns=columns, sample_rows=rows)

    async def ingest(
        self, *, source: DataSource, storage_key: str | None = None
    ) -> IngestionResult:
        del storage_key
        config, _, _ = self._source_config(source)
        rows, columns, truncated = await self._read(
            source=source, limit=config.row_limit, sample=False
        )
        errors = (
            [{"error": "row limit reached", "limit": min(
                config.row_limit, settings.sql_row_limit
            )}]
            if truncated
            else []
        )
        parsed_rows = [
            ParsedRow(row_number=index, data=row)
            for index, row in enumerate(rows, start=1)
        ]
        return IngestionResult(
            rows=parsed_rows,
            metadata=SourceMetadata(
                columns=columns,
                sample_rows=rows[:5],
                row_count_hint=None if truncated else len(rows),
            ),
            errors=errors,
        )
