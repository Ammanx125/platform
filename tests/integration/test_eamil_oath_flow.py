# tests/integration/test_email_oauth_flow.py
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db.models.email import EmailAccount
from app.db.session import SessionLocal
from app.services.email import service as email_service
from app.services.email.provider_registry import _PROVIDERS


@pytest.mark.asyncio
async def test_full_connect_flow(two_tenants, monkeypatch):
    """
    Simulate: user starts connect -> gets authorize URL -> provider
    redirects with code + state -> callback creates EmailAccount.
    """
    tenant_id = two_tenants["tenant_a"]
    user_id = two_tenants["user_a"]

    # Force provider selection to 'fake' so nothing hits Google.
    monkeypatch.setitem(_PROVIDERS, "gmail", _PROVIDERS["fake"])

    async with SessionLocal() as db:
        source_id, url, _expires = await email_service.start_connect(
            db,
            tenant_id=tenant_id,
            user_id=user_id,
            provider_name="gmail",
            name="Test Inbox",
            source_id=None,
        )
        await db.commit()

        # Extract the state nonce from the URL (fake builds a simple one).
        from urllib.parse import parse_qs, urlsplit
        q = parse_qs(urlsplit(url).query)
        nonce = q["state"][0]

        account = await email_service.complete_connect(
            db,
            nonce=nonce,
            code="fake-code",
            provider_name="gmail",
        )
        await db.commit()

        assert account.tenant_id == tenant_id
        assert account.status == "active"
        assert account.email_address == "manager@meridian.example"
        assert account.source_id == source_id
        assert account.oauth_tokens_encrypted


@pytest.mark.asyncio
async def test_connect_requires_valid_state(two_tenants, monkeypatch):
    tenant_id = two_tenants["tenant_a"]
    user_id = two_tenants["user_a"]
    monkeypatch.setitem(_PROVIDERS, "gmail", _PROVIDERS["fake"])

    async with SessionLocal() as db:
        await email_service.start_connect(
            db,
            tenant_id=tenant_id,
            user_id=user_id,
            provider_name="gmail",
            name="Test",
            source_id=None,
        )
        await db.commit()

        with pytest.raises(email_service.EmailServiceError):
            await email_service.complete_connect(
                db,
                nonce="not-a-real-state",
                code="fake-code",
                provider_name="gmail",
            )


@pytest.mark.asyncio
async def test_reconnect_updates_existing_account(two_tenants, monkeypatch):
    tenant_id = two_tenants["tenant_a"]
    user_id = two_tenants["user_a"]
    monkeypatch.setitem(_PROVIDERS, "gmail", _PROVIDERS["fake"])

    async with SessionLocal() as db:
        source_ids = []
        for _ in range(2):
            source_id, url, _ = await email_service.start_connect(
                db, tenant_id=tenant_id, user_id=user_id,
                provider_name="gmail", name="Test", source_id=None,
            )
            source_ids.append(source_id)
            await db.commit()
            from urllib.parse import parse_qs, urlsplit
            nonce = parse_qs(urlsplit(url).query)["state"][0]
            await email_service.complete_connect(
                db, nonce=nonce, code="fake-code", provider_name="gmail",
            )
            await db.commit()

        assert source_ids[0] == source_ids[1], "reconnect must reuse its source"
        rows = (
            await db.execute(
                select(EmailAccount).where(
                    EmailAccount.tenant_id == tenant_id,
                    EmailAccount.email_address == "manager@meridian.example",
                )
            )
        ).scalars().all()
        assert len(rows) == 1, "reconnect must not create a duplicate"