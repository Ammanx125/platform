# app/services/scenarios/service.py
"""
Scenario Lab orchestration.

This layer owns tenancy, persistence, staleness, and the translation
between the pure engine's dataclasses and the stored JSONB documents.
It does not own arithmetic: every number comes from engine.py.

Read-only guarantee: nothing in this module writes to StagedRow,
IngestionJob, DataSource, or any other source table. Saving a scenario
touches exactly one table, whatif_scenarios.
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
    ScenarioRead,
    ScenarioResultRead,
    ScenarioSummaryRead,
    SourceCapabilityRead,
    SpendTotalsRead,
    SupplierProjectionRead,
)
from app.services.audit import service as audit_service
from app.services.scenarios import engine
from app.services.scenarios.baseline import (
    BaselineUnavailable,
    SupplierBaseline,
    is_baseline_current,
    list_candidate_sources,
    resolve_supplier_baseline,
)

SCENARIO_TYPES = (engine.SCENARIO_TYPE,)

# Permission keys used by the API/web layers. Kept here so both agree.
PERMISSION_READ = "dataset:read"
PERMISSION_WRITE = "dataset:write"


class ScenarioServiceError(Exception):
    """A scenario operation failed in a way the caller must report."""


class ScenarioNotFound(ScenarioServiceError):
    pass


# ---------- calculation ----------


def _result_document(
    *,
    result: engine.SpendSensitivityResult,
    provenance: SupplierBaseline,
) -> dict[str, Any]:
    """
    The JSONB shape stored in ``whatif_scenarios.result`` and returned to
    callers. Values are strings (Decimal-safe) and the formula version is
    embedded so an old scenario still renders after a v2 formula ships.
    """
    return {
        "scenario_type": result.scenario_type,
        "formula_version": result.formula_version,
        "price_change_pct": str(result.price_change_pct),
        # The baseline block reports the *before* state on its own terms:
        # baseline == projected and there is no delta to show.
        "baseline": {
            "baseline_spend": str(result.baseline_spend),
            "projected_spend": str(result.baseline_spend),
            "increase": "0",
            "increase_pct": "0",
        },
        "scenario": {
            "baseline_spend": str(result.baseline_spend),
            "projected_spend": str(result.projected_spend),
            "increase": str(result.increase),
            "increase_pct": (
                str(result.increase_pct) if result.increase_pct is not None else None
            ),
        },
        "suppliers": [
            {
                "supplier_key": s.supplier_key,
                "baseline_spend": str(s.baseline_spend),
                "projected_spend": str(s.projected_spend),
                "increase": str(s.increase),
                "share_of_total_increase_pct": (
                    str(s.share_of_total_increase_pct)
                    if s.share_of_total_increase_pct is not None
                    else None
                ),
            }
            for s in result.suppliers
        ],
        "explanation": engine.explain_deterministically(result),
        "limitations": list(result.limitations),
        "source_name": provenance.source_name,
        "spend_shape": provenance.spend_shape,
    }


def _provenance_document(baseline: SupplierBaseline) -> dict[str, Any]:
    snapshot = baseline.to_snapshot()
    snapshot["calculated_at"] = datetime.now(UTC).isoformat()
    return snapshot


async def calculate(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    source_id: uuid.UUID,
    price_change_pct: Decimal,
) -> tuple[dict[str, Any], SupplierBaseline, engine.SpendSensitivityResult]:
    """
    Resolve the baseline and run the engine. Returns the result document,
    the baseline (for provenance) and the engine result (for the
    deterministic explanation).
    """
    baseline = await resolve_supplier_baseline(
        db, tenant_id=tenant_id, source_id=source_id
    )
    try:
        result = engine.simulate_supplier_spend(
            suppliers=baseline.suppliers,
            price_change_pct=price_change_pct,
        )
    except engine.ScenarioCalculationError as exc:
        raise ScenarioServiceError(str(exc)) from exc
    return _result_document(result=result, provenance=baseline), baseline, result


# ---------- capabilities ----------


async def capabilities(
    db: AsyncSession, *, tenant_id: uuid.UUID
) -> list[SourceCapabilityRead]:
    """
    Report, per active source, whether the supplier-spend scenario can run
    and exactly what is missing when it cannot. This is what lets the UI
    explain instead of failing.

    Resolving a baseline reads staged rows, so this is bounded to the
    tenant's active sources and is intended for the Scenario Lab page, not
    for high-frequency polling.
    """
    sources = await list_candidate_sources(db, tenant_id=tenant_id)
    capabilities: list[SourceCapabilityRead] = []
    for source in sources:
        try:
            baseline = await resolve_supplier_baseline(
                db, tenant_id=tenant_id, source_id=source.id
            )
        except BaselineUnavailable as exc:
            capabilities.append(
                SourceCapabilityRead(
                    source_id=source.id,
                    source_name=source.name,
                    source_type=source.source_type,
                    snapshot_mode=source.snapshot_mode,
                    supported=False,
                    code=exc.code,
                    detail=exc.detail,
                    missing_concepts=list(exc.missing),
                )
            )
            continue
        capabilities.append(
            SourceCapabilityRead(
                source_id=source.id,
                source_name=source.name,
                source_type=source.source_type,
                snapshot_mode=baseline.snapshot_mode,
                supported=True,
                code="ready",
                detail=(
                    f"{len(baseline.suppliers)} suppliers, "
                    f"{baseline.rows_considered - baseline.rows_skipped} usable rows"
                ),
                supplier_count=len(baseline.suppliers),
                total_spend=baseline.total_spend,
                rows_considered=baseline.rows_considered,
                rows_skipped=baseline.rows_skipped,
            )
        )
    return capabilities


# ---------- persistence ----------


async def save(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID | None,
    name: str,
    source_id: uuid.UUID,
    price_change_pct: Decimal,
) -> WhatIfScenario:
    result_document, baseline, _result = await calculate(
        db, tenant_id=tenant_id, source_id=source_id, price_change_pct=price_change_pct
    )
    scenario = WhatIfScenario(
        tenant_id=tenant_id,
        created_by_user_id=user_id,
        name=name,
        scenario_type=engine.SCENARIO_TYPE,
        source_id=baseline.source_id,
        status="calculated",
        assumptions={
            "supplier_price_change_pct": str(price_change_pct),
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
        event_type="scenario.saved",
        actor_user_id=user_id,
        subject_type="whatif_scenario",
        subject_id=scenario.id,
        metadata={
            "scenario_type": scenario.scenario_type,
            "source_id": str(scenario.source_id),
            "price_change_pct": str(price_change_pct),
        },
        message=f"what-if scenario {name!r} saved",
    )
    return scenario


async def get(
    db: AsyncSession, *, tenant_id: uuid.UUID, scenario_id: uuid.UUID
) -> WhatIfScenario:
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
    stmt = (
        select(WhatIfScenario)
        .where(WhatIfScenario.tenant_id == tenant_id)
        .order_by(WhatIfScenario.created_at.desc())
        .limit(limit)
    )
    return list((await db.execute(stmt)).scalars().all())


async def recalculate(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    scenario_id: uuid.UUID,
    user_id: uuid.UUID | None,
) -> WhatIfScenario:
    """
    Re-run a saved scenario against the source's *current* baseline.

    The assumptions are reused verbatim. The previous result is replaced
    only after a successful calculation; if the source can no longer
    support the scenario, the stored numbers stay and ``stale_reason``
    explains why recalculating did not happen.
    """
    scenario = await get(db, tenant_id=tenant_id, scenario_id=scenario_id)
    try:
        price_change = Decimal(str(scenario.assumptions["supplier_price_change_pct"]))
    except (KeyError, TypeError, ArithmeticError) as exc:
        raise ScenarioServiceError(
            "saved scenario has unreadable assumptions; save a new one"
        ) from exc

    try:
        result_document, baseline, _result = await calculate(
            db,
            tenant_id=tenant_id,
            source_id=scenario.source_id,
            price_change_pct=price_change,
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
        event_type="scenario.recalculated",
        actor_user_id=user_id,
        subject_type="whatif_scenario",
        subject_id=scenario.id,
        metadata={
            "source_id": str(scenario.source_id),
            "ingestion_job_ids": [str(j) for j in baseline.ingestion_job_ids],
        },
        message=f"what-if scenario {scenario.name!r} recalculated",
    )
    return scenario


# ---------- staleness ----------


async def mark_stale_scenarios(
    db: AsyncSession, *, tenant_id: uuid.UUID
) -> int:
    """
    Flag saved scenarios whose baseline is no longer current.

    A scenario goes stale when the set of ingestion jobs its baseline came
    from differs from what the source would produce now. This only changes
    ``status``/``stale_reason``; the stored numbers are never rewritten.

    Idempotent: already-stale scenarios with the same reason are skipped.
    Returns the number of rows updated.
    """
    scenarios = (
        await db.execute(
            select(WhatIfScenario).where(
                WhatIfScenario.tenant_id == tenant_id,
                WhatIfScenario.status == "calculated",
            )
        )
    ).scalars().all()

    current_by_source: dict[uuid.UUID, list[str] | None] = {}
    updated = 0
    for scenario in scenarios:
        if scenario.source_id not in current_by_source:
            try:
                baseline = await resolve_supplier_baseline(
                    db, tenant_id=tenant_id, source_id=scenario.source_id
                )
                current_by_source[scenario.source_id] = [
                    str(j) for j in baseline.ingestion_job_ids
                ]
            except BaselineUnavailable:
                current_by_source[scenario.source_id] = None

        current = current_by_source[scenario.source_id]
        saved = list((scenario.baseline_snapshot or {}).get("ingestion_job_ids") or [])
        if current is None:
            scenario.status = "stale"
            scenario.stale_reason = (
                "the source no longer supports this scenario; recalculate to "
                "see why"
            )
            updated += 1
        elif not is_baseline_current(saved_job_ids=saved, current_job_ids=current):
            scenario.status = "stale"
            scenario.stale_reason = (
                "the source's ingested data has changed since this scenario "
                "was calculated"
            )
            updated += 1
    if updated:
        await db.flush()
    return updated


# ---------- serialization ----------


def _totals(document: dict[str, Any]) -> SpendTotalsRead:
    return SpendTotalsRead(
        baseline_spend=Decimal(str(document["baseline_spend"])),
        projected_spend=Decimal(str(document["projected_spend"])),
        increase=Decimal(str(document["increase"])),
        increase_pct=(
            Decimal(str(document["increase_pct"]))
            if document.get("increase_pct") is not None
            else None
        ),
    )


def result_read(document: dict[str, Any]) -> ScenarioResultRead:
    return ScenarioResultRead(
        scenario_type=document["scenario_type"],
        formula_version=int(document["formula_version"]),
        price_change_pct=Decimal(str(document["price_change_pct"])),
        baseline=_totals(document["baseline"]),
        scenario=_totals(document["scenario"]),
        suppliers=[
            SupplierProjectionRead(
                supplier_key=s["supplier_key"],
                baseline_spend=Decimal(str(s["baseline_spend"])),
                projected_spend=Decimal(str(s["projected_spend"])),
                increase=Decimal(str(s["increase"])),
                share_of_total_increase_pct=(
                    Decimal(str(s["share_of_total_increase_pct"]))
                    if s.get("share_of_total_increase_pct") is not None
                    else None
                ),
            )
            for s in document.get("suppliers", [])
        ],
        explanation=document.get("explanation", ""),
        limitations=list(document.get("limitations", [])),
    )


def to_read(scenario: WhatIfScenario) -> ScenarioRead:
    provenance = scenario.provenance or {}
    return ScenarioRead(
        id=scenario.id,
        name=scenario.name,
        scenario_type=scenario.scenario_type,
        source_id=scenario.source_id,
        status=scenario.status,
        assumptions=scenario.assumptions or {},
        result=result_read(scenario.result),
        provenance=provenance,
        is_stale=scenario.status == "stale",
        stale_reason=scenario.stale_reason,
        created_by_user_id=scenario.created_by_user_id,
        created_at=scenario.created_at,
        updated_at=scenario.updated_at,
    )


def to_summary(scenario: WhatIfScenario) -> ScenarioSummaryRead:
    scenario_doc = (scenario.result or {}).get("scenario", {})
    return ScenarioSummaryRead(
        id=scenario.id,
        name=scenario.name,
        scenario_type=scenario.scenario_type,
        source_id=scenario.source_id,
        status=scenario.status,
        baseline_spend=Decimal(str(scenario_doc.get("baseline_spend", "0"))),
        projected_spend=Decimal(str(scenario_doc.get("projected_spend", "0"))),
        increase=Decimal(str(scenario_doc.get("increase", "0"))),
        is_stale=scenario.status == "stale",
        created_at=scenario.created_at,
    )
