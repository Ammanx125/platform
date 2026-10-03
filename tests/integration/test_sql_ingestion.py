from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import settings
from app.db.models.dataset import DataSource
from app.services.ingestion.base import IngestionError
from app.services.ingestion.sql import SQLConnector


@pytest.mark.asyncio
async def test_sql_connector_streams_read_only_sqlite_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "source.db"
    setup_url = f"sqlite+aiosqlite:///{database_path.as_posix()}"
    setup_engine = create_async_engine(setup_url)
    async with setup_engine.begin() as connection:
        await connection.execute(
            text("CREATE TABLE suppliers (id INTEGER, name TEXT)")
        )
        await connection.execute(
            text("INSERT INTO suppliers VALUES (1, 'Acme'), (2, 'Beacon')")
        )
    await setup_engine.dispose()

    readonly_url = (
        f"sqlite+aiosqlite:///file:{database_path.as_posix()}"
        "?mode=ro&uri=true"
    )
    monkeypatch.setenv("SANSA_SECRET_TEST_SQL", readonly_url)
    source = DataSource(
        name="Test SQL source",
        source_type="sql",
        config={
            "credential_ref": "TEST_SQL",
            "query": "SELECT id, name FROM suppliers ORDER BY id",
            "row_limit": 10,
        }
    )

    connector = SQLConnector()
    preview = await connector.inspect(source=source)
    result = await connector.ingest(source=source)

    assert preview.columns == ["id", "name"]
    assert len(preview.sample_rows) == 2
    assert result.metadata.columns == ["id", "name"]
    assert [row.data for row in result.rows] == [
        {"id": 1, "name": "Acme"},
        {"id": 2, "name": "Beacon"},
    ]
    assert result.errors == []


@pytest.mark.asyncio
async def test_sql_connector_applies_configured_row_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "source.db"
    setup_engine = create_async_engine(
        f"sqlite+aiosqlite:///{database_path.as_posix()}"
    )
    async with setup_engine.begin() as connection:
        await connection.execute(text("CREATE TABLE seed (id INTEGER)"))
        await connection.execute(text("INSERT INTO seed VALUES (1), (2)"))
    await setup_engine.dispose()
    monkeypatch.setenv(
        "SANSA_SECRET_TEST_SQL",
        (
            f"sqlite+aiosqlite:///file:{database_path.as_posix()}"
            "?mode=ro&uri=true"
        ),
    )
    monkeypatch.setattr(settings, "sql_row_limit", 1)
    source = DataSource(
        name="Test SQL source",
        source_type="sql",
        config={
            "credential_ref": "TEST_SQL",
            "query": "SELECT id FROM seed ORDER BY id",
            "row_limit": 10,
        }
    )

    result = await SQLConnector().ingest(source=source)

    assert len(result.rows) == 1
    assert result.errors == [{"error": "row limit reached", "limit": 1}]
    assert result.metadata.row_count_hint is None


@pytest.mark.asyncio
async def test_sql_connector_rejects_non_read_only_sqlite_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "SANSA_SECRET_TEST_SQL",
        "sqlite+aiosqlite:///source.db",
    )
    source = DataSource(
        name="Test SQL source",
        source_type="sql",
        config={
            "credential_ref": "TEST_SQL",
            "query": "SELECT 1",
        }
    )

    with pytest.raises(IngestionError, match="mode=ro"):
        await SQLConnector().inspect(source=source)
