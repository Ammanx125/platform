# app/web/routes/overview.py
from __future__ import annotations

import math
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.action import ActionRecord
from app.db.models.agent import Agent
from app.db.models.analytics import Anomaly, Forecast
from app.db.models.dataset import DataSource, IngestionJob
from app.db.models.decision import DecisionRun
from app.db.models.semantic import CanonicalConcept, SemanticMapping
from app.db.models.tenant import Tenant
from app.db.models.timestamps import FileObservation
from app.db.session import get_db
from app.services.agents.classifier import classify_observations
from app.services.analytics import customer_behavior as customer_behavior_service
from app.services.analytics import forecasting as forecasting_service
from app.services.analytics import kpi as kpi_service
from app.web.deps import HtmlUser
from app.web.templating import base_context, templates

router = APIRouter()

_DEMO_KPI_ORDER = (
    "sales.total_revenue",
    "operations.average_downtime",
    "transport.average_fuel_use_rate",
)
_DEMO_KPI_CONTEXT = {
    "sales.total_revenue": "Ticket fares and customer accounts · total in source currency",
    "operations.average_downtime": "Central Depot · average hours per week",
    "transport.average_fuel_use_rate": "Bus-17 · litres per 100 kilometres",
}
_DEMO_KPI_UNITS = {
    "transport.average_fuel_use_rate": "L/100 km",
}
_ANOMALY_TITLES = {
    "procurement.purchase_price_spikes": "A supplier price was unusually high",
    "inventory.stock_level_drops": "Product stock dropped unusually",
    "sales.revenue_swings": "Customer revenue changed unusually",
    "operations.downtime_spikes": "Depot downtime was unusually high",
    "transport.fuel_use_rate_spikes": "Bus fuel use was higher than usual",
    "transport.supplier_delay_spikes": "A supplier delivery took longer than usual",
}
_ANOMALY_UNITS = {
    "procurement.purchase_price_spikes": "currency units",
    "inventory.stock_level_drops": "items",
    "sales.revenue_swings": "currency units",
    "operations.downtime_spikes": "hours",
    "transport.fuel_use_rate_spikes": "L/100 km",
    "transport.supplier_delay_spikes": "days",
}
_FORECAST_PRESENTATION = {
    "Transport.PassengerCount": ("Weekly passengers", "passengers"),
    "Transport.FuelUseRate": ("Fuel use per 100 km", "L/100 km"),
    "Finance.Revenue": ("Revenue", "currency units"),
}


def _forecast_plot_points(forecast: Forecast) -> list[str]:
    values = [
        float(point["value"])
        for point in forecast.predicted_points
        if math.isfinite(float(point["value"]))
    ]
    if not values:
        return []

    width = 720
    top, bottom = 12.0, 156.0
    minimum, maximum = min(values), max(values)
    spread = maximum - minimum or 1.0
    denominator = max(len(values) - 1, 1)
    return [
        f"{index * width / denominator:.1f},"
        f"{bottom - (value - minimum) * (bottom - top) / spread:.1f}"
        for index, value in enumerate(values)
    ]


@router.get("/", response_class=HTMLResponse)
async def overview(
    request: Request,
    user: HtmlUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    tenant_id = user.tenant_id
    tenant = (
        await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    ).scalar_one_or_none()
    tenant_name = tenant.name if tenant else str(tenant_id)

    demo_source = (
        await db.execute(
            select(DataSource).where(
                DataSource.tenant_id == tenant_id,
                DataSource.name == "Meridian Watcher Files",
                DataSource.source_type == "agent",
            )
        )
    ).scalar_one_or_none()
    demo_agent = None
    demo_files = []
    demo_ready_file_count = 0
    demo_observed_file_count = 0
    demo_ingested_row_count = 0
    demo_file_total = 0
    demo_queue_file_total = 0
    demo_file_completed_count = 0
    demo_file_failed_count = 0
    demo_has_ingestion_jobs = False
    if demo_source is not None:
        demo_agent = (
            await db.execute(
                select(Agent)
                .where(
                    Agent.tenant_id == tenant_id,
                    Agent.source_id == demo_source.id,
                )
                .order_by(Agent.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        file_names = demo_source.config.get("demo_files", [])
        demo_file_total = len(file_names)
        observations = (
            await db.execute(
                select(FileObservation)
                .where(
                    FileObservation.tenant_id == tenant_id,
                    FileObservation.source_id == demo_source.id,
                    FileObservation.status != "deleted",
                )
                .order_by(FileObservation.last_seen_at.desc())
            )
        ).scalars().all()
        latest_observation_by_path: dict[str, FileObservation] = {}
        for observation in observations:
            latest_observation_by_path.setdefault(observation.path, observation)

        ingestion_jobs = (
            await db.execute(
                select(IngestionJob)
                .where(
                    IngestionJob.tenant_id == tenant_id,
                    IngestionJob.source_id == demo_source.id,
                )
                .order_by(IngestionJob.created_at.desc())
            )
        ).scalars().all()
        latest_job_by_file: dict[str, IngestionJob] = {}
        for job in ingestion_jobs:
            if job.pending_path is not None and job.pending_path in file_names:
                latest_job_by_file.setdefault(
                    job.pending_path, job
                )
            if job.status == "succeeded":
                demo_ingested_row_count += job.rows_staged
        demo_has_ingestion_jobs = bool(latest_job_by_file)
        demo_queue_file_total = len(latest_job_by_file)
        demo_file_completed_count = sum(
            job.status == "succeeded"
            for job in latest_job_by_file.values()
        )
        demo_file_failed_count = sum(
            job.status == "failed"
            for job in latest_job_by_file.values()
        )
        classification = await classify_observations(
            db, tenant_id=tenant_id, source_id=demo_source.id
        )
        ready_pairs = {
            (observation.path, observation.content_hash)
            for observation in classification.ready
        }
        claimed_pairs = {
            (job.pending_path, job.pending_hash)
            for job in ingestion_jobs
            if job.status in {"pending", "running", "succeeded"}
        }
        demo_ready_file_count = len(ready_pairs - claimed_pairs)
        for file_name in file_names:
            latest_observation = latest_observation_by_path.get(file_name)
            latest_job = latest_job_by_file.get(file_name)
            if latest_job and latest_job.status == "succeeded":
                file_status = f"Ingested · {latest_job.rows_staged} rows"
            elif latest_job and latest_job.status in {"pending", "running"}:
                file_status = "Upload or ingestion in progress"
            elif latest_job and latest_job.status == "failed":
                file_status = (
                    f"Ingestion failed · {latest_job.error_message or 'see job details'}"
                )
            elif latest_observation:
                file_status = "Seen by watcher · ready to ingest"
            else:
                file_status = "Waiting for watcher scan"
            demo_files.append({
                "name": file_name,
                "status": file_status,
                "job_status": latest_job.status if latest_job else None,
                "hash_prefix": (
                    latest_observation.content_hash[:12]
                    if latest_observation else None
                ),
                "observed": latest_observation is not None,
            })
        demo_observed_file_count = sum(
            1 for file in demo_files if file["observed"]
        )

    # Recent decisions
    decisions = (
        await db.execute(
            select(DecisionRun)
            .where(DecisionRun.tenant_id == tenant_id)
            .order_by(DecisionRun.created_at.desc())
            .limit(5)
        )
    ).scalars().all()

    # Pending approvals
    pending = (
        await db.execute(
            select(ActionRecord)
            .where(
                ActionRecord.tenant_id == tenant_id,
                ActionRecord.status == "pending_approval",
            )
            .order_by(ActionRecord.proposed_at.desc())
            .limit(5)
        )
    ).scalars().all()

    # Recent anomalies
    anomaly_stmt = (
        select(Anomaly)
        .where(Anomaly.tenant_id == tenant_id)
        .order_by(Anomaly.detected_at.desc())
        .limit(5)
    )
    if demo_source is not None:
        anomaly_stmt = anomaly_stmt.where(
            Anomaly.source_ids.contains([str(demo_source.id)])
        )
    anomalies = (await db.execute(anomaly_stmt)).scalars().all()
    pending_count = (
        await db.execute(
            select(func.count())
            .select_from(ActionRecord)
            .where(
                ActionRecord.tenant_id == tenant_id,
                ActionRecord.status == "pending_approval",
            )
        )
    ).scalar_one()

    # KPIs: evaluate the catalog, keep those that returned a value.
    kpi_defs = await kpi_service.list_kpis(db)
    kpi_defs_by_key = {definition.key: definition for definition in kpi_defs}
    if demo_source is not None:
        kpi_defs_to_show = [
            kpi_defs_by_key[key]
            for key in _DEMO_KPI_ORDER
            if key in kpi_defs_by_key
        ]
    else:
        kpi_defs_to_show = kpi_defs[:8]
    kpis = []
    for d in kpi_defs_to_show:
        result = await kpi_service.evaluate_kpi(
            db,
            key=d.key,
            tenant_id=tenant_id,
            source_ids=[demo_source.id] if demo_source else None,
        )
        if result.error is None and result.value is not None:
            kpis.append(result)
    demo_kpi_context = (
        _DEMO_KPI_CONTEXT if demo_source is not None else {}
    )
    demo_kpi_units = _DEMO_KPI_UNITS if demo_source is not None else {}

    saved_forecasts = await forecasting_service.list_forecasts(
        db, tenant_id=tenant_id, limit=20
    )
    latest_forecast = next(
        (
            forecast for forecast in saved_forecasts
            if forecast.status == "ok" and forecast.predicted_points
            and (
                demo_source is None
                or str(demo_source.id) in {
                    str(source_id) for source_id in forecast.source_ids
                }
            )
        ),
        None,
    )
    forecast_plot_points = (
        _forecast_plot_points(latest_forecast) if latest_forecast else []
    )
    forecast_title, forecast_unit = (
        _FORECAST_PRESENTATION.get(
            latest_forecast.value_concept,
            (
                latest_forecast.value_concept.rsplit(".", 1)[-1]
                .replace("_", " ")
                .title(),
                "",
            ),
        )
        if latest_forecast
        else ("", "")
    )

    customer_behavior = None
    customer_behavior_error = None
    try:
        customer_behavior = (
            await customer_behavior_service.analyze_tenant_customer_behavior(
                db,
                tenant_id=tenant_id,
                source_ids=[demo_source.id] if demo_source else None,
            )
        )
    except customer_behavior_service.CustomerBehaviorDataError as exc:
        customer_behavior_error = str(exc)

    customer_segment_labels = {
        "champions": "Champions",
        "loyal": "Loyal",
        "growing": "Growing",
        "declining": "Declining",
        "at_risk": "At risk",
        "inactive": "Inactive",
        "low_value": "Low value",
    }
    customer_segment_bars = []
    if customer_behavior is not None:
        customer_segment_bars = [
            {
                "key": key,
                "label": customer_segment_labels[key],
                "count": count,
                "percent": round(count / customer_behavior.customer_count * 100, 1),
            }
            for key, count in customer_behavior.segment_counts.items()
            if count
        ]
    customer_attention_profiles = (
        [
            profile for profile in customer_behavior.customers
            if profile.segment in {"at_risk", "inactive", "declining"}
        ][:4]
        if customer_behavior is not None
        else []
    )

    mapped_source_count = (
        await db.execute(
            select(func.count(func.distinct(SemanticMapping.source_id)))
            .join(DataSource, DataSource.id == SemanticMapping.source_id)
            .where(
                SemanticMapping.tenant_id == tenant_id,
                SemanticMapping.status == "confirmed",
                DataSource.is_active.is_(True),
            )
        )
    ).scalar_one()
    confirmed_mapping_count = (
        await db.execute(
            select(func.count())
            .select_from(SemanticMapping)
            .join(DataSource, DataSource.id == SemanticMapping.source_id)
            .where(
                SemanticMapping.tenant_id == tenant_id,
                SemanticMapping.status == "confirmed",
                DataSource.is_active.is_(True),
            )
        )
    ).scalar_one()
    semantic_examples = (
        await db.execute(
            select(
                DataSource.id,
                DataSource.name,
                SemanticMapping.source_column,
                CanonicalConcept.display_name,
                CanonicalConcept.domain,
            )
            .join(SemanticMapping, SemanticMapping.source_id == DataSource.id)
            .join(
                CanonicalConcept,
                CanonicalConcept.key == SemanticMapping.canonical_concept_key,
            )
            .where(
                DataSource.tenant_id == tenant_id,
                DataSource.is_active.is_(True),
                SemanticMapping.tenant_id == tenant_id,
                SemanticMapping.status == "confirmed",
            )
            .order_by(SemanticMapping.source_column, DataSource.name)
            .limit(6)
        )
    ).all()

    return templates.TemplateResponse(
        request,
        "overview.html",
        {
            **base_context(),
            "loop_stage": "decision",
            "active_nav": "overview",
            "tenant_name": tenant_name,
            "user_email": user.email,
            "user_name": user.full_name or user.email,
            "demo_source": demo_source,
            "demo_agent": demo_agent,
            "demo_files": demo_files,
            "demo_ready_file_count": demo_ready_file_count,
            "demo_observed_file_count": demo_observed_file_count,
            "demo_ingested_row_count": demo_ingested_row_count,
            "demo_file_total": demo_file_total,
            "demo_queue_file_total": demo_queue_file_total,
            "demo_file_completed_count": demo_file_completed_count,
            "demo_file_failed_count": demo_file_failed_count,
            "demo_has_ingestion_jobs": demo_has_ingestion_jobs,
            "kpis": kpis,
            "demo_kpi_context": demo_kpi_context,
            "demo_kpi_units": demo_kpi_units,
            "pending_count": pending_count,
            "pending_actions": pending,
            "recent_anomalies": anomalies,
            "anomaly_titles": _ANOMALY_TITLES,
            "anomaly_units": _ANOMALY_UNITS,
            "recent_decisions": decisions,
            "latest_forecast": latest_forecast,
            "forecast_plot_points": forecast_plot_points,
            "forecast_title": forecast_title,
            "forecast_unit": forecast_unit,
            "customer_behavior": customer_behavior,
            "customer_behavior_error": customer_behavior_error,
            "customer_segment_bars": customer_segment_bars,
            "customer_attention_profiles": customer_attention_profiles,
            "mapped_source_count": mapped_source_count,
            "confirmed_mapping_count": confirmed_mapping_count,
            "semantic_examples": semantic_examples,
        },
    )