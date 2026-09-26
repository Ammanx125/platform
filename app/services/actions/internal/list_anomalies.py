# app/services/actions/internal/list_anomalies.py
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.actions.base import (
    RISK_READ,
    ActionContext,
    ActionResult,
    ValidationOutcome,
    VerificationOutcome,
)
from app.services.actions.registry import register
from app.services.analytics import anomalies as anomalies_service


class ListAnomaliesParams(BaseModel):
    severity: str | None = Field(
        default=None,
        description="Filter by severity: info | warning | error | critical",
    )
    limit: int = Field(default=20, ge=1, le=100)


def validate_anomaly_filter(
    payload: ListAnomaliesParams,
    context: ActionContext,
) -> ValidationOutcome:
    allowed = {"info", "warning", "error", "critical"}
    if payload.severity is not None and payload.severity not in allowed:
        return ValidationOutcome(
            passed=False,
            error=f"severity must be one of {sorted(allowed)}",
            detail={"severity": payload.severity},
        )
    return ValidationOutcome(passed=True, detail={})


class ListAnomaliesAction:
    name = "list_recent_anomalies"
    description = "List recently detected anomalies for the tenant."
    risk_level = RISK_READ
    parameters_model = ListAnomaliesParams
    validators = [validate_anomaly_filter]

    async def execute(
        self,
        *,
        db: Any,
        payload: ListAnomaliesParams,
        context: ActionContext,
    ) -> ActionResult:
        session: AsyncSession = db
        rows = await anomalies_service.list_anomalies(
            session,
            tenant_id=context.tenant_id,
            severity=payload.severity,
            limit=payload.limit,
        )
        return ActionResult(output={
            "count": len(rows),
            "anomalies": [
                {
                    "id": str(a.id),
                    "detector_key": a.detector_key,
                    "group_label": a.group_label,
                    "value": a.value,
                    "severity": a.severity,
                    "point_timestamp": a.point_timestamp.isoformat(),
                }
                for a in rows
            ],
        })

    async def verify(
        self,
        *,
        db: Any,
        payload: ListAnomaliesParams,
        context: ActionContext,
        result: ActionResult,
    ) -> VerificationOutcome:
        """Read-only tools produce no side effect; verification is trivial."""
        return VerificationOutcome(verified=True, detail={"readonly": True})


register(ListAnomaliesAction())