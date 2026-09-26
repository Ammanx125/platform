# app/services/actions/internal/list_suppliers.py
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.semantic import SemanticMapping
from app.services.actions.base import (
    RISK_READ,
    ActionContext,
    ActionResult,
    ValidationOutcome,
)
from app.services.actions.registry import register


class ListSuppliersParams(BaseModel):
    limit: int = Field(default=20, ge=1, le=100)


def validate_supplier_limit(
    payload: ListSuppliersParams,
    context: ActionContext,
) -> ValidationOutcome:
    """
    The list can't be larger than the LLM was told is reasonable. Pydantic
    already enforces 1..100; this is the domain-level counterpart —
    anything above 50 in a UI context is a request for a report, not a
    list.
    """
    if payload.limit > 50:
        return ValidationOutcome(
            passed=False,
            error="limit exceeds 50; use generate_report for larger sets",
            detail={"limit": payload.limit},
        )
    return ValidationOutcome(passed=True, detail={"limit": payload.limit})


class ListSuppliersAction:
    name = "list_suppliers"
    description = (
        "List suppliers known to the tenant. Returns supplier names and "
        "their sources."
    )
    risk_level = RISK_READ
    parameters_model = ListSuppliersParams
    validators = [validate_supplier_limit]

    async def execute(
        self,
        *,
        db: Any,
        payload: ListSuppliersParams,
        context: ActionContext,
    ) -> ActionResult:
        session: AsyncSession = db

        # Suppliers come from columns mapped to the Procurement.Supplier
        # concept. Each distinct source column gives a set of supplier
        # names in that source's data.
        mappings = (
            await session.execute(
                select(SemanticMapping).where(
                    SemanticMapping.tenant_id == context.tenant_id,
                    SemanticMapping.canonical_concept_key == "Procurement.Supplier",
                    SemanticMapping.status == "confirmed",
                )
            )
        ).scalars().all()

        suppliers: list[dict[str, Any]] = []
        for m in mappings:
            suppliers.append({
                "source_id": str(m.source_id),
                "column": m.source_column,
            })

        suppliers = suppliers[: payload.limit]
        return ActionResult(output={
            "count": len(suppliers),
            "suppliers": suppliers,
        })


register(ListSuppliersAction())