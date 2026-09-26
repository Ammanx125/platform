# app/services/actions/internal/generate_report.py
"""
Generate a report as a Document.

The first write-producing internal tool. It creates a `Document` (which is
durable, auditable, and re-chunkable) rather than a freeform note. The
report content is supplied by the caller — typically the LLM, citing
evidence — and is stored under the tenant's ownership.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.actions.base import (
    RISK_REPORT,
    ActionContext,
    ActionResult,
    ValidationOutcome,
)
from app.services.actions.registry import register
from app.services.actions.validators import validate_evidence_cited
from app.services.knowledge import service as knowledge_service


class GenerateReportParams(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    body: str = Field(min_length=1, max_length=100_000)


def validate_report_shape(
    payload: GenerateReportParams,
    context: ActionContext,
) -> ValidationOutcome:
    if len(payload.body) < 20:
        return ValidationOutcome(
            passed=False,
            error="report body is too short to be useful",
            detail={"body_length": len(payload.body)},
        )
    return ValidationOutcome(passed=True, detail={"body_length": len(payload.body)})


class GenerateReportAction:
    name = "generate_report"
    description = (
        "Create a durable report as a knowledge Document. Use for "
        "summaries the user will want to revisit."
    )
    risk_level = RISK_REPORT
    parameters_model = GenerateReportParams
    validators = [validate_evidence_cited, validate_report_shape]

    async def execute(
        self,
        *,
        db: Any,
        payload: GenerateReportParams,
        context: ActionContext,
    ) -> ActionResult:
        session: AsyncSession = db
        doc = await knowledge_service.create_document(
            session,
            tenant_id=context.tenant_id,
            title=payload.title,
            content_type="markdown",
            raw_text=payload.body,
            source_id=None,
            doc_metadata={
                "generated_by": "generate_report",
                "decision_run_id": (
                    str(context.decision_run_id) if context.decision_run_id else None
                ),
                "evidence_ids": context.evidence_ids,
                "user_id": str(context.user_id),
            },
        )
        return ActionResult(output={
            "document_id": str(doc.id),
            "title": doc.title,
            "chunk_count": doc.chunk_count,
        })


register(GenerateReportAction())