# app/db/models/user.py
from __future__ import annotations

from sqlalchemy import Boolean, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin


class User(Base, UUIDPrimaryKeyMixin, TimestampMixin, TenantMixin):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_system: Mapped[bool] = mapped_column(nullable=False, default=False)

    # Relationships
    roles = relationship(
        "Role",
        secondary="user_roles",
        lazy="selectin",
        back_populates="users",
    )

    __table_args__ = (
        Index(
            "ix_users_one_system_per_tenant",
            "tenant_id",
            unique=True,
            postgresql_where=text("is_system = true"),
        ),
    )