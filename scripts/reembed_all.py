# scripts/reembed_all.py
"""
Re-embed every chunk in every tenant using the currently configured provider.

Use after:
  - Changing EMBEDDING_MODEL
  - Changing EMBEDDING_PROVIDER
  - Applying a migration that alters the embedding column

This is destructive to the embeddings only — text is untouched. Runs
synchronously, one document at a time, committing per document so a failure
doesn't lose everything.

Usage:
    python -m scripts.reembed_all

    # Narrow to one tenant
    python -m scripts.reembed_all --tenant-slug demo1

    # Dry run: show what would be re-embedded, do nothing
    python -m scripts.reembed_all --dry-run
"""
from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import select

from app.db.models.knowledge import Document
from app.db.models.tenant import Tenant
from app.db.session import SessionLocal
from app.services.knowledge.service import rechunk_document


async def main(*, tenant_slug: str | None, dry_run: bool) -> None:
    async with SessionLocal() as db:
        stmt = select(Tenant)
        if tenant_slug:
            stmt = stmt.where(Tenant.slug == tenant_slug)
        tenants = (await db.execute(stmt)).scalars().all()
        if not tenants:
            print("no matching tenants")
            return

        total = 0
        for t in tenants:
            docs = (
                await db.execute(
                    select(Document).where(Document.tenant_id == t.id)
                )
            ).scalars().all()
            print(f"tenant {t.slug}: {len(docs)} document(s)")
            if dry_run:
                continue
            for d in docs:
                try:
                    await rechunk_document(db, document_id=d.id)
                    await db.commit()
                    print(f"  re-embedded: {d.title!r} ({d.chunk_count} chunks)")
                    total += 1
                except Exception as exc:  # noqa: BLE001
                    await db.rollback()
                    print(f"  FAILED: {d.title!r} — {exc}")

        if not dry_run:
            print(f"re-embedded {total} document(s)")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--tenant-slug", default=None)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    asyncio.run(main(tenant_slug=args.tenant_slug, dry_run=args.dry_run))