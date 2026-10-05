"""Auth and audit tables."""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import BigInteger, DateTime, Enum, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db.session import Base


class UserRole(StrEnum):
    commander = "commander"
    maintenance_officer = "maintenance_officer"
    viewer = "viewer"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(48), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(96))
    role: Mapped[UserRole] = mapped_column(Enum(UserRole, name="user_role"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.utcnow())
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    work_orders: Mapped[list[WorkOrder]] = relationship(back_populates="created_by_user")
    audit_logs: Mapped[list[AuditLog]] = relationship(back_populates="actor")
    alerts_acked: Mapped[list[Alert]] = relationship(back_populates="acked_by_user")


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    entity: Mapped[str] = mapped_column(String(32))
    entity_id: Mapped[str] = mapped_column(String(32))
    action: Mapped[str] = mapped_column(String(24))
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    actor_name: Mapped[str] = mapped_column(String(48))  # denormalised: survives deletion
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.utcnow())
    before: Mapped[dict | None] = mapped_column(JSONB)
    after: Mapped[dict | None] = mapped_column(JSONB)
    request_id: Mapped[str | None] = mapped_column(String(32))

    actor: Mapped[User | None] = relationship(back_populates="audit_logs")

    __table_args__ = (
        Index("ix_audit_entity", "entity", "entity_id", "at"),
        Index("ix_audit_actor", "actor_id", "at"),
        Index("ix_audit_at", "at"),
    )