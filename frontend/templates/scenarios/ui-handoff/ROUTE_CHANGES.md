# Route changes for `app/web/routes/scenarios.py`

Template-side work is complete; these are the small server changes the new templates expect.
Nothing in `engine.py`, `baseline.py`, `service.py`, schemas or the API router changes.

## 1. Imports
```python
from app.schemas.scenarios import ProfitAssumptions
from app.services.scenarios.service import ScenarioServiceError  # optional; already reachable via scenario_service
```

## 2. Bounds helper (single source of truth: engine constants)
```python
def _bounds() -> dict[str, dict[str, Decimal]]:
    return {
        "supplier_cost_change_pct": {"min": engine.SUPPLIER_COST_CHANGE_MIN, "max": engine.SUPPLIER_COST_CHANGE_MAX},
        "selling_price_change_pct": {"min": engine.SELLING_PRICE_CHANGE_MIN, "max": engine.SELLING_PRICE_CHANGE_MAX},
        "volume_change_pct": {"min": engine.VOLUME_CHANGE_MIN, "max": engine.VOLUME_CHANGE_MAX},
    }
```

## 3. Workbench page: bounds, `?from=` duplicate prefill, `?saved=1` toast
```python
@router.get("/scenarios", response_class=HTMLResponse)
async def scenarios_page(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: HtmlUser,
    from_: Annotated[uuid.UUID | None, Query(alias="from")] = None,
    saved: int = 0,
) -> HTMLResponse:
    context = await _page_context(db, tenant_id=user.tenant_id)
    prefill: dict[str, Any] = {}
    if from_ is not None:
        try:
            src = await scenario_service.get(db, tenant_id=user.tenant_id, scenario_id=from_)
        except scenario_service.ScenarioNotFound:
            src = None
        if src is not None and src.scenario_type == engine.SCENARIO_TYPE:
            prefill = {k: str(v) for k, v in (src.assumptions or {}).items()}
            prefill["source_id"] = str(src.source_id)
    context.update(
        request=request, user=user, **base_context(),
        loop_stage="scenarios", active_nav="scenarios",
        tenant_name=str(user.tenant_id), user_email=user.email,
        preview=None, error=None, form=prefill, saved=bool(saved), bounds=_bounds(),
        csrf_token=request.cookies.get(settings.csrf_cookie_name, ""),
    )
    return templates.TemplateResponse(request, "scenarios/index.html", context)
```
(`from fastapi import Query`.)

## 4. Save redirect carries the confirmation flag
```python
await db.commit()
return RedirectResponse("/scenarios?saved=1", status_code=303)
```
Leave the `BaselineUnavailable` branch redirecting to plain `/scenarios`.

## 5. New: comparison page (uses the existing `compare_scenarios` service, which already
refuses mixed sources / currencies / unsupported formats)
```python
@router.get("/scenarios/compare", response_class=HTMLResponse)
async def scenarios_compare(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: HtmlUser,
    ids: Annotated[list[uuid.UUID], Query()] = [],
) -> HTMLResponse:
    comparison, error = None, None
    if not 2 <= len(ids) <= 3 or len(set(ids)) != len(ids):
        error = "Select two or three different saved scenarios."
    else:
        try:
            comparison = await scenario_service.compare_scenarios(
                db, tenant_id=user.tenant_id, scenario_ids=ids)
        except scenario_service.ScenarioNotFound:
            error = "One of those scenarios no longer exists."
        except scenario_service.ScenarioServiceError as exc:
            error = str(exc)
    return templates.TemplateResponse(request, "scenarios/compare.html", {
        "request": request, "user": user, **base_context(),
        "loop_stage": "scenarios", "active_nav": "scenarios",
        "tenant_name": str(user.tenant_id), "user_email": user.email,
        "comparison": comparison, "error": error,
    })
```
Define it anywhere; its path does not collide with the POST `/{scenario_id}/recalculate` route.

## 6. Preview route
No change required. It already returns `_result.html` with `preview`, `error`, `missing`, `csrf_token`.
