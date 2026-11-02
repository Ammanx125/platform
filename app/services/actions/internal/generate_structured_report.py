from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.knowledge import Document
from app.schemas.report_structured import StructuredReport
from app.services.actions.base import (
    RISK_REPORT,
    ActionContext,
    ActionResult,
    ValidationOutcome,
    VerificationOutcome,
)
from app.services.actions.registry import register
from app.services.actions.validators import validate_evidence_cited
from app.services.knowledge import service as knowledge_service


class GenerateStructuredReportParams(BaseModel):
    report: StructuredReport


def _validate_has_evidence(
    payload: GenerateStructuredReportParams,
    context: ActionContext,
) -> ValidationOutcome:
    cited = {
        evidence_id
        for section in payload.report.sections
        for insight in section.insights
        for evidence_id in insight.evidence_ids
    }
    if not cited:
        return ValidationOutcome(
            passed=False,
            error="structured report cites no evidence ids in any insight",
        )

    available = set(context.evidence_ids)
    unknown = cited - available
    if unknown:
        return ValidationOutcome(
            passed=False,
            error="structured report cites evidence ids not present in the decision",
            detail={"unknown_evidence_ids": sorted(unknown)},
        )
    return ValidationOutcome(
        passed=True,
        detail={"cited_count": len(cited)},
    )


class GenerateStructuredReportAction:
    name = "generate_structured_report"
    description = (
        "Create a durable, evidence-backed structured management report as "
        "a knowledge Document. Use for multi-section reports with "
        "observations, impacts, causes, and recommendations; use "
        "generate_report for short free-form prose."
    )
    risk_level = RISK_REPORT
    parameters_model = GenerateStructuredReportParams
    validators = [validate_evidence_cited, _validate_has_evidence]

    async def execute(
        self,
        *,
        db: Any,
        payload: GenerateStructuredReportParams,
        context: ActionContext,
    ) -> ActionResult:
        report = payload.report
        document = await knowledge_service.create_document(
            db,
            tenant_id=context.tenant_id,
            title=report.title,
            content_type="markdown",
            raw_text=_render_markdown(report),
            source_id=None,
            doc_metadata={
                "generated_by": self.name,
                "decision_run_id": (
                    str(context.decision_run_id) if context.decision_run_id is not None else None
                ),
                "evidence_ids": context.evidence_ids,
                "user_id": str(context.user_id),
                "structured_report": report.model_dump(mode="json"),
            },
        )
        return ActionResult(
            output={
                "document_id": str(document.id),
                "title": document.title,
                "chunk_count": document.chunk_count,
            }
        )

    async def verify(
        self,
        *,
        db: Any,
        payload: GenerateStructuredReportParams,
        context: ActionContext,
        result: ActionResult,
    ) -> VerificationOutcome:
        document_id = result.output.get("document_id")
        if not document_id:
            return VerificationOutcome(
                verified=False,
                error="no document_id in execution result",
            )
        try:
            parsed_document_id = uuid.UUID(str(document_id))
        except ValueError:
            return VerificationOutcome(
                verified=False,
                error="invalid document_id in execution result",
            )

        session: AsyncSession = db
        document = (
            await session.execute(
                select(Document).where(
                    Document.id == parsed_document_id,
                    Document.tenant_id == context.tenant_id,
                )
            )
        ).scalar_one_or_none()
        if document is None:
            return VerificationOutcome(
                verified=False,
                error="document was not found after creation",
                detail={"document_id": str(parsed_document_id)},
            )
        if document.status not in ("ready", "processing"):
            return VerificationOutcome(
                verified=False,
                error=f"document status is {document.status!r}, expected ready or processing",
                detail={
                    "document_id": str(parsed_document_id),
                    "status": document.status,
                },
            )
        return VerificationOutcome(
            verified=True,
            detail={
                "document_id": str(parsed_document_id),
                "status": document.status,
                "chunk_count": document.chunk_count,
            },
        )


def _render_markdown(report: StructuredReport) -> str:
    lines = [f"# {report.title}", ""]
    for section in report.sections:
        lines.extend((f"## {section.title}", ""))
        if section.body:
            lines.extend((section.body, ""))
        for insight in section.insights:
            lines.append(f"- **Observation:** {insight.observation}")
            if insight.possible_causes:
                lines.append(f"  - Possible causes: {insight.possible_causes}")
            if insight.business_impact:
                lines.append(f"  - Business impact: {insight.business_impact}")
            if insight.recommendation:
                lines.append(f"  - Recommendation: {insight.recommendation}")
            if insight.evidence_ids:
                lines.append(f"  - Evidence: {', '.join(insight.evidence_ids)}")
        lines.append("")
    return "\n".join(lines)


register(GenerateStructuredReportAction())
