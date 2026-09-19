# scripts/seed_concepts.py
"""
Seed the global canonical concept catalog.

Usage:
    python -m scripts.seed_concepts
"""
from __future__ import annotations

import asyncio

from app.db.seed_concepts import seed_canonical_concepts
from app.db.session import SessionLocal


async def main() -> None:
    async with SessionLocal() as db:
        n = await seed_canonical_concepts(db)
        await db.commit()
        print(f"reconciled {n} canonical concepts")


if __name__ == "__main__":
    asyncio.run(main())