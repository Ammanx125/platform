# tests/integration/test_web_authorization.py
"""RBAC on the HTML surface and tenant suspension across the auth lifecycle."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.core.config import settings
from app.core.security import generate_refresh_token, hash_password, hash_refresh_token
from app.db.models.agent import Agent
from app.db.models.dataset import DataSource
from app.db.models.decision import DecisionRun
from app.db.models.refresh_token import RefreshToken
from app.db.models.tenant import Tenant
from app.db.models.user import User
from app.db.seed import seed_tenant_roles
from app.db.session import SessionLocal
from app.services.agents import service as agents_service
from app.services.agents.service import AgentError


def _csrf(client: AsyncClient) -> dict[str, str]:
    token = client.cookies.get(settings.csrf_cookie_name)
    return {settings.csrf_header_name: token} if token else {}


async def _login(client: AsyncClient, email: str, password: str) -> None:
    await client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )


async def _make_user(tenant_id, *, role: str, password: str = "Passw0rd") -> str:
    """A user in `tenant_id` with exactly one role. Returns the email."""
    async with SessionLocal() as db:
        roles = await seed_tenant_roles(db, tenant_id=tenant_id)
        email = f"{role}-{uuid.uuid4().hex[:8]}@example.com"
        user = User(
            tenant_id=tenant_id,
            email=email,
            password_hash=hash_password(password),
            is_active=True,
        )
        user.roles = [roles[role]]
        db.add(user)
        await db.commit()
        return email


async def _set_tenant_active(tenant_id, active: bool) -> None:
    async with SessionLocal() as db:
        tenant = (
            await db.execute(select(Tenant).where(Tenant.id == tenant_id))
        ).scalar_one()
        tenant.is_active = active
        await db.commit()


@pytest.mark.asyncio
async def test_browser_authorization_requires_csrf(
    client: AsyncClient, two_tenants: dict
) -> None:
    email = await _make_user(two_tenants["tenant_a"], role="analyst")
    await _login(client, email, "Passw0rd")

    async with SessionLocal() as db:
        source = DataSource(
            tenant_id=two_tenants["tenant_a"],
            name="demo-agent-source",
            source_type="agent",
        )
        db.add(source)
        await db.commit()
        source_id = source.id

    payload = {
        "origin": "https://erp.example.com",
        "approved_purpose": "customer portal access",
        "scope": {"read": True},
    }

    csrf_token = client.cookies.get(settings.csrf_cookie_name)
    client.cookies.delete(settings.csrf_cookie_name)
    no_csrf = await client.post(
        f"/api/v1/browser/sources/{source_id}/authorization",
        json=payload,
    )
    assert no_csrf.status_code == 403

    assert csrf_token is not None
    client.cookies.set(settings.csrf_cookie_name, csrf_token)
    ok = await client.post(
        f"/api/v1/browser/sources/{source_id}/authorization",
        json=payload,
        headers=_csrf(client),
    )
    assert ok.status_code == 201


@pytest.mark.asyncio
async def test_cookie_authenticated_api_writes_require_csrf_by_default(
    client: AsyncClient, two_tenants: dict
) -> None:
    from httpx import ASGITransport
    from httpx import AsyncClient as BareAsyncClient

    from app.main import app

    await _login(client, two_tenants["email_a"], "Passw0rd")
    action_id = uuid.uuid4()

    async with BareAsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies=client.cookies,
    ) as bare_client:
        rejected = await bare_client.post(f"/api/v1/actions/{action_id}/approve")
        assert rejected.status_code == 403

        csrf_token = client.cookies.get(settings.csrf_cookie_name)
        assert csrf_token
        allowed_past_csrf = await bare_client.post(
            f"/api/v1/actions/{action_id}/approve",
            headers={settings.csrf_header_name: csrf_token},
        )
        assert allowed_past_csrf.status_code == 409

        webhook = await bare_client.post(
            "/api/v1/webhooks/invalid-token",
            content=b"{}",
        )
        assert webhook.status_code == 401

        agent = await bare_client.post(
            f"/api/v1/agents/{uuid.uuid4()}/heartbeat",
            headers={"Authorization": "Bearer invalid-agent-token"},
        )
        assert agent.status_code == 401


# ---------- RBAC on /ask ----------


@pytest.mark.asyncio
async def test_viewer_cannot_run_a_decision(
    client: AsyncClient, two_tenants: dict
) -> None:
    """
    Regression: a viewer could post to /ask, reach the orchestrator, and
    trigger auto-executable tools. The route must refuse before that.
    """
    email = await _make_user(two_tenants["tenant_a"], role="viewer")
    await _login(client, email, "Passw0rd")

    response = await client.post(
        "/ask",
        data={"query": "Why did revenue drop?"},
        headers=_csrf(client),
    )

    assert response.status_code == 403
    assert "text/html" in response.headers["content-type"]
    assert "decision:run" in response.text


@pytest.mark.asyncio
async def test_viewer_is_refused_before_any_decision_is_recorded(
    client: AsyncClient, two_tenants: dict
) -> None:
    email = await _make_user(two_tenants["tenant_a"], role="viewer")
    await _login(client, email, "Passw0rd")

    await client.post("/ask", data={"query": "nothing should run"}, headers=_csrf(client))

    async with SessionLocal() as db:
        runs = (
            await db.execute(
                select(DecisionRun).where(
                    DecisionRun.tenant_id == two_tenants["tenant_a"],
                    DecisionRun.query == "nothing should run",
                )
            )
        ).scalars().all()

    assert runs == []


@pytest.mark.asyncio
async def test_analyst_can_run_a_decision(
    client: AsyncClient, two_tenants: dict
) -> None:
    """The gate must not lock out roles that legitimately hold it."""
    email = await _make_user(two_tenants["tenant_a"], role="analyst")
    await _login(client, email, "Passw0rd")

    response = await client.post(
        "/ask",
        data={"query": "What is our revenue?"},
        headers=_csrf(client),
    )

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_viewer_is_blocked_from_decision_history_html(
    client: AsyncClient, two_tenants: dict
) -> None:
    email = await _make_user(two_tenants["tenant_a"], role="viewer")
    await _login(client, email, "Passw0rd")

    response = await client.get("/decisions")

    assert response.status_code == 403
    assert "decision:run" in response.text


# ---------- tenant suspension ----------

@pytest.mark.asyncio
async def test_disabled_tenant_rejects_agent_credentials(
    two_tenants: dict,
) -> None:
    async with SessionLocal() as db:
        source = DataSource(
            tenant_id=two_tenants["tenant_a"],
            name="demo-agent-source",
            source_type="agent",
        )
        db.add(source)
        await db.flush()

        secret = "agent-demo-secret"
        agent = Agent(
            tenant_id=two_tenants["tenant_a"],
            name="demo-agent",
            source_id=source.id,
            status="active",
            credential_hash=agents_service._hash(secret),
        )
        db.add(agent)
        await db.commit()

        active = await agents_service.authenticate_agent(db, credential=secret)
        assert active.id == agent.id

        tenant = (
            await db.execute(
                select(Tenant).where(Tenant.id == two_tenants["tenant_a"])
            )
        ).scalar_one()
        tenant.is_active = False
        await db.commit()

        with pytest.raises(AgentError, match="tenant is disabled"):
            await agents_service.authenticate_agent(db, credential=secret)


@pytest.mark.asyncio
async def test_disabled_tenant_invalidates_an_existing_access_token(
    client: AsyncClient, two_tenants: dict
) -> None:
    email = await _make_user(two_tenants["tenant_a"], role="analyst")
    await _login(client, email, "Passw0rd")

    assert (await client.get("/api/v1/analytics/kpis")).status_code == 200

    await _set_tenant_active(two_tenants["tenant_a"], False)
    try:
        response = await client.get("/api/v1/analytics/kpis")
        assert response.status_code == 401
        assert "tenant is disabled" in response.json()["detail"]
    finally:
        await _set_tenant_active(two_tenants["tenant_a"], True)


@pytest.mark.asyncio
async def test_disabled_tenant_cannot_rotate_a_refresh_token(
    client: AsyncClient, two_tenants: dict
) -> None:
    email = await _make_user(two_tenants["tenant_a"], role="analyst")
    await _login(client, email, "Passw0rd")

    async with SessionLocal() as db:
        user = (await db.execute(select(User).where(User.email == email))).scalar_one()
        raw = generate_refresh_token()
        db.add(
            RefreshToken(
                tenant_id=user.tenant_id,
                user_id=user.id,
                token_hash=hash_refresh_token(raw),
                family_id=uuid.uuid4(),
                expires_at=datetime.now(UTC) + timedelta(days=1),
            )
        )
        await db.commit()

    await _set_tenant_active(two_tenants["tenant_a"], False)
    try:
        response = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": raw},
            headers=_csrf(client),
        )
        assert response.status_code in (401, 403), response.text
        assert "tenant is disabled" in response.text
    finally:
        await _set_tenant_active(two_tenants["tenant_a"], True)


@pytest.mark.asyncio
async def test_active_tenant_refresh_still_works(
    client: AsyncClient, two_tenants: dict
) -> None:
    """The guard must not break the normal rotation path."""
    email = await _make_user(two_tenants["tenant_a"], role="analyst")
    await _login(client, email, "Passw0rd")

    async with SessionLocal() as db:
        user = (await db.execute(select(User).where(User.email == email))).scalar_one()
        raw = generate_refresh_token()
        db.add(
            RefreshToken(
                tenant_id=user.tenant_id,
                user_id=user.id,
                token_hash=hash_refresh_token(raw),
                family_id=uuid.uuid4(),
                expires_at=datetime.now(UTC) + timedelta(days=1),
            )
        )
        await db.commit()

    response = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": raw},
        headers=_csrf(client),
    )

    assert response.status_code == 200, response.text
