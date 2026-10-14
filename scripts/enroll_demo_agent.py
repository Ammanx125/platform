"""Create a one-time enrollment token for the Meridian watcher."""
from __future__ import annotations

import asyncio
import os

from sqlalchemy import select

from app.db.models.agent import Agent
from app.db.models.dataset import DataSource
from app.db.models.tenant import Tenant
from app.db.models.user import User
from app.db.session import SessionLocal
from app.services.agents.service import (
    AgentError,
    create_agent_enrollment,
    reissue_agent_enrollment,
)

TENANT_SLUG = "meridian-transport-demo"
SOURCE_NAME = "Meridian Watcher Files"


async def create_enrollment(*, email: str) -> tuple[str, str]:
    async with SessionLocal() as db:
        tenant = (
            await db.execute(select(Tenant).where(Tenant.slug == TENANT_SLUG))
        ).scalar_one_or_none()
        if tenant is None:
            raise ValueError("Meridian demo tenant is not provisioned yet")

        user = (
            await db.execute(
                select(User).where(
                    User.tenant_id == tenant.id,
                    User.email == email,
                    User.is_active.is_(True),
                    User.is_system.is_(False),
                )
            )
        ).scalar_one_or_none()
        if user is None:
            raise ValueError(f"active demo manager {email!r} was not found")

        source = (
            await db.execute(
                select(DataSource).where(
                    DataSource.tenant_id == tenant.id,
                    DataSource.name == SOURCE_NAME,
                    DataSource.source_type == "agent",
                )
            )
        ).scalar_one_or_none()
        if source is None:
            raise ValueError("Meridian watcher source is not provisioned yet")

        existing = (
            await db.execute(
                select(Agent).where(
                    Agent.tenant_id == tenant.id,
                    Agent.source_id == source.id,
                    Agent.status.in_(("pending", "active")),
                ).order_by(Agent.created_at.desc())
            )
        ).scalars().first()
        try:
            if existing is not None:
                agent, token = await reissue_agent_enrollment(
                    db,
                    agent_id=existing.id,
                    tenant_id=tenant.id,
                    enrolled_by_user_id=user.id,
                )
            else:
                agent, token = await create_agent_enrollment(
                    db,
                    tenant_id=tenant.id,
                    source_id=source.id,
                    name="Meridian Transport File Watcher",
                    description="Watches the visible Meridian demo CSV folder.",
                    enrolled_by_user_id=user.id,
                )
        except AgentError as exc:
            raise ValueError(str(exc)) from exc
        await db.commit()
        return str(agent.id), token


def main() -> None:
    email = os.environ.get("DEMO_ADMIN_EMAIL", "").strip()
    if not email:
        raise SystemExit("Set DEMO_ADMIN_EMAIL to the Meridian manager's email.")
    try:
        agent_id, token = asyncio.run(create_enrollment(email=email))
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    print(f"Agent ID: {agent_id}")
    print("One-time enrollment token (expires in 30 minutes):")
    print(token)
    print(
        "Use it immediately with `sansa-agent enroll`; it will not be shown "
        "again. Any previous credential for this agent has been invalidated."
    )


if __name__ == "__main__":
    main()
