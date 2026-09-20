# tests/unit/ingestion/test_sql_guard.py
import pytest

from app.services.ingestion.base import IngestionError
from app.services.ingestion.sql import _validate_select_only


@pytest.mark.parametrize("query", [
    "SELECT * FROM purchases",
    "SELECT a, b FROM t WHERE x = 1",
    "SELECT count(*) FROM events",
    "WITH cte AS (SELECT 1 AS x) SELECT * FROM cte",
    "select id from users",
])
def test_allows_select(query: str) -> None:
    _validate_select_only(query)  # should not raise


@pytest.mark.parametrize("query", [
    "INSERT INTO t VALUES (1)",
    "UPDATE t SET x = 1",
    "DELETE FROM t",
    "DROP TABLE t",
    "ALTER TABLE t ADD COLUMN y INT",
    "TRUNCATE t",
    "GRANT SELECT ON t TO u",
    "SELECT 1; DROP TABLE users;",       # multi-statement
    "SELECT 1; SELECT 2",                # multi-statement
    "",
])
def test_rejects_non_select(query: str) -> None:
    with pytest.raises(IngestionError):
        _validate_select_only(query)