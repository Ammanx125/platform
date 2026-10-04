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
from app.services.agents.service import AgentError, create_agent_enrollment

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
                select(Agent.id, Agent.status).where(
                    Agent.tenant_id == tenant.id,
                    Agent.source_id == source.id,
                    Agent.status.in_(("pending", "active")),
                )
            )
        ).first()
        if existing is not None:
            raise ValueError(
                f"Meridian watcher already exists in {existing.status!r} "
                f"status (agent {existing.id}); refusing to create a second "
                "agent for the same source"
            )

        try:
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
    agent_id, token = asyncio.run(create_enrollment(email=email))
    print(f"Agent ID: {agent_id}")
    print("One-time enrollment token (expires in 30 minutes):")
    print(token)
    print("Use it immediately with `sansa-agent enroll`; it will not be shown again.")


if __name__ == "__main__":
    main()
