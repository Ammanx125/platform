# app/services/actions/internal/supplier_detail.py
from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.dataset import DataSource
from app.services.actions.base import (
    RISK_READ,
    ActionContext,
    ActionResult,
    ValidationOutcome,
)
from app.services.actions.registry import register


class SupplierDetailParams(BaseModel):
    supplier_id: uuid.UUID = Field(
        description="Identifier of the supplier, as returned by list_suppliers."
    )


def validate_supplier_reference(
    payload: SupplierDetailParams,
    context: ActionContext,
) -> ValidationOutcome:
    """
    The supplier_id is expected to be a UUID; Pydantic handles the format.
    This validator confirms the caller isn't asking for something outside
    their tenant — but that check requires DB access, so it lives in
    execute(). What we check here is only the shape.
    """
    # The param type already enforced the format. This validator exists as
    # a named slot where a future business rule (e.g. only suppliers with
    # an active contract) can live without touching execute().
    return ValidationOutcome(passed=True, detail={})


class SupplierDetailAction:
    name = "get_supplier_detail"
    description = "Return details for a single supplier by id."
    risk_level = RISK_READ
    parameters_model = SupplierDetailParams
    validators = [validate_supplier_reference]

    async def execute(
        self,
        *,
        db: Any,
        payload: SupplierDetailParams,
        context: ActionContext,
    ) -> ActionResult:
        session: AsyncSession = db

        # The supplier_id here is interpreted as a DataSource id for now,
        # since we don't have a canonical Supplier table yet. This is the
        # same constraint as list_suppliers — the tool is honest about
        # what data it can access. When a canonical Supplier model lands
        # (Step 6+ work), this becomes a direct lookup.
        row = (
            await session.execute(
                select(DataSource).where(
                    DataSource.id == payload.supplier_id,
                    DataSource.tenant_id == context.tenant_id,
                )
            )
        ).scalar_one_or_none()

        if row is None:
            return ActionResult(output={
                "found": False,
                "supplier_id": str(payload.supplier_id),
            })

        return ActionResult(output={
            "found": True,
            "supplier_id": str(row.id),
            "name": row.name,
            "source_type": row.source_type,
        })


register(SupplierDetailAction())