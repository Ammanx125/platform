# app/services/timestamps/constants.py
from __future__ import annotations

from enum import StrEnum


class TimeBasis(StrEnum):
    """
    The four time bases a row can carry.

    Using str mixin so the value is JSON-serializable and comparable to the
    DB string without conversion.
    """
    CONTENT = "content"
    INGESTION = "ingestion"
    FILE = "file"
    STREAM = "stream"


ALL_BASES: frozenset[str] = frozenset(b.value for b in TimeBasis)