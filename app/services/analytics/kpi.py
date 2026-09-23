# app/services/analytics/kpi.py
"""
KPI evaluation: the top-level entry point for computing a KPI.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.analytics import KPIDefinition
from app.services.analytics.context import EvaluationContext
from app.services.analytics.evaluator import evaluate
from app.services.analytics.operations import KPIFormulaError


@dataclass
class KPIResult:
    """
    The output of a KPI evaluation. Carries the value plus the evidence
    needed to explain it — the orchestrator (Step 10) uses `formula_trace`
    to justify the number without recomputing it.
    """
    key: str
    display_name: str
    domain: str
    unit: str | None
    value_type: str
    value: float | dict[str, float] | None
    formula_trace: dict[str, Any] = field(default_factory=dict)
    source_ids: list[uuid.UUID] = field(default_factory=list)
    rows_considered: int = 0
    computed_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    error: str | None = None


async def load_kpi(db: AsyncSession, *, key: str) -> KPIDefinition | None:
    return (
        await db.execute(select(KPIDefinition).where(KPIDefinition.key == key))
    ).scalar_one_or_none()


async def list_kpis(db: AsyncSession) -> list[KPIDefinition]:
    stmt = select(KPIDefinition).order_by(KPIDefinition.domain, KPIDefinition.key)
    return list((await db.execute(stmt)).scalars().all())


async def evaluate_kpi(
    db: AsyncSession,
    *,
    key: str,
    tenant_id: uuid.UUID,
    source_ids: list[uuid.UUID] | None = None,
) -> KPIResult:
    """
    Load a KPI definition, build the evaluation context, run the evaluator.

    Never raises for data or formula problems — those come back in
    `KPIResult.error`. Raises only for infrastructure failures (DB down).
    """
    definition = await load_kpi(db, key=key)
    if definition is None:
        raise ValueError(f"unknown KPI: {key!r}")

    try:
        ctx = await EvaluationContext.load(
            db, tenant_id=tenant_id, source_ids=source_ids
        )
    except Exception as exc:  # noqa: BLE001
        return KPIResult(
            key=definition.key,
            display_name=definition.display_name,
            domain=definition.domain,
            unit=definition.unit,
            value_type=definition.value_type,
            value=None,
            error=f"failed to load evaluation context: {exc}",
        )

    try:
        value = evaluate(definition.formula, ctx)
        return KPIResult(
            key=definition.key,
            display_name=definition.display_name,
            domain=definition.domain,
            unit=definition.unit,
            value_type=definition.value_type,
            value=value,
            formula_trace={
                "formula": definition.formula,
                "rows_considered": len(ctx._rows),  # noqa: SLF001
                "sources_used": [str(s) for s in ctx.source_ids],
            },
            source_ids=ctx.source_ids,
            rows_considered=len(ctx._rows),  # noqa: SLF001
        )
    except KPIFormulaError as exc:
        return KPIResult(
            key=definition.key,
            display_name=definition.display_name,
            domain=definition.domain,
            unit=definition.unit,
            value_type=definition.value_type,
            value=None,
            error=f"formula error: {exc}",
        )