# scripts/seed_packs.py
"""
Seed the industry pack catalog.

Usage:
    python -m scripts.seed_packs
"""
from __future__ import annotations

import asyncio

from app.db.seed_packs import seed_industry_packs
from app.db.session import SessionLocal


async def main() -> None:
    async with SessionLocal() as db:
        n = await seed_industry_packs(db)
        await db.commit()
        print(f"reconciled {n} industry pack(s)")


if __name__ == "__main__":
    asyncio.run(main())