# app/db/seed.py
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.role import Permission, Role

CANONICAL_PERMISSIONS: dict[str, str] = {
    "user:manage":      "Invite, deactivate, and manage users in the tenant",
    "role:manage":      "Create and edit tenant roles",
    "dataset:read":     "View data sources and their metadata",
    "dataset:write":    "Create, edit, and ingest data sources",
    "knowledge:read":   "Search the semantic and knowledge layer",
    "decision:run":     "Run management questions through the orchestrator",
    "action:propose":   "Propose an action (model or user)",
    "action:approve":   "Approve a pending action",
    "action:reject":    "Reject a pending action",
    "workflow:manage":  "Create and edit workflows",
    "audit:read":       "View the audit trail",
    "knowledge:write":  "Create, edit, and delete knowledge documents",
    "tool_policy:manage": "Manage per-tenant tool approval policies",
}


CANONICAL_ROLES: dict[str, dict] = {
    "admin": {
        "description": "Full control of the tenant",
        "permissions": list(CANONICAL_PERMISSIONS.keys()),
    },
    "manager": {
        "description": "Run decisions, approve actions, read audit",
        "permissions": [
            "dataset:read", "knowledge:read", "decision:run",
            "action:propose", "action:approve", "action:reject",
            "tool_policy:manage",
            "workflow:manage", "audit:read", "user:manage",
        ],
    },
    "analyst": {
        "description": "Explore data and run decisions, no approvals",
        "permissions": [
            "dataset:read", "dataset:write",
            "knowledge:read", "decision:run", "action:propose",
        ],
    },
    "viewer": {
        "description": "Read-only access",
        "permissions": ["dataset:read", "knowledge:read", "audit:read"],
    },
}


async def ensure_permission_catalog(db: AsyncSession) -> dict[str, Permission]:
    """
    Idempotently ensure the global Permission catalog exists.
    Returns {key: Permission}.
    """
    result = await db.execute(select(Permission))
    existing = {p.key: p for p in result.scalars().all()}

    for key, description in CANONICAL_PERMISSIONS.items():
        if key not in existing:
            p = Permission(key=key, description=description)
            db.add(p)
            existing[key] = p

    await db.flush()
    return existing


async def seed_tenant_roles(db: AsyncSession, *, tenant_id: uuid.UUID) -> dict[str, Role]:
    """
    Idempotently create canonical roles for a tenant and attach permissions.
    Assumes ensure_permission_catalog() has already been called.
    """
    perms = (await db.execute(select(Permission))).scalars().all()
    perm_by_key = {p.key: p for p in perms}

    result = await db.execute(select(Role).where(Role.tenant_id == tenant_id))
    existing_roles = {r.name: r for r in result.scalars().all()}

    for name, spec in CANONICAL_ROLES.items():
        if name in existing_roles:
            role = existing_roles[name]
        else:
            role = Role(
                tenant_id=tenant_id,
                name=name,
                description=spec["description"],
                is_system=True,
            )
            db.add(role)
            existing_roles[name] = role
        # Reconcile permissions
        role.permissions = [perm_by_key[k] for k in spec["permissions"]]

    await db.flush()
    return existing_roles