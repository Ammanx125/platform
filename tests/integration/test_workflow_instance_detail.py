# tests/integration/test_workflow_instance_detail.py
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient

from app.db.models.workflow import WorkflowInstance
from app.db.session import SessionLocal


async def _login(client: AsyncClient, email: str, password: str) -> None:
    await client.post("/api/v1/auth/login", json={"email": email, "password": password})


async def _seed_instance(
    tenant_id, user_id, *, status: str, pending_approval_step: str | None
) -> str:
    async with SessionLocal() as db:
        instance = WorkflowInstance(
            tenant_id=tenant_id,
            workflow_key="procurement_review",
            status=status,
            current_step=pending_approval_step,
            step_states=[
                {
                    "name": "analyze",
                    "type": "analyze",
                    "status": "completed",
                    "output": {"kpis": 2},
                    "error": None,
                },
                {
                    "name": "approval",
                    "type": "await_approval",
                    "status": "waiting",
                    "output": {},
                    "error": None,
                },
            ],
            workflow_context={},
            triggered_by_user_id=user_id,
            trigger_type="manual",
            trigger_metadata={},
            pending_approval_step=pending_approval_step,
            started_at=datetime.now(UTC),
        )
        db.add(instance)
        await db.commit()
        await db.refresh(instance)
        return str(instance.id)


@pytest.mark.asyncio
async def test_instance_detail_renders_execution_timeline(
    client: AsyncClient, two_tenants: dict
) -> None:
    await _login(client, two_tenants["email_a"], two_tenants["password"])
    instance_id = await _seed_instance(
        two_tenants["tenant_a"],
        two_tenants["user_a"],
        status="waiting_workflow_approval",
        pending_approval_step="approval",
    )

    response = await client.get(f"/workflows/instances/{instance_id}")

    assert response.status_code == 200
    assert "Execution timeline" in response.text
    assert 'class="timeline"' in response.text
    assert "status-complete" in response.text
    assert "status-waiting" in response.text
    assert "Review and approve" in response.text


@pytest.mark.asyncio
async def test_instance_detail_other_tenant_not_visible(
    client: AsyncClient, two_tenants: dict
) -> None:
    instance_id = await _seed_instance(
        two_tenants["tenant_b"],
        two_tenants["user_b"],
        status="completed",
        pending_approval_step=None,
    )
    await _login(client, two_tenants["email_a"], two_tenants["password"])

    response = await client.get(f"/workflows/instances/{instance_id}")

    assert response.status_code == 404
