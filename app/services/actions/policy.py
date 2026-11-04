# app/services/actions/policy.py
"""
Per-tenant tool policy resolution.

Given a tenant and a tool, return the effective policy that governs
whether the tool auto-executes, requires approval, or is disabled.

Resolution:
  1. If a TenantToolPolicy exists for (tenant, tool), use it.
  2. Otherwise, fall back to the tool's declared risk_level.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.tool_policy import TenantToolPolicy
from app.services.actions.base import (
    RISK_ELEVATED,
    RISK_READ,
    RISK_REQUIRED,
)
from app.services.actions.registry import get as get_action

# Effective policy values returned by resolve().
POLICY_AUTO = "auto"
POLICY_REQUIRE_APPROVAL = "require_approval"
POLICY_DISABLED = "disabled"

VALID_OVERRIDES = frozenset({POLICY_AUTO, POLICY_REQUIRE_APPROVAL, POLICY_DISABLED})


def _default_policy_for_risk(risk_level: str) -> str:
    """Map a tool's risk_level to an effective policy."""
    if risk_level in (RISK_REQUIRED, RISK_ELEVATED):
        return POLICY_REQUIRE_APPROVAL
    if risk_level == RISK_READ:
        return POLICY_AUTO
    # "report" and "configurable" default to auto.
    return POLICY_AUTO


def _validate_override_for_risk(
    tool_name: str,
    tool_risk_level: str,
    policy_override: str,
) -> None:
    """Reject tenant overrides that weaken a mandatory approval requirement."""
    default_policy = _default_policy_for_risk(tool_risk_level)
    if default_policy == POLICY_REQUIRE_APPROVAL and policy_override == POLICY_AUTO:
        raise ValueError(
            f"tool {tool_name!r} is marked {tool_risk_level!r}; "
            "auto execution is not allowed because it weakens a required "
            "approval gate"
        )


def _normalize_override_for_risk(
    tool_name: str,
    tool_risk_level: str,
    policy_override: str,
) -> str:
    """Clamp stale or invalid overrides back to the minimum safe policy."""
    default_policy = _default_policy_for_risk(tool_risk_level)
    if default_policy == POLICY_REQUIRE_APPROVAL and policy_override == POLICY_AUTO:
        return default_policy
    return policy_override


async def resolve(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    tool_name: str,
    tool_risk_level: str,
) -> str:
    """
    Return the effective policy: POLICY_AUTO, POLICY_REQUIRE_APPROVAL,
    or POLICY_DISABLED.
    """
    override = (
        await db.execute(
            select(TenantToolPolicy).where(
                TenantToolPolicy.tenant_id == tenant_id,
                TenantToolPolicy.tool_name == tool_name,
            )
        )
    ).scalar_one_or_none()

    if override is not None:
        normalized = _normalize_override_for_risk(
            tool_name=tool_name,
            tool_risk_level=tool_risk_level,
            policy_override=override.policy_override,
        )
        return normalized
    return _default_policy_for_risk(tool_risk_level)


async def list_overrides(
    db: AsyncSession, *, tenant_id: uuid.UUID
) -> list[TenantToolPolicy]:
    stmt = (
        select(TenantToolPolicy)
        .where(TenantToolPolicy.tenant_id == tenant_id)
        .order_by(TenantToolPolicy.tool_name)
    )
    return list((await db.execute(stmt)).scalars().all())


async def set_override(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    tool_name: str,
    policy_override: str,
    actor_user_id: uuid.UUID,
) -> TenantToolPolicy:
    if policy_override not in VALID_OVERRIDES:
        raise ValueError(
            f"policy_override must be one of {sorted(VALID_OVERRIDES)}"
        )

    action = get_action(tool_name)
    if action is not None:
        _validate_override_for_risk(
            tool_name=tool_name,
            tool_risk_level=action.risk_level,
            policy_override=policy_override,
        )

    existing = (
        await db.execute(
            select(TenantToolPolicy).where(
                TenantToolPolicy.tenant_id == tenant_id,
                TenantToolPolicy.tool_name == tool_name,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.policy_override = policy_override
        existing.created_by_user_id = actor_user_id
        await db.flush()
        return existing
    row = TenantToolPolicy(
        tenant_id=tenant_id,
        tool_name=tool_name,
        policy_override=policy_override,
        created_by_user_id=actor_user_id,
    )
    db.add(row)
    await db.flush()
    return row


async def clear_override(
    db: AsyncSession, *, tenant_id: uuid.UUID, tool_name: str
) -> bool:
    existing = (
        await db.execute(
            select(TenantToolPolicy).where(
                TenantToolPolicy.tenant_id == tenant_id,
                TenantToolPolicy.tool_name == tool_name,
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        return False
    await db.delete(existing)
    await db.flush()
    return True