from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.action import ActionRecord
from app.db.models.knowledge import Document
from app.schemas.report_structured import StructuredReport


async def load_structured_report(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    decision_run_id: uuid.UUID,
) -> StructuredReport | None:
    """Load the newest verified structured-report document for a decision."""
    records = (
        (
            await db.execute(
                select(ActionRecord.execution_result)
                .where(
                    ActionRecord.tenant_id == tenant_id,
                    ActionRecord.decision_run_id == decision_run_id,
                    ActionRecord.tool_name == "generate_structured_report",
                    ActionRecord.status == "executed",
                )
                .order_by(ActionRecord.proposed_at.desc())
            )
        )
        .scalars()
        .all()
    )

    record_document_ids: list[uuid.UUID] = []
    for execution_result in records:
        output = (execution_result or {}).get("output") or {}
        raw_document_id = output.get("document_id")
        if raw_document_id:
            record_document_ids.append(uuid.UUID(str(raw_document_id)))
    if not record_document_ids:
        return None

    documents = (
        (
            await db.execute(
                select(Document).where(
                    Document.tenant_id == tenant_id,
                    Document.id.in_(record_document_ids),
                )
            )
        )
        .scalars()
        .all()
    )
    documents_by_id = {document.id: document for document in documents}

    for document_id in record_document_ids:
        document = documents_by_id.get(document_id)
        if document is None:
            continue
        metadata = document.doc_metadata or {}
        if metadata.get("generated_by") != "generate_structured_report":
            continue
        structured = metadata.get("structured_report")
        if structured is not None:
            return StructuredReport.model_validate(structured)
    return None
