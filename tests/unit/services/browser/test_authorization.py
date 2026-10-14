# tests/unit/services/browser/test_authorization.py
from __future__ import annotations

import pytest

from app.db.models.browser import BrowserAuthorization
from app.db.models.dataset import DataSource
from app.db.session import SessionLocal
from app.services.browser import service as svc
from app.services.browser.service import BrowserServiceError


async def _agent_source(db, tenant_id, name="Src"):
    src = DataSource(tenant_id=tenant_id, name=name, source_type="agent")
    db.add(src)
    await db.flush()
    return src


@pytest.mark.asyncio
async def test_create_authorization_happy_path(two_tenants):
    ta, ua = two_tenants["tenant_a"], two_tenants["user_a"]
    async with SessionLocal() as db:
        src = await _agent_source(db, ta)
        auth = await svc.create_authorization(
            db,
            tenant_id=ta,
            source_id=src.id,
            user_id=ua,
            origin="https://erp.example.com",
            approved_purpose="quarterly reporting",
            scope={"reports": ["Sales"]},
            evidence_ref="ticket-1",
        )
        await db.commit()
        assert auth.status == "active"
        assert auth.authorized_by_user_id == ua


@pytest.mark.asyncio
async def test_create_authorization_rejects_non_agent_source(two_tenants):
    ta, ua = two_tenants["tenant_a"], two_tenants["user_a"]
    async with SessionLocal() as db:
        src = DataSource(tenant_id=ta, name="csv", source_type="csv")
        db.add(src)
        await db.flush()
        with pytest.raises(BrowserServiceError):
            await svc.create_authorization(
                db, tenant_id=ta, source_id=src.id, user_id=ua,
                origin="https://erp.example.com",
                approved_purpose="x", scope={}, evidence_ref=None,
            )


@pytest.mark.asyncio
async def test_create_authorization_rejects_cross_tenant_source(two_tenants):
    ta, ua = two_tenants["tenant_a"], two_tenants["user_a"]
    tb = two_tenants["tenant_b"]
    async with SessionLocal() as db:
        src_b = await _agent_source(db, tb, name="B")
        await db.commit()
        with pytest.raises(BrowserServiceError):
            await svc.create_authorization(
                db, tenant_id=ta, source_id=src_b.id, user_id=ua,
                origin="https://erp.example.com",
                approved_purpose="x", scope={}, evidence_ref=None,
            )


@pytest.mark.asyncio
async def test_create_authorization_is_idempotent(two_tenants):
    ta, ua = two_tenants["tenant_a"], two_tenants["user_a"]
    async with SessionLocal() as db:
        src = await _agent_source(db, ta)
        a1 = await svc.create_authorization(
            db, tenant_id=ta, source_id=src.id, user_id=ua,
            origin="https://erp.example.com",
            approved_purpose="x", scope={}, evidence_ref=None,
        )
        a2 = await svc.create_authorization(
            db, tenant_id=ta, source_id=src.id, user_id=ua,
            origin="https://erp.example.com",
            approved_purpose="y", scope={}, evidence_ref=None,
        )
        await db.commit()
        assert a1.id == a2.id


@pytest.mark.asyncio
async def test_revoke_is_idempotent_and_preserves_row(two_tenants):
    ta, ua = two_tenants["tenant_a"], two_tenants["user_a"]
    async with SessionLocal() as db:
        src = await _agent_source(db, ta)
        auth = await svc.create_authorization(
            db, tenant_id=ta, source_id=src.id, user_id=ua,
            origin="https://erp.example.com",
            approved_purpose="x", scope={}, evidence_ref=None,
        )
        await db.commit()

        r1 = await svc.revoke_authorization(
            db, tenant_id=ta, authorization_id=auth.id, user_id=ua,
        )
        await db.commit()
        assert r1.status == "revoked"
        assert r1.revoked_at is not None

        r2 = await svc.revoke_authorization(
            db, tenant_id=ta, authorization_id=auth.id, user_id=ua,
        )
        await db.commit()
        assert r2.id == r1.id

        still_there = (
            await db.execute(
                BrowserAuthorization.__table__.select().where(
                    BrowserAuthorization.id == auth.id
                )
            )
        ).first()
        assert still_there is not None


@pytest.mark.asyncio
async def test_get_active_authorization_respects_status(two_tenants):
    ta, ua = two_tenants["tenant_a"], two_tenants["user_a"]
    async with SessionLocal() as db:
        src = await _agent_source(db, ta)
        auth = await svc.create_authorization(
            db, tenant_id=ta, source_id=src.id, user_id=ua,
            origin="https://erp.example.com",
            approved_purpose="x", scope={}, evidence_ref=None,
        )
        await db.commit()

        active = await svc.get_active_authorization(
            db, tenant_id=ta, source_id=src.id, origin="https://erp.example.com",
        )
        assert active is not None and active.id == auth.id

        await svc.revoke_authorization(
            db, tenant_id=ta, authorization_id=auth.id, user_id=ua,
        )
        await db.commit()

        gone = await svc.get_active_authorization(
            db, tenant_id=ta, source_id=src.id, origin="https://erp.example.com",
        )
        assert gone is None