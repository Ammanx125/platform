# app/web/routes/ask.py
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.services.orchestration import executor
from app.services.orchestration.context import OrchestratorRequest
from app.web.deps import HtmlUser
from app.web.templating import templates

router = APIRouter()


@router.post("/ask", response_class=HTMLResponse)
async def ask(
    request: Request,
    user: HtmlUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    query: Annotated[str, Form()],
):
    result = await executor.execute(
        db,
        request=OrchestratorRequest(query=query),
        tenant_id=user.tenant_id,
        user_id=user.id,
    )
    row = await executor.persist(db, result=result)
    await db.commit()
    await db.refresh(row)

    return templates.TemplateResponse(
        request,
        "decisions/_answer.html",
        {
            "error": row.error,
            "decision_id": row.id,
            "summary": row.llm_summary,
            "validated_claims": row.validated_claims,
            "tool_calls": row.llm_tool_calls,
        },
    )