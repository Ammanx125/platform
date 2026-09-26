# app/services/events/types.py
"""
Event type constants.

A str, not an enum, matching the pattern of TimeBasis and risk levels.
Event type strings are stored as-is in the DB and filtered against by
workflow triggers. Adding a new type is a code change; there is no
registration mechanism because the set of things the platform itself
emits is small and controlled.
"""
from __future__ import annotations

WEBHOOK_RECEIVED = "webhook.received"
FILE_OBSERVED = "file.observed"
INGESTION_COMPLETED = "ingestion.completed"
INGESTION_FAILED = "ingestion.failed"
DETECTOR_FIRED = "detector.fired"
FORECAST_COMPLETED = "forecast.completed"


ALL_EVENT_TYPES: frozenset[str] = frozenset({
    WEBHOOK_RECEIVED,
    FILE_OBSERVED,
    INGESTION_COMPLETED,
    INGESTION_FAILED,
    DETECTOR_FIRED,
    FORECAST_COMPLETED,
})