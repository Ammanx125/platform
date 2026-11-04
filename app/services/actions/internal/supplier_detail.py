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
    Validator,
    VerificationOutcome,
)
from app.services.actions.registry import register


class SupplierDetailParams(BaseModel):
    supplier_id: uuid.UUID = Field(
        description="Identifier of the supplier, as returned by list_suppliers."
    )


def validate_supplier_reference(
    _payload: SupplierDetailParams,
    _context: ActionContext,
) -> ValidationOutcome:
    return ValidationOutcome(passed=True, detail={})


class SupplierDetailAction:
    name = "get_supplier_detail"
    description = "Return details for a single supplier by id."
    risk_level = RISK_READ
    parameters_model = SupplierDetailParams
    validators: list[Validator] = [validate_supplier_reference]

    @staticmethod
    async def execute(
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

    @staticmethod
    async def verify(
        *,
        db: Any,
        payload: SupplierDetailParams,
        context: ActionContext,
        result: ActionResult,
    ) -> VerificationOutcome:
        """Read-only tools produce no side effect; verification is trivial."""
        del db, payload, context, result
        return VerificationOutcome(verified=True, detail={"readonly": True})


register(SupplierDetailAction())