# scripts/seed_anomaly_detectors.py
"""
Seed the base anomaly detector catalog.

Usage:
    python -m scripts.seed_anomaly_detectors
"""
from __future__ import annotations

import asyncio

from app.db.seed_anomaly_detectors import seed_anomaly_detectors
from app.db.session import SessionLocal


async def main() -> None:
    async with SessionLocal() as session:
        n = await seed_anomaly_detectors(session)
        await session.commit()
        print(f"reconciled {n} anomaly detector(s)")


if __name__ == "__main__":
    asyncio.run(main())