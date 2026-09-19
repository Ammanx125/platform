# app/services/ingestion/registry.py
from __future__ import annotations

from app.services.ingestion.base import Connector
from app.services.ingestion.csv import CSVConnector
from app.services.ingestion.excel import ExcelConnector


_CONNECTORS: dict[str, Connector] = {
    "csv": CSVConnector(),
    "excel": ExcelConnector(),
}


def get_connector(source_type: str) -> Connector:
    try:
        return _CONNECTORS[source_type]
    except KeyError as exc:
        raise ValueError(f"no connector for source_type={source_type!r}") from exc


def known_source_types() -> list[str]:
    return sorted(_CONNECTORS.keys())