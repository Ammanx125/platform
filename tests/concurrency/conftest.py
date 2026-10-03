# tests/concurrency/conftest.py
"""
Fixtures for concurrency tests.

Unlike the general-purpose fixtures in tests/conftest.py, these do NOT
hold an open session across the test body. Concurrency tests open their
own sessions and must be free to do so without a fixture's session
interfering.

Setup and teardown each open a fresh, short-lived session.

Tenants and users are created with unique slugs/emails (uuid-based) so
concurrent test runs don't collide. Cleanup is explicit and idempotent.
"""
from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

import pytest_asyncio

from app.core.security import hash_password
from app.db.models.tenant import Tenant
from app.db.models.user import User
from app.db.seed import ensure_permission_catalog, seed_tenant_roles
from app.db.session import SessionLocal


@pytest_asyncio.fixture
async def concurrency_env() -> AsyncGenerator[dict]:
    """
    Create two tenants with one admin user each, plus a system user for
    tenant A. Commit, close the session, and yield only IDs.

    Caller gets:
      {
        "tenant_a": uuid, "tenant_b": uuid,
        "user_a": uuid, "user_b": uuid,
        "system_user_a": uuid,
      }

    Cleanup runs on a fresh session, deletes users then tenants.
    """
    tenant_a_id = uuid.uuid4()
    tenant_b_id = uuid.uuid4()

    # --- Setup ---
    async with SessionLocal() as db:
        await ensure_permission_catalog(db)

        ta = Tenant(
            id=tenant_a_id,
            name="Concurrency A",
            slug=f"conc-a-{tenant_a_id.hex[:12]}",
        )
        tb = Tenant(
            id=tenant_b_id,
            name="Concurrency B",
            slug=f"conc-b-{tenant_b_id.hex[:12]}",
        )
        db.add_all([ta, tb])
        await db.flush()

        roles_a = await seed_tenant_roles(db, tenant_id=ta.id)
        roles_b = await seed_tenant_roles(db, tenant_id=tb.id)

        email_a = f"conc-admin-a-{tenant_a_id.hex[:12]}@example.com"
        email_b = f"conc-admin-b-{tenant_b_id.hex[:12]}@example.com"
        password = "Passw0rd"

        ua = User(
            tenant_id=ta.id,
            email=email_a,
            password_hash=hash_password(password),
            is_active=True,
        )
        ua.roles = [roles_a["admin"]]

        ub = User(
            tenant_id=tb.id,
            email=email_b,
            password_hash=hash_password(password),
            is_active=True,
        )
        ub.roles = [roles_b["admin"]]

        # A second user in tenant A — needed for maker-checker tests where
        # the approver cannot be the proposer.
        email_a2 = f"conc-admin-a2-{tenant_a_id.hex[:12]}@example.com"
        ua2 = User(
            tenant_id=ta.id,
            email=email_a2,
            password_hash=hash_password(password),
            is_active=True,
        )
        ua2.roles = [roles_a["manager"]]

        # System user for tenant A, required by the event dispatcher.
        system_user_a = User(
            tenant_id=ta.id,
            email=f"system+{tenant_a_id}@sansa.local",
            password_hash=hash_password(uuid.uuid4().hex),
            full_name="System",
            is_active=True,
            is_system=True,
        )

        db.add_all([ua, ub, ua2, system_user_a])
        await db.flush()
        user_a_id = ua.id
        user_a2_id = ua2.id
        user_b_id = ub.id
        system_user_a_id = system_user_a.id
        await db.commit()

        env = {
            "tenant_a": tenant_a_id,
            "tenant_b": tenant_b_id,
            "tenant_slug_a": ta.slug,
            "tenant_slug_b": tb.slug,
            "user_a": user_a_id,
            "user_a2": user_a2_id,
            "user_b": user_b_id,
            "email_a": email_a,
            "email_b": email_b,
            "password": password,
            "system_user_a": system_user_a_id,
        }

    # --- Yield ---
    yield env

    # --- Teardown ---
    async with SessionLocal() as db:
        from sqlalchemy import delete
        # Deleting tenants cascades users, roles, actions, workflows,
        # events, audit events, etc. via FK ON DELETE CASCADE.
        await db.execute(delete(Tenant).where(Tenant.id.in_([tenant_a_id, tenant_b_id])))
        await db.commit()