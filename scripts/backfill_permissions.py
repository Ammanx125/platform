# scripts/backfill_permissions.py
"""
Backfill the permission catalog and re-attach permissions to existing roles.

Use after editing CANONICAL_PERMISSIONS or CANONICAL_ROLES in app/db/seed.py.

What it does:
  1. Ensures every permission in CANONICAL_PERMISSIONS exists.
  2. For every tenant, reconciles its canonical roles' permission sets.
     (Requires seed_tenant_roles to reconcile existing roles — see note.)

Usage:
    python -m scripts.backfill_permissions
"""
from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.db.models.tenant import Tenant
from app.db.seed import ensure_permission_catalog, seed_tenant_roles
from app.db.session import SessionLocal


async def main() -> None:
    async with SessionLocal() as db:
        await ensure_permission_catalog(db)

        tenants = (await db.execute(select(Tenant))).scalars().all()
        for t in tenants:
            await seed_tenant_roles(db, tenant_id=t.id)

        await db.commit()
        print(f"backfilled permissions for {len(tenants)} tenant(s)")


if __name__ == "__main__":
    asyncio.run(main())