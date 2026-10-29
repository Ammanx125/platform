# app/web/routes/scenarios.py
"""
Scenario Lab HTML routes (Jinja2 + HTMX, no build step).

The page is rendered server-side so the simulator works with JavaScript
disabled for everything except the HTMX preview/save calls. Every number
in the page comes from the same service the API uses; the HTML layer does
no arithmetic beyond formatting.
"""

from __future__ import annotations

import hmac
import uuid
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_permission
from app.core.config import settings
from app.db.session import get_db
from app.schemas.scenarios import ProfitAssumptions
from app.services.scenarios import engine
from app.services.scenarios import service as scenario_service
from app.services.scenarios.baseline import BaselineUnavailable
from app.web.deps import HtmlUser
from app.web.templating import base_context, templates

router = APIRouter()

PAGE_LIMIT = 50


async def require_scenario_csrf(request: Request) -> None:
    """Accept the standard CSRF header or the token carried by HTML forms."""
    cookie_token = request.cookies.get(settings.csrf_cookie_name)
    submitted_token = request.headers.get(settings.csrf_header_name)
    if not submitted_token:
        form = await request.form()
        token = form.get("csrf_token")
        submitted_token = token if isinstance(token, str) else None
    if (
        not cookie_token
        or not submitted_token
        or not hmac.compare_digest(cookie_token, submitted_token)
    ):
        raise HTTPException(status_code=403, detail="csrf check failed")


def _fmt_money(value: Decimal | str | None) -> str:
    """Group thousands, keep two decimals. Readability only, no rounding."""
    if value is None:
        return "(none)"
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    return f"{value:,.2f}"


def _fmt_pct(value: Decimal | str | None) -> str:
    if value is None:
        return "n/a"
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    sign = "+" if value > 0 else ""
    return f"{sign}{value}%"


templates.env.filters["money"] = _fmt_money
templates.env.filters["pct"] = _fmt_pct


async def _page_context(db: AsyncSession, *, tenant_id: uuid.UUID) -> dict[str, Any]:
    capabilities = await scenario_service.capabilities(db, tenant_id=tenant_id)
    scenarios = await scenario_service.list_scenarios(db, tenant_id=tenant_id, limit=PAGE_LIMIT)
    return {
        "capabilities": capabilities,
        "selected_source": next((c for c in capabilities if c.supported), None),
        "scenarios": [scenario_service.to_summary(s) for s in scenarios],
        "limitations": engine.default_limitations(),
    }


@router.get("/scenarios", response_class=HTMLResponse)
async def scenarios_page(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: HtmlUser,
) -> HTMLResponse:
    context = await _page_context(db, tenant_id=user.tenant_id)
    context.update(
        request=request,
        user=user,
        **base_context(),
        loop_stage="scenarios",
        active_nav="scenarios",
        tenant_name=str(user.tenant_id),
        user_email=user.email,
        preview=None,
        error=None,
        form={},
        csrf_token=request.cookies.get(settings.csrf_cookie_name, ""),
    )
    return templates.TemplateResponse(request, "scenarios/index.html", context)


@router.post("/scenarios/preview", response_class=HTMLResponse)
async def scenarios_preview(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: HtmlUser,
    source_id: Annotated[str, Form()],
    supplier_cost_change_pct: Annotated[str, Form()],
    selling_price_change_pct: Annotated[str, Form()],
    volume_change_pct: Annotated[str, Form()],
) -> HTMLResponse:
    """
    HTMX target. Returns just the result fragment. A bad input renders an
    inline explanation rather than a broken page.
    """
    error: str | None = None
    missing: list[str] = []
    preview = None
    try:
        assumptions = ProfitAssumptions(
            supplier_cost_change_pct=Decimal(supplier_cost_change_pct),
            selling_price_change_pct=Decimal(selling_price_change_pct),
            volume_change_pct=Decimal(volume_change_pct),
        )
    except ValidationError as exc:
        assumptions = None
        error = str(exc)
    except (InvalidOperation, ValueError):
        assumptions = None
        error = "Enter valid finite numbers for all three assumptions."

    if assumptions is None and error is None:
        error = "Enter valid values for all three assumptions."
    elif assumptions is not None:
        try:
            parsed_source_id = uuid.UUID(source_id)
        except ValueError:
            error = "That source identifier is not valid."
        else:
            try:
                result_document, baseline, _result = await scenario_service.calculate(
                    db,
                    tenant_id=user.tenant_id,
                    source_id=parsed_source_id,
                    assumptions=engine.ProfitAssumptions(
                        supplier_cost_change_pct=assumptions.supplier_cost_change_pct,
                        selling_price_change_pct=assumptions.selling_price_change_pct,
                        volume_change_pct=assumptions.volume_change_pct,
                    ),
                )
            except BaselineUnavailable as exc:
                error = exc.detail
                missing = list(exc.missing)
            except scenario_service.ScenarioServiceError as exc:
                error = str(exc)
            else:
                preview = {
                    "result": scenario_service.result_read(result_document),
                    "provenance": baseline.to_snapshot(),
                    "source_id": baseline.source_id,
                }

    return templates.TemplateResponse(
        request,
        "scenarios/_result.html",
        {
            "user": user,
            "preview": preview,
            "error": error,
            "missing": missing,
            "csrf_token": request.cookies.get(settings.csrf_cookie_name, ""),
            "form": {
                "source_id": source_id,
                "supplier_cost_change_pct": supplier_cost_change_pct,
                "selling_price_change_pct": selling_price_change_pct,
                "volume_change_pct": volume_change_pct,
            },
        },
    )


@router.post(
    "/scenarios/save",
    dependencies=[
        Depends(require_scenario_csrf),
        Depends(require_permission("dataset:write")),
    ],
)
async def scenarios_save(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: HtmlUser,
    name: Annotated[str, Form()],
    source_id: Annotated[str, Form()],
    supplier_cost_change_pct: Annotated[str, Form()],
    selling_price_change_pct: Annotated[str, Form()],
    volume_change_pct: Annotated[str, Form()],
) -> RedirectResponse:
    """Save round-trip: persists then redirects back to the list."""
    try:
        assumptions = ProfitAssumptions(
            supplier_cost_change_pct=Decimal(supplier_cost_change_pct),
            selling_price_change_pct=Decimal(selling_price_change_pct),
            volume_change_pct=Decimal(volume_change_pct),
        )
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except (InvalidOperation, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Enter valid finite values for all three assumptions.",
        ) from exc

    try:
        parsed_source_id = uuid.UUID(source_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="That source identifier is not valid.",
        ) from exc

    try:
        await scenario_service.save(
            db,
            tenant_id=user.tenant_id,
            user_id=user.id,
            name=name.strip() or "Untitled scenario",
            source_id=parsed_source_id,
            assumptions=engine.ProfitAssumptions(
                supplier_cost_change_pct=assumptions.supplier_cost_change_pct,
                selling_price_change_pct=assumptions.selling_price_change_pct,
                volume_change_pct=assumptions.volume_change_pct,
            ),
        )
    except BaselineUnavailable:
        # The preview already explained the failure; a stale form submit
        # just goes back to the page rather than creating a half-record.
        await db.rollback()
        return RedirectResponse("/scenarios", status_code=303)
    await db.commit()
    return RedirectResponse("/scenarios", status_code=303)


@router.post(
    "/scenarios/{scenario_id}/recalculate",
    dependencies=[
        Depends(require_scenario_csrf),
        Depends(require_permission("dataset:write")),
    ],
)
async def scenarios_recalculate(
    scenario_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: HtmlUser,
) -> RedirectResponse:
    try:
        await scenario_service.recalculate(
            db,
            tenant_id=user.tenant_id,
            scenario_id=scenario_id,
            user_id=user.id,
        )
    except (scenario_service.ScenarioNotFound, BaselineUnavailable):
        await db.commit()
    else:
        await db.commit()
    return RedirectResponse("/scenarios", status_code=303)


@router.post(
    "/scenarios/stale-scan",
    dependencies=[
        Depends(require_scenario_csrf),
        Depends(require_permission("dataset:write")),
    ],
)
async def scenarios_stale_scan(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: HtmlUser,
) -> RedirectResponse:
    await scenario_service.mark_stale_scenarios(db, tenant_id=user.tenant_id)
    await db.commit()
    return RedirectResponse("/scenarios", status_code=303)
