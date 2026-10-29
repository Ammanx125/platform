"""
Scenario Lab orchestration and persistence.

The service owns tenant-scoped data resolution, durable snapshots,
staleness, and API serialization. Numerical calculations stay in engine.py.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.scenario import WhatIfScenario
from app.schemas.scenarios import (
    ProfitMetricsRead,
    ScenarioCompareResponse,
    ScenarioDeltaRead,
    ScenarioProvenanceRead,
    ScenarioRead,
    ScenarioResultRead,
    ScenarioSummaryRead,
    SourceCapabilityRead,
)
from app.services.audit import service as audit_service
from app.services.audit import types as audit_types
from app.services.scenarios import engine
from app.services.scenarios.baseline import (
    BaselineUnavailable,
    ProfitBaseline,
    is_baseline_current,
    list_candidate_sources,
    resolve_profit_baseline,
)

PERMISSION_READ = "dataset:read"
PERMISSION_WRITE = "dataset:write"


class ScenarioServiceError(Exception):
    """A scenario operation failed in a way the caller must report."""


class ScenarioNotFound(ScenarioServiceError):
    pass


def _result_document(
    result: engine.ProfitSensitivityResult,
) -> dict[str, Any]:
    """JSONB-safe result shape, with Decimal values stored as strings."""
    return {
        "scenario_type": result.scenario_type,
        "formula_version": result.formula_version,
        "currency": result.currency,
        "assumptions": {
            "supplier_cost_change_pct": str(result.assumptions.supplier_cost_change_pct),
            "selling_price_change_pct": str(result.assumptions.selling_price_change_pct),
            "volume_change_pct": str(result.assumptions.volume_change_pct),
        },
        "baseline": {
            "revenue": str(result.baseline_revenue),
            "cogs": str(result.baseline_cogs),
            "gross_profit": str(result.baseline_gross_profit),
            "gross_margin_pct": (
                str(result.baseline_gross_margin_pct)
                if result.baseline_gross_margin_pct is not None
                else None
            ),
        },
        "scenario": {
            "revenue": str(result.scenario_revenue),
            "cogs": str(result.scenario_cogs),
            "gross_profit": str(result.scenario_gross_profit),
            "gross_margin_pct": (
                str(result.scenario_gross_margin_pct)
                if result.scenario_gross_margin_pct is not None
                else None
            ),
        },
        "delta": {
            "gross_profit": str(result.gross_profit_change),
            "gross_profit_pct": (
                str(result.gross_profit_change_pct)
                if result.gross_profit_change_pct is not None
                else None
            ),
            "gross_margin_percentage_points": (
                str(result.gross_margin_change_percentage_points)
                if result.gross_margin_change_percentage_points is not None
                else None
            ),
        },
        "explanation": result.explanation,
        "limitations": list(result.limitations),
    }


def _provenance_document(baseline: ProfitBaseline) -> dict[str, Any]:
    snapshot = baseline.to_snapshot()
    snapshot["calculated_at"] = datetime.now(UTC).isoformat()
    return snapshot


async def calculate(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    source_id: uuid.UUID,
    assumptions: engine.ProfitAssumptions,
) -> tuple[dict[str, Any], ProfitBaseline, engine.ProfitSensitivityResult]:
    baseline = await resolve_profit_baseline(db, tenant_id=tenant_id, source_id=source_id)
    try:
        result = engine.simulate_profit(
            baseline=engine.ProfitBaseline(
                revenue=baseline.revenue,
                cogs=baseline.cogs,
                currency=baseline.currency,
            ),
            assumptions=assumptions,
        )
    except engine.ScenarioCalculationError as exc:
        raise ScenarioServiceError(str(exc)) from exc
    return _result_document(result), baseline, result


def _assumptions_from_scenario(scenario: WhatIfScenario) -> engine.ProfitAssumptions:
    try:
        values = scenario.assumptions
        return engine.ProfitAssumptions(
            supplier_cost_change_pct=Decimal(str(values["supplier_cost_change_pct"])),
            selling_price_change_pct=Decimal(str(values["selling_price_change_pct"])),
            volume_change_pct=Decimal(str(values["volume_change_pct"])),
        )
    except (KeyError, TypeError, ArithmeticError, ValueError) as exc:
        raise ScenarioServiceError(
            "saved scenario has unreadable assumptions; save a new scenario"
        ) from exc


async def capabilities(db: AsyncSession, *, tenant_id: uuid.UUID) -> list[SourceCapabilityRead]:
    sources = await list_candidate_sources(db, tenant_id=tenant_id)
    output: list[SourceCapabilityRead] = []
    for source in sources:
        try:
            baseline = await resolve_profit_baseline(db, tenant_id=tenant_id, source_id=source.id)
        except BaselineUnavailable as exc:
            output.append(
                SourceCapabilityRead(
                    source_id=source.id,
                    source_name=source.name,
                    source_type=source.source_type,
                    snapshot_mode=source.snapshot_mode,
                    supported=False,
                    code=exc.code,
                    detail=exc.detail,
                    missing_concepts=exc.missing,
                )
            )
            continue
        output.append(
            SourceCapabilityRead(
                source_id=baseline.source_id,
                source_name=baseline.source_name,
                source_type=source.source_type,
                snapshot_mode=baseline.snapshot_mode,
                supported=True,
                code="ready",
                detail=(
                    f"{baseline.currency} {baseline.revenue:,.2f} revenue, "
                    f"{baseline.currency} {baseline.cogs:,.2f} COGS from "
                    f"{baseline.rows_considered} complete rows"
                ),
                currency=baseline.currency,
                revenue=baseline.revenue,
                cogs=baseline.cogs,
                rows_considered=baseline.rows_considered,
                rows_skipped=0,
            )
        )
    return output


async def save(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID | None,
    name: str,
    source_id: uuid.UUID,
    assumptions: engine.ProfitAssumptions,
) -> WhatIfScenario:
    result_document, baseline, _result = await calculate(
        db,
        tenant_id=tenant_id,
        source_id=source_id,
        assumptions=assumptions,
    )
    scenario = WhatIfScenario(
        tenant_id=tenant_id,
        created_by_user_id=user_id,
        name=name,
        scenario_type=engine.SCENARIO_TYPE,
        source_id=baseline.source_id,
        status="calculated",
        assumptions={
            "supplier_cost_change_pct": str(assumptions.supplier_cost_change_pct),
            "selling_price_change_pct": str(assumptions.selling_price_change_pct),
            "volume_change_pct": str(assumptions.volume_change_pct),
        },
        baseline_snapshot=baseline.to_snapshot(),
        result=result_document,
        provenance=_provenance_document(baseline),
    )
    db.add(scenario)
    await db.flush()
    await audit_service.emit(
        db,
        tenant_id=tenant_id,
        event_type=audit_types.SCENARIO_SAVED,
        actor_user_id=user_id,
        subject_type="whatif_scenario",
        subject_id=scenario.id,
        metadata={
            "scenario_type": scenario.scenario_type,
            "source_id": str(scenario.source_id),
            "assumptions": scenario.assumptions,
        },
        message=f"profit scenario {name!r} saved",
    )
    return scenario


async def get(db: AsyncSession, *, tenant_id: uuid.UUID, scenario_id: uuid.UUID) -> WhatIfScenario:
    scenario = (
        await db.execute(
            select(WhatIfScenario).where(
                WhatIfScenario.id == scenario_id,
                WhatIfScenario.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if scenario is None:
        raise ScenarioNotFound("scenario not found")
    return scenario


async def list_scenarios(
    db: AsyncSession, *, tenant_id: uuid.UUID, limit: int = 100
) -> list[WhatIfScenario]:
    statement = (
        select(WhatIfScenario)
        .where(WhatIfScenario.tenant_id == tenant_id)
        .order_by(WhatIfScenario.created_at.desc())
        .limit(limit)
    )
    return list((await db.execute(statement)).scalars().all())


async def compare_scenarios(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    scenario_ids: list[uuid.UUID],
) -> ScenarioCompareResponse:
    scenarios = [
        await get(db, tenant_id=tenant_id, scenario_id=scenario_id) for scenario_id in scenario_ids
    ]
    if any(not to_read(scenario).supported for scenario in scenarios):
        raise ScenarioServiceError("only scenarios in the current profit format can be compared")
    source_ids = {scenario.source_id for scenario in scenarios}
    currencies = {str((scenario.result or {}).get("currency", "")) for scenario in scenarios}
    if len(source_ids) != 1:
        raise ScenarioServiceError("scenarios must use the same source to be compared")
    if len(currencies) != 1 or "" in currencies:
        raise ScenarioServiceError("scenarios must use the same known currency to be compared")
    return ScenarioCompareResponse(
        source_id=next(iter(source_ids)),
        currency=next(iter(currencies)),
        scenarios=[to_read(scenario) for scenario in scenarios],
    )


async def recalculate(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    scenario_id: uuid.UUID,
    user_id: uuid.UUID | None,
) -> WhatIfScenario:
    scenario = await get(db, tenant_id=tenant_id, scenario_id=scenario_id)
    assumptions = _assumptions_from_scenario(scenario)
    try:
        result_document, baseline, _result = await calculate(
            db,
            tenant_id=tenant_id,
            source_id=scenario.source_id,
            assumptions=assumptions,
        )
    except BaselineUnavailable as exc:
        scenario.status = "stale"
        scenario.stale_reason = exc.detail[:500]
        await db.flush()
        raise

    scenario.result = result_document
    scenario.baseline_snapshot = baseline.to_snapshot()
    scenario.provenance = _provenance_document(baseline)
    scenario.status = "calculated"
    scenario.stale_reason = None
    await db.flush()
    await audit_service.emit(
        db,
        tenant_id=tenant_id,
        event_type=audit_types.SCENARIO_RECALCULATED,
        actor_user_id=user_id,
        subject_type="whatif_scenario",
        subject_id=scenario.id,
        metadata={
            "source_id": str(scenario.source_id),
            "ingestion_job_ids": [str(job_id) for job_id in baseline.ingestion_job_ids],
        },
        message=f"profit scenario {scenario.name!r} recalculated",
    )
    return scenario


async def mark_stale_scenarios(db: AsyncSession, *, tenant_id: uuid.UUID) -> int:
    scenarios = (
        (
            await db.execute(
                select(WhatIfScenario).where(
                    WhatIfScenario.tenant_id == tenant_id,
                    WhatIfScenario.status == "calculated",
                )
            )
        )
        .scalars()
        .all()
    )
    current_by_source: dict[uuid.UUID, dict[str, Any] | None] = {}
    updated = 0
    for scenario in scenarios:
        if scenario.source_id not in current_by_source:
            try:
                baseline = await resolve_profit_baseline(
                    db, tenant_id=tenant_id, source_id=scenario.source_id
                )
                current_by_source[scenario.source_id] = baseline.to_snapshot()
            except BaselineUnavailable:
                current_by_source[scenario.source_id] = None
        current = current_by_source[scenario.source_id]
        saved = scenario.baseline_snapshot or {}
        if current is None or not is_baseline_current(
            saved_snapshot=saved, current_snapshot=current
        ):
            scenario.status = "stale"
            scenario.stale_reason = (
                "source data changed or no longer supports a verified profit baseline"
            )
            updated += 1
    if updated:
        await db.flush()
    return updated


def _decimal_or_none(value: Any) -> Decimal | None:
    return Decimal(str(value)) if value is not None else None


def result_read(document: dict[str, Any]) -> ScenarioResultRead:
    baseline = document["baseline"]
    scenario = document["scenario"]
    delta = document["delta"]
    assumptions = document["assumptions"]
    return ScenarioResultRead(
        scenario_type=document["scenario_type"],
        formula_version=int(document["formula_version"]),
        currency=document["currency"],
        assumptions=assumptions,
        baseline=ProfitMetricsRead(
            revenue=Decimal(str(baseline["revenue"])),
            cogs=Decimal(str(baseline["cogs"])),
            gross_profit=Decimal(str(baseline["gross_profit"])),
            gross_margin_pct=_decimal_or_none(baseline.get("gross_margin_pct")),
        ),
        scenario=ProfitMetricsRead(
            revenue=Decimal(str(scenario["revenue"])),
            cogs=Decimal(str(scenario["cogs"])),
            gross_profit=Decimal(str(scenario["gross_profit"])),
            gross_margin_pct=_decimal_or_none(scenario.get("gross_margin_pct")),
        ),
        delta=ScenarioDeltaRead(
            gross_profit=Decimal(str(delta["gross_profit"])),
            gross_profit_pct=_decimal_or_none(delta.get("gross_profit_pct")),
            gross_margin_percentage_points=_decimal_or_none(
                delta.get("gross_margin_percentage_points")
            ),
        ),
        explanation=document["explanation"],
        limitations=list(document["limitations"]),
    )


def provenance_read(document: dict[str, Any]) -> ScenarioProvenanceRead:
    return ScenarioProvenanceRead.model_validate(document)


def to_read(scenario: WhatIfScenario) -> ScenarioRead:
    result: ScenarioResultRead | None = None
    provenance: ScenarioProvenanceRead | None = None
    supported = scenario.scenario_type == engine.SCENARIO_TYPE
    if supported:
        try:
            result = result_read(scenario.result or {})
            provenance = provenance_read(scenario.provenance or {})
        except (ArithmeticError, KeyError, TypeError, ValueError):
            supported = False

    return ScenarioRead(
        id=scenario.id,
        name=scenario.name,
        scenario_type=scenario.scenario_type,
        source_id=scenario.source_id,
        status=scenario.status if supported else "unsupported",
        assumptions=scenario.assumptions or {},
        result=result,
        provenance=provenance,
        is_stale=scenario.status == "stale",
        stale_reason=scenario.stale_reason,
        created_by_user_id=scenario.created_by_user_id,
        created_at=scenario.created_at,
        updated_at=scenario.updated_at,
        supported=supported,
        compatibility_note=(
            None
            if supported
            else "This saved scenario uses an unsupported format. Its historical "
            "values are preserved but cannot be represented as profit metrics."
        ),
    )


def to_summary(scenario: WhatIfScenario) -> ScenarioSummaryRead:
    detail = to_read(scenario)
    result = detail.result
    return ScenarioSummaryRead(
        id=scenario.id,
        name=scenario.name,
        scenario_type=scenario.scenario_type,
        source_id=scenario.source_id,
        status=detail.status,
        currency=result.currency if result is not None else None,
        baseline_gross_profit=(result.baseline.gross_profit if result is not None else None),
        scenario_gross_profit=(result.scenario.gross_profit if result is not None else None),
        gross_profit_change=(result.delta.gross_profit if result is not None else None),
        is_stale=scenario.status == "stale",
        created_at=scenario.created_at,
        supported=detail.supported,
    )
