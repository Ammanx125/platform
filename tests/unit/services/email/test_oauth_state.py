# tests/unit/services/email/test_oauth_state.py
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.db.models.dataset import DataSource
from app.db.models.email import EmailOAuthState
from app.db.session import SessionLocal
from app.services.email.oauth_state import (
    OAuthStateError,
    consume_state,
    create_state,
    mark_failed,
)


@pytest.mark.asyncio
async def test_create_and_consume_state_one_shot(two_tenants):
    tenant_id = two_tenants["tenant_a"]
    user_id = two_tenants["user_a"]

    async with SessionLocal() as db:
        src = DataSource(tenant_id=tenant_id, name="Inbox", source_type="email_gmail")
        db.add(src)
        await db.flush()

        created = await create_state(
            db, tenant_id=tenant_id, user_id=user_id,
            source_id=src.id, provider="gmail",
            redirect_uri="http://localhost/cb",
            scopes=["gmail.readonly"],
        )
        await db.commit()

        # First consume succeeds.
        consumed = await consume_state(db, nonce=created.nonce, provider="gmail")
        await db.commit()
        assert consumed.tenant_id == tenant_id
        assert consumed.provider == "gmail"

        # Second consume fails.
        with pytest.raises(OAuthStateError):
            await consume_state(db, nonce=created.nonce, provider="gmail")


@pytest.mark.asyncio
async def test_consume_rejects_wrong_provider(two_tenants):
    tenant_id = two_tenants["tenant_a"]
    user_id = two_tenants["user_a"]
    async with SessionLocal() as db:
        src = DataSource(tenant_id=tenant_id, name="I", source_type="email_gmail")
        db.add(src)
        await db.flush()
        created = await create_state(
            db, tenant_id=tenant_id, user_id=user_id,
            source_id=src.id, provider="gmail",
            redirect_uri="x", scopes=[],
        )
        await db.commit()
        with pytest.raises(OAuthStateError):
            await consume_state(db, nonce=created.nonce, provider="graph")


@pytest.mark.asyncio
async def test_consume_rejects_expired_state(two_tenants):
    tenant_id = two_tenants["tenant_a"]
    user_id = two_tenants["user_a"]
    async with SessionLocal() as db:
        src = DataSource(tenant_id=tenant_id, name="I", source_type="email_gmail")
        db.add(src)
        await db.flush()
        created = await create_state(
            db, tenant_id=tenant_id, user_id=user_id,
            source_id=src.id, provider="gmail",
            redirect_uri="x", scopes=[],
        )
        # Force expiry.
        row = (
            await db.execute(
                select(EmailOAuthState).where(EmailOAuthState.id
                == (await db.execute(
                    select(EmailOAuthState.id).where(
                        EmailOAuthState.status == "pending"
                    ).limit(1)
                )).scalar_one())
            )
        ).scalar_one()
        row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        await db.commit()
        with pytest.raises(OAuthStateError):
            await consume_state(db, nonce=created.nonce, provider="gmail")


@pytest.mark.asyncio
async def test_mark_failed_records_error(two_tenants):
    tenant_id = two_tenants["tenant_a"]
    user_id = two_tenants["user_a"]
    async with SessionLocal() as db:
        src = DataSource(tenant_id=tenant_id, name="I", source_type="email_gmail")
        db.add(src)
        await db.flush()
        created = await create_state(
            db, tenant_id=tenant_id, user_id=user_id,
            source_id=src.id, provider="gmail",
            redirect_uri="x", scopes=[],
        )
        await db.commit()
        consumed = await consume_state(db, nonce=created.nonce, provider="gmail")
        await mark_failed(db, state_id=consumed.state_id, error="boom")
        await db.commit()
        row = (
            await db.execute(
                select(EmailOAuthState).where(EmailOAuthState.id == consumed.state_id)
            )
        ).scalar_one()
        assert row.status == "failed"
        assert row.error == "boom"