# app/services/analytics/anomalies.py
"""
Anomaly detection orchestration.

Loads a detector definition, builds series, runs the detector, persists
Anomaly rows. Also exposes listing/reading for the API.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.analytics import Anomaly, AnomalyDetectorDefinition
from app.services.analytics.detectors import (
    DetectedPoint,
    DetectorError,
    get_detector,
)
from app.services.analytics.series import (
    build_series,
)


async def list_detectors(db: AsyncSession) -> list[AnomalyDetectorDefinition]:
    stmt = (
        select(AnomalyDetectorDefinition)
        .order_by(
            AnomalyDetectorDefinition.domain,
            AnomalyDetectorDefinition.key,
        )
    )
    return list((await db.execute(stmt)).scalars().all())


async def load_detector(
    db: AsyncSession, *, key: str
) -> AnomalyDetectorDefinition | None:
    return (
        await db.execute(
            select(AnomalyDetectorDefinition).where(
                AnomalyDetectorDefinition.key == key
            )
        )
    ).scalar_one_or_none()
async def list_anomalies(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    detector_key: str | None = None,
    severity: str | None = None,
    group_key: str | None = None,
    since: datetime | None = None,
    limit: int = 100,
) -> list[Anomaly]:
    stmt = (
        select(Anomaly)
        .where(Anomaly.tenant_id == tenant_id)
        .order_by(Anomaly.detected_at.desc())
        .limit(limit)
    )
    if detector_key is not None:
        stmt = stmt.where(Anomaly.detector_key == detector_key)
    if severity is not None:
        stmt = stmt.where(Anomaly.severity == severity)
    if group_key is not None:
        stmt = stmt.where(Anomaly.group_key == group_key)
    if since is not None:
        stmt = stmt.where(Anomaly.detected_at >= since)
    return list((await db.execute(stmt)).scalars().all())


async def get_anomaly(
    db: AsyncSession, *, anomaly_id: uuid.UUID, tenant_id: uuid.UUID
) -> Anomaly | None:
    return (
        await db.execute(
            select(Anomaly).where(
                Anomaly.id == anomaly_id,
                Anomaly.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()




async def run_detector(
    db: AsyncSession,
    *,
    detector_key: str,
    tenant_id: uuid.UUID,
    source_ids: list[uuid.UUID] | None,
) -> list[Anomaly]:
    """
    Run one named detector for a tenant. Persists Anomaly rows and returns
    them. Idempotent via the unique constraint on (detector_key, tenant_id,
    group_key, point_timestamp).
    """
    definition = await load_detector(db, key=detector_key)
    if definition is None:
        raise ValueError(f"unknown detector: {detector_key!r}")
    if not definition.is_enabled:
        return []

    build = await build_series(
        db,
        tenant_id=tenant_id,
        source_ids=source_ids,
        value_concept=definition.value_concept,
        group_by_concept=definition.group_by_concept,
        time_basis_preference=definition.time_basis_preference,
    )

    detector_fn = get_detector(definition.detector)
    detected_at = datetime.now(UTC)
    persisted: list[Anomaly] = []

    for series in build.series:
        try:
            points = detector_fn(series, **(definition.parameters or {}))
        except DetectorError:
            # Series too short or unsuitable — skip this group, keep going.
            continue

        series_source_ids = sorted({str(p.source_id) for p in series.points})
        for dp in points:
            row = await _upsert_anomaly(
                db,
                tenant_id=tenant_id,
                definition=definition,
                series_group_key=series.group_key,
                series_group_label=series.group_label,
                time_basis_used=series.time_basis_used or "unknown",
                source_ids=series_source_ids,
                point=dp,
                detected_at=detected_at,
            )
            if row is not None:
                persisted.append(row)

    await db.flush()
    return persisted


async def run_all_detectors(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    source_ids: list[uuid.UUID] | None = None,
) -> list[Anomaly]:
    detectors = await list_detectors(db)
    all_anomalies: list[Anomaly] = []
    for d in detectors:
        if not d.is_enabled:
            continue
        try:
            rows = await run_detector(
                db,
                detector_key=d.key,
                tenant_id=tenant_id,
                source_ids=source_ids,
            )
            all_anomalies.extend(rows)
        except Exception:  # noqa: BLE001
            # A single detector failing must not stop the others.
            continue
    return all_anomalies


async def _upsert_anomaly(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    definition: AnomalyDetectorDefinition,
    series_group_key: str,
    series_group_label: str,
    time_basis_used: str,
    source_ids: list[str],
    point: DetectedPoint,
    detected_at: datetime,
) -> Anomaly | None:
    stmt = (
        pg_insert(Anomaly)
        .values({
            "id": uuid.uuid4(),
            "tenant_id": tenant_id,
            "detector_key": definition.key,
            "source_ids": source_ids,
            "group_key": series_group_key,
            "group_label": series_group_label,
            "time_basis_used": time_basis_used,
            "point_timestamp": point.timestamp,
            "value": point.value,
            "expected_min": point.expected_min,
            "expected_max": point.expected_max,
            "score": point.score,
            "severity": definition.severity,
            "detected_at": detected_at,
            "detail": point.detail,
        })
        .on_conflict_do_nothing(
            index_elements=["detector_key", "tenant_id", "group_key", "point_timestamp"]
        )
        .returning(Anomaly)
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()