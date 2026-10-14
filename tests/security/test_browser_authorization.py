# tests/security/test_browser_authorization.py
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.db.models.dataset import DataSource
from app.db.session import SessionLocal
from app.schemas.browser import BrowserAuthorizationCreate
from app.services.browser import service as svc
from app.services.browser.service import BrowserServiceError


def test_origin_must_be_absolute():
    with pytest.raises(ValidationError):
        BrowserAuthorizationCreate(
            origin="erp.example.com",
            approved_purpose="x",
        )


def test_origin_rejects_path():
    with pytest.raises(ValidationError):
        BrowserAuthorizationCreate(
            origin="https://erp.example.com/admin",
            approved_purpose="x",
        )


def test_origin_normalizes_to_scheme_and_host():
    m = BrowserAuthorizationCreate(
        origin="https://erp.example.com/",
        approved_purpose="x",
    )
    assert m.origin == "https://erp.example.com"


@pytest.mark.asyncio
async def test_revoke_rejects_cross_tenant_attempt(two_tenants):
    """
    Tenant B must not be able to revoke Tenant A's authorization by
    guessing its id.
    """
    ta, ua = two_tenants["tenant_a"], two_tenants["user_a"]
    tb, ub = two_tenants["tenant_b"], two_tenants["user_b"]

    async with SessionLocal() as db:
        src = DataSource(tenant_id=ta, name="A", source_type="agent")
        db.add(src)
        await db.flush()
        auth = await svc.create_authorization(
            db, tenant_id=ta, source_id=src.id, user_id=ua,
            origin="https://erp.example.com",
            approved_purpose="x", scope={}, evidence_ref=None,
        )
        await db.commit()

        with pytest.raises(BrowserServiceError):
            await svc.revoke_authorization(
                db, tenant_id=tb, authorization_id=auth.id, user_id=ub,
            )