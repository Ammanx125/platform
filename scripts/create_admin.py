# scripts/create_admin.py
"""
Create a tenant, seed its roles/permissions, and create the first admin user.

Usage:
    python -m scripts.create_admin \
        --tenant-name "Acme Corp" \
        --tenant-slug "acme" \
        --admin-email "admin@acme.test" \
        --admin-password "Sansa2026"
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import select

from app.core.security import hash_password
from app.db.models.role import Role
from app.db.models.tenant import Tenant
from app.db.models.user import User
from app.db.seed import ensure_permission_catalog, seed_tenant_roles
from app.db.session import SessionLocal


async def run(
    *,
    tenant_name: str,
    tenant_slug: str,
    admin_email: str,
    admin_password: str,
) -> None:
    async with SessionLocal() as db:
        existing = (
            await db.execute(select(Tenant).where(Tenant.slug == tenant_slug))
        ).scalar_one_or_none()
        if existing:
            print(f"tenant slug already exists: {tenant_slug}", file=sys.stderr)
            sys.exit(1)

        tenant = Tenant(name=tenant_name, slug=tenant_slug)
        db.add(tenant)
        await db.flush()

        await ensure_permission_catalog(db)
        roles = await seed_tenant_roles(db, tenant_id=tenant.id)

        admin_role = roles["admin"]
        user = User(
            tenant_id=tenant.id,
            email=admin_email,
            password_hash=hash_password(admin_password),
            full_name="Administrator",
            is_active=True,
        )
        user.roles = [admin_role]
        db.add(user)
        await db.flush()

        await db.commit()

        print(f"tenant_id = {tenant.id}")
        print(f"user_id   = {user.id}")
        print(f"email     = {admin_email}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--tenant-name", required=True)
    p.add_argument("--tenant-slug", required=True)
    p.add_argument("--admin-email", required=True)
    p.add_argument("--admin-password", required=True)
    args = p.parse_args()

    asyncio.run(run(
        tenant_name=args.tenant_name,
        tenant_slug=args.tenant_slug,
        admin_email=args.admin_email,
        admin_password=args.admin_password,
    ))


if __name__ == "__main__":
    main()