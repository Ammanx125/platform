# app/db/models/role.py
from __future__ import annotations

from sqlalchemy import Column, ForeignKey, String, Table, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin

role_permissions = Table(
    "role_permissions",
    Base.metadata,
    Column(
        "role_id",
        PG_UUID(as_uuid=True),
        ForeignKey("roles.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "permission_id",
        PG_UUID(as_uuid=True),
        ForeignKey("permissions.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


user_roles = Table(
    "user_roles",
    Base.metadata,
    Column(
        "user_id",
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "role_id",
        PG_UUID(as_uuid=True),
        ForeignKey("roles.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


class Permission(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """
    Global permission catalog. Not tenant-scoped: the same 'dataset:read'
    permission means the same thing in every tenant.
    """
    __tablename__ = "permissions"

    key: Mapped[str] = mapped_column(String(80), nullable=False, unique=True, index=True)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)

    roles = relationship(
        "Role",
        secondary=role_permissions,
        back_populates="permissions",
    )


class Role(Base, UUIDPrimaryKeyMixin, TimestampMixin, TenantMixin):
    """
    Roles are tenant-scoped: a tenant can define custom roles.
    Canonical roles (admin/manager/analyst/viewer) are seeded per tenant.
    """
    __tablename__ = "roles"

    name: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_system: Mapped[bool] = mapped_column(nullable=False, default=False)

    permissions = relationship(
        "Permission",
        secondary=role_permissions,
        lazy="selectin",
        back_populates="roles",
    )
    users = relationship(
        "User",
        secondary=user_roles,
        back_populates="roles",
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_roles_tenant_name"),
    )