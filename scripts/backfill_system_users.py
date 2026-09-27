"""Ensure every existing tenant has a system user."""
from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.db.models.tenant import Tenant
from app.db.session import SessionLocal
from scripts.create_admin import ensure_system_user


async def main() -> None:
    async with SessionLocal() as db:
        tenants = (await db.execute(select(Tenant))).scalars().all()
        for tenant in tenants:
            await ensure_system_user(db, tenant_id=tenant.id)
        await db.commit()
    print(f"backfilled system users for {len(tenants)} tenant(s)")


if __name__ == "__main__":
    asyncio.run(main())