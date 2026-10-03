# app/services/agents/classifier.py
"""
Classify file observations for an agent source.

Reads FileObservation rows and splits them into:
  - ready:        ingestable and under the size cap
  - needs_review: ingestable but large (over the cap) — needs explicit
                  customer confirmation
  - unsupported:  extension not in the allowlist

The classifier is deterministic and pure (aside from DB reads). It does
not trigger anything; it produces a report. The ingest endpoint decides
what to do with it.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.timestamps import FileObservation

# Extensions that map to an existing ingestion connector.
INGESTABLE_EXTENSIONS: dict[str, str] = {
    ".csv": "csv",
    ".xlsx": "excel",
    ".xls": "excel",
    ".pdf": "pdf",
}

# Files under this size are "ready". Between this and the hard cap, they
# are "needs_review". Above the hard cap, they are "needs_review" too but
# with a stronger reason.
READY_SIZE_BYTES = 50 * 1024 * 1024
HARD_SIZE_BYTES = 500 * 1024 * 1024


@dataclass
class Observation:
    """One classified file observation."""
    observation_id: uuid.UUID
    path: str
    content_hash: str
    byte_size: int
    extension: str
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "observation_id": str(self.observation_id),
            "path": self.path,
            "content_hash": self.content_hash,
            "byte_size": self.byte_size,
            "extension": self.extension,
            "reason": self.reason,
        }


@dataclass
class Classification:
    ready: list[Observation] = field(default_factory=list)
    needs_review: list[Observation] = field(default_factory=list)
    unsupported: list[Observation] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        return {
            "ready": len(self.ready),
            "needs_review": len(self.needs_review),
            "unsupported": len(self.unsupported),
        }

    def to_dict(self) -> dict:
        return {
            "counts": self.counts(),
            "ready": [o.to_dict() for o in self.ready],
            "needs_review": [o.to_dict() for o in self.needs_review],
            "unsupported": [o.to_dict() for o in self.unsupported],
        }


def _extension_of(path: str) -> str:
    if "." not in path:
        return ""
    return "." + path.rsplit(".", 1)[-1].lower()


async def classify_observations(
    db: AsyncSession, *, tenant_id: uuid.UUID, source_id: uuid.UUID
) -> Classification:
    """
    Load the latest observation per path for a source and classify them.
    Scoped to `tenant_id`; passing a foreign `source_id` returns nothing.

    "Latest per path" means: if the same path appears with multiple hashes
    (changed over time), only the newest observation (by first_seen_at) is
    considered. Older observations for the same path are stale.
    """
    # Get all observations for the source, newest first per path.
    rows = (
        await db.execute(
            select(FileObservation)
            .where(
                FileObservation.source_id == source_id,
                FileObservation.tenant_id == tenant_id,
                FileObservation.status != "deleted",
            )
            .order_by(FileObservation.first_seen_at.desc())
        )
    ).scalars().all()

    latest_by_path: dict[str, FileObservation] = {}
    for row in rows:
        if row.path not in latest_by_path:
            latest_by_path[row.path] = row

    result = Classification()
    for path, row in latest_by_path.items():
        ext = _extension_of(path)
        size = row.byte_size or 0

        if ext not in INGESTABLE_EXTENSIONS:
            result.unsupported.append(Observation(
                observation_id=row.id,
                path=path,
                content_hash=row.content_hash,
                byte_size=size,
                extension=ext,
                reason="extension not supported",
            ))
            continue

        if size > HARD_SIZE_BYTES:
            result.needs_review.append(Observation(
                observation_id=row.id,
                path=path,
                content_hash=row.content_hash,
                byte_size=size,
                extension=ext,
                reason=f"file exceeds {HARD_SIZE_BYTES // (1024 * 1024)} MB",
            ))
        elif size > READY_SIZE_BYTES:
            result.needs_review.append(Observation(
                observation_id=row.id,
                path=path,
                content_hash=row.content_hash,
                byte_size=size,
                extension=ext,
                reason=f"file exceeds {READY_SIZE_BYTES // (1024 * 1024)} MB",
            ))
        else:
            result.ready.append(Observation(
                observation_id=row.id,
                path=path,
                content_hash=row.content_hash,
                byte_size=size,
                extension=ext,
            ))

    return result