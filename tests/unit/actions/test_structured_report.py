from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.schemas.report_structured import StructuredReport
from app.services.actions.base import ActionContext
from app.services.actions.internal.generate_structured_report import (
    GenerateStructuredReportAction,
    GenerateStructuredReportParams,
    _validate_has_evidence,
)


def _context(evidence_ids: list[str] | None = None) -> ActionContext:
    return ActionContext(
        tenant_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        decision_run_id=uuid.uuid4(),
        evidence_ids=evidence_ids or [],
    )


def _payload() -> GenerateStructuredReportParams:
    return GenerateStructuredReportParams(
        report=StructuredReport.model_validate(
            {
                "title": "Revenue report",
                "sections": [
                    {
                        "section_type": "summary",
                        "title": "Summary",
                        "insights": [
                            {
                                "observation": "Revenue rose.",
                                "evidence_ids": ["chunk:1"],
                            }
                        ],
                    }
                ],
            }
        )
    )


def test_structured_report_requires_known_citations():
    payload = _payload()

    assert _validate_has_evidence(payload, _context(["chunk:1"])).passed

    uncited_payload = GenerateStructuredReportParams(
        report=StructuredReport(
            title="Report",
            sections=[
                {
                    "section_type": "summary",
                    "title": "Summary",
                    "insights": [{"observation": "Revenue rose."}],
                }
            ],
        )
    )
    missing = _validate_has_evidence(uncited_payload, _context(["chunk:1"]))
    assert not missing.passed
    assert "no evidence ids" in missing.error

    unknown = _validate_has_evidence(payload, _context(["chunk:2"]))
    assert not unknown.passed
    assert unknown.detail == {"unknown_evidence_ids": ["chunk:1"]}


@pytest.mark.asyncio
async def test_structured_report_creates_document_with_json_and_markdown(monkeypatch):
    document = SimpleNamespace(
        id=uuid.uuid4(),
        title="Revenue report",
        chunk_count=1,
    )
    create_document = AsyncMock(return_value=document)
    monkeypatch.setattr(
        "app.services.actions.internal.generate_structured_report."
        "knowledge_service.create_document",
        create_document,
    )
    payload = _payload()
    context = _context(["chunk:1"])

    result = await GenerateStructuredReportAction().execute(
        db=object(),
        payload=payload,
        context=context,
    )

    assert result.output["document_id"] == str(document.id)
    create_document.assert_awaited_once()
    metadata = create_document.await_args.kwargs["doc_metadata"]
    assert metadata["generated_by"] == "generate_structured_report"
    assert metadata["structured_report"] == payload.report.model_dump(mode="json")
    assert "## Summary" in create_document.await_args.kwargs["raw_text"]
    assert "Evidence: chunk:1" in create_document.await_args.kwargs["raw_text"]


@pytest.mark.asyncio
async def test_structured_report_verifies_tenant_document():
    document = SimpleNamespace(status="ready", chunk_count=1)
    result_proxy = SimpleNamespace(scalar_one_or_none=lambda: document)
    db = SimpleNamespace(execute=AsyncMock(return_value=result_proxy))
    payload = _payload()
    context = _context(["chunk:1"])

    outcome = await GenerateStructuredReportAction().verify(
        db=db,
        payload=payload,
        context=context,
        result=SimpleNamespace(
            output={"document_id": str(uuid.uuid4())},
        ),
    )

    assert outcome.verified
    assert outcome.detail["status"] == "ready"
