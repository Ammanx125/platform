# scripts/seed_kpis.py
"""
Seed the base KPI catalog.

Usage:
    python -m scripts.seed_kpis
"""
from __future__ import annotations

import asyncio

from app.db.seed_kpis import seed_kpis
from app.db.session import SessionLocal


async def main() -> None:
    async with SessionLocal() as session:
        n = await seed_kpis(session)
        await session.commit()
        print(f"reconciled {n} KPI definition(s)")


if __name__ == "__main__":
    asyncio.run(main())