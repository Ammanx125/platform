# app/services/orchestration/capabilities.py
"""
Capabilities the orchestrator can invoke.

Each capability is a Protocol implementation. The orchestrator dispatches
to whichever capabilities the plan selects; capabilities never call each
other and never call the LLM.

A capability returns a list of EvidenceItem on success, or raises
CapabilityError. The executor catches CapabilityError and records it in
evidence.capability_errors — one failing capability must not sink the
whole run.

Capabilities in Step 10:
  - retrieval:  hybrid document + row search
  - kpi:        evaluate one or more KPI definitions
  - anomaly:    run one or more anomaly detectors
  - forecast:   forecast one or more series

Realtime state and action proposal are deferred (Steps 17 and 11).
"""
from __future__ import annotations

import uuid
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.analytics import (
    AnomalyDetectorDefinition,
    KPIDefinition,
)
from app.services.analytics import anomalies as anomalies_service
from app.services.analytics import forecasting as forecasting_service
from app.services.analytics import kpi as kpi_service
from app.services.orchestration.context import EvidenceItem
from app.services.retrieval.base import RetrievalFilters
from app.services.retrieval.hybrid import HybridRetriever


class CapabilityError(Exception):
    """A capability could not produce evidence for a step."""


class Capability(Protocol):
    name: str

    async def run(
        self,
        *,
        db: AsyncSession,
        tenant_id: uuid.UUID,
        step_params: dict[str, Any],
        query: str,
        source_ids: list[uuid.UUID] | None,
    ) -> list[EvidenceItem]: ...


# ---------- retrieval ----------

class RetrievalCapability:
    name = "retrieval"

    async def run(
        self,
        *,
        db: AsyncSession,
        tenant_id: uuid.UUID,
        step_params: dict[str, Any],
        query: str,
        source_ids: list[uuid.UUID] | None,
    ) -> list[EvidenceItem]:
        top_k = int(step_params.get("top_k", 20))
        filters = RetrievalFilters(
            source_ids=list(source_ids) if source_ids else [],
        )
        retriever = HybridRetriever()
        try:
            result = await retriever.retrieve(
                db=db,
                tenant_id=tenant_id,
                query=query,
                top_k=top_k,
                filters=filters,
            )
        except Exception as exc:  # noqa: BLE001
            raise CapabilityError(f"retrieval failed: {exc}") from exc

        items: list[EvidenceItem] = []
        for r in result.items:
            items.append(EvidenceItem(
                kind="chunk" if r.kind == "chunk" else "row",
                id=str(r.id),
                text=r.content if isinstance(r.content, str) else str(r.content),
                data={
                    "id": str(r.id),
                    "kind": r.kind,
                    "content": r.content,
                    "metadata": r.metadata,
                    "score": r.score,
                    "score_components": r.score_components,
                },
                score=r.score,
            ))
        return items


# ---------- KPI ----------

class KPICapability:
    name = "kpi"

    async def run(
        self,
        *,
        db: AsyncSession,
        tenant_id: uuid.UUID,
        step_params: dict[str, Any],
        query: str,
        source_ids: list[uuid.UUID] | None,
    ) -> list[EvidenceItem]:
        keys = step_params.get("keys")
        if not keys:
            # No explicit KPI keys — try to infer from the query against
            # KPI display names. If nothing matches, run the full catalog.
            keys = await _infer_kpi_keys(db, query)
            if not keys:
                keys = [
                    k.key for k in (
                        await db.execute(select(KPIDefinition))
                    ).scalars().all()
                ]
        items: list[EvidenceItem] = []
        for key in keys:
            try:
                result = await kpi_service.evaluate_kpi(
                    db,
                    key=key,
                    tenant_id=tenant_id,
                    source_ids=source_ids,
                )
            except ValueError as exc:
                raise CapabilityError(f"kpi {key!r} failed: {exc}") from exc

            if result.error:
                continue
            items.append(EvidenceItem(
                kind="kpi",
                id=f"kpi:{result.key}",
                text=_render_kpi_text(result),
                data={
                    "id": f"kpi:{result.key}",
                    "key": result.key,
                    "display_name": result.display_name,
                    "domain": result.domain,
                    "unit": result.unit,
                    "value": result.value,
                    "rows_considered": result.rows_considered,
                },
            ))
        return items


def _render_kpi_text(result: Any) -> str:
    if isinstance(result.value, dict):
        top = sorted(result.value.items(), key=lambda kv: kv[1], reverse=True)[:5]
        parts = ", ".join(f"{k}: {v:g}" for k, v in top)
        return f"{result.display_name}: {parts}"
    if result.value is None:
        return f"{result.display_name}: (no value)"
    return f"{result.display_name}: {result.value:g} {result.unit or ''}".strip()


async def _infer_kpi_keys(db: AsyncSession, query: str) -> list[str]:
    """Very small name-overlap heuristic. LLM routing handles the long tail."""
    q = query.lower()
    keys: list[str] = []
    for k in (await db.execute(select(KPIDefinition))).scalars().all():
        tokens = {t for t in k.display_name.lower().replace("%", "").split() if len(t) > 2}
        if tokens and any(t in q for t in tokens):
            keys.append(k.key)
    return keys


# ---------- anomaly ----------

class AnomalyCapability:
    name = "anomaly"

    async def run(
        self,
        *,
        db: AsyncSession,
        tenant_id: uuid.UUID,
        step_params: dict[str, Any],
        query: str,
        source_ids: list[uuid.UUID] | None,
    ) -> list[EvidenceItem]:
        keys = step_params.get("keys")
        if not keys:
            keys = await _infer_detector_keys(db, query)
            if not keys:
                keys = [
                    d.key for d in (
                        await db.execute(select(AnomalyDetectorDefinition))
                    ).scalars().all()
                ]
        items: list[EvidenceItem] = []
        for key in keys:
            try:
                rows = await anomalies_service.run_detector(
                    db,
                    detector_key=key,
                    tenant_id=tenant_id,
                    source_ids=source_ids,
                )
            except ValueError:
                continue
            for a in rows[:10]:  # cap per detector
                items.append(EvidenceItem(
                    kind="anomaly",
                    id=f"anomaly:{a.id}",
                    text=(
                        f"{key} detected {a.value:g} at "
                        f"{a.point_timestamp.isoformat()} "
                        f"(group: {a.group_label or 'n/a'}, severity: {a.severity})"
                    ),
                    data={
                        "id": f"anomaly:{a.id}",
                        "detector_key": a.detector_key,
                        "group_key": a.group_key,
                        "group_label": a.group_label,
                        "point_timestamp": a.point_timestamp.isoformat(),
                        "value": a.value,
                        "score": a.score,
                        "severity": a.severity,
                    },
                    score=a.score,
                ))
        return items


async def _infer_detector_keys(db: AsyncSession, query: str) -> list[str]:
    q = query.lower()
    keys: list[str] = []
    for d in (await db.execute(select(AnomalyDetectorDefinition))).scalars().all():
        tokens = {t for t in d.display_name.lower().split() if len(t) > 3}
        if tokens and any(t in q for t in tokens):
            keys.append(d.key)
    return keys


# ---------- forecast ----------

class ForecastCapability:
    name = "forecast"

    async def run(
        self,
        *,
        db: AsyncSession,
        tenant_id: uuid.UUID,
        step_params: dict[str, Any],
        query: str,
        source_ids: list[uuid.UUID] | None,
    ) -> list[EvidenceItem]:
        specs = step_params.get("specs")
        if not specs:
            raise CapabilityError(
                "forecast capability requires explicit specs in step_params"
            )

        items: list[EvidenceItem] = []
        for spec in specs:
            value_concept = spec["value_concept"]
            group_by = spec.get("group_by_concept")
            horizon = spec.get("horizon")
            try:
                results = await forecasting_service.run_forecast_for_concept(
                    db,
                    tenant_id=tenant_id,
                    value_concept=value_concept,
                    group_by_concept=group_by,
                    horizon=horizon,
                    source_ids=source_ids,
                )
            except Exception as exc:  # noqa: BLE001
                raise CapabilityError(
                    f"forecast {value_concept!r} failed: {exc}"
                ) from exc

            for r in results:
                if r.status != "ok":
                    continue
                items.append(EvidenceItem(
                    kind="forecast",
                    id=f"forecast:{r.value_concept}:{r.group_key}",
                    text=(
                        f"Forecast for {value_concept} "
                        f"({'all' if not r.group_key else r.group_label}): "
                        f"model={r.model_used}, reliability={r.reliability}, "
                        f"horizon={r.horizon}"
                    ),
                    data={
                        "id": f"forecast:{r.value_concept}:{r.group_key}",
                        "value_concept": r.value_concept,
                        "group_key": r.group_key,
                        "group_label": r.group_label,
                        "model_used": r.model_used,
                        "reliability": r.reliability,
                        "horizon": r.horizon,
                        "predicted_points": r.predicted_points,
                    },
                ))
        return items


# ---------- registry ----------

CAPABILITIES: dict[str, Capability] = {
    "retrieval": RetrievalCapability(),
    "kpi": KPICapability(),
    "anomaly": AnomalyCapability(),
    "forecast": ForecastCapability(),
}


def get_capability(name: str) -> Capability:
    try:
        return CAPABILITIES[name]
    except KeyError as exc:
        raise CapabilityError(f"unknown capability: {name!r}") from exc