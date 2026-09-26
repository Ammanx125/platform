# app/db/models/tool_policy.py
from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin


class TenantToolPolicy(Base, UUIDPrimaryKeyMixin, TimestampMixin, TenantMixin):
    """
    A per-tenant override of a tool's default approval behavior.

    Absence of a row means: use the tool's declared risk_level.

    policy_override:
      - "auto"              — always execute, even if risk_level says otherwise
      - "require_approval"  — always queue for approval, even if risk_level says otherwise
      - "disabled"          — reject the tool for this tenant

    Setting "auto" on a "required" tool is a deliberate widening of authority.
    It requires tool_policy:manage permission and is audited. Use sparingly.
    """
    __tablename__ = "tenant_tool_policies"

    tool_name: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    policy_override: Mapped[str] = mapped_column(String(20), nullable=False)

    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "tool_name", name="uq_tenant_tool_policies"),
    )