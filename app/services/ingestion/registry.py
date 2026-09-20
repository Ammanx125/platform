# app/services/ingestion/registry.py
from __future__ import annotations

from app.services.ingestion.base import PullConnector
from app.services.ingestion.csv import CSVConnector
from app.services.ingestion.excel import ExcelConnector
from app.services.ingestion.http import HTTPConnector
from app.services.ingestion.sql import SQLConnector

_CONNECTORS: dict[str, PullConnector] = {
    "csv": CSVConnector(),
    "excel": ExcelConnector(),
    "sql": SQLConnector(),
    "http": HTTPConnector(),
}


def get_connector(source_type: str) -> PullConnector:
    try:
        return _CONNECTORS[source_type]
    except KeyError as exc:
        raise ValueError(f"no connector for source_type={source_type!r}") from exc


def known_source_types() -> list[str]:
    return sorted(_CONNECTORS.keys())