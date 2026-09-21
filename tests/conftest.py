from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.rate_limit import limiter
from app.core.security import hash_password
from app.db.models.tenant import Tenant
from app.db.models.user import User
from app.db.seed import ensure_permission_catalog, seed_tenant_roles
from app.db.session import SessionLocal, engine
from app.main import app


@pytest_asyncio.fixture(autouse=True)
async def reset_rate_limits() -> AsyncGenerator[None]:
    limiter.reset()
    yield
    limiter.reset()


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture(autouse=True)
async def dispose_db_engine() -> AsyncGenerator[None]:
    yield
    await engine.dispose()


@pytest_asyncio.fixture
async def two_tenants() -> AsyncGenerator[dict]:
    """Create two tenants with admin users and clean them up after the test."""
    async with SessionLocal() as db:
        await ensure_permission_catalog(db)

        ta = Tenant(name="Tenant A", slug=f"ta-{id(db)}")
        tb = Tenant(name="Tenant B", slug=f"tb-{id(db)}")
        db.add_all([ta, tb])
        await db.flush()

        roles_a = await seed_tenant_roles(db, tenant_id=ta.id)
        roles_b = await seed_tenant_roles(db, tenant_id=tb.id)

        ua = User(
            tenant_id=ta.id,
            email=f"admin-a-{id(db)}@example.com",
            password_hash=hash_password("Passw0rd"),
            is_active=True,
        )
        ua.roles = [roles_a["admin"]]
        ub = User(
            tenant_id=tb.id,
            email=f"admin-b-{id(db)}@example.com",
            password_hash=hash_password("Passw0rd"),
            is_active=True,
        )
        ub.roles = [roles_b["admin"]]
        db.add_all([ua, ub])
        await db.flush()
        await db.commit()

        yield {
            "tenant_a": ta.id,
            "tenant_b": tb.id,
            "user_a": ua.id,
            "user_b": ub.id,
            "email_a": ua.email,
            "email_b": ub.email,
            "password": "Passw0rd",
        }

        await db.delete(ua)
        await db.delete(ub)
        await db.commit()
        await db.delete(ta)
        await db.delete(tb)
        await db.commit()