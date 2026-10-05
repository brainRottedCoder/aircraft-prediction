"""Alert table — derived from risk transitions (docs/03 §9)."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db.session import Base
from .auth import User
from .fleet import Aircraft, Part


class Alert(Base):
    __tablename__ = "alert"
    __table_args__ = (
        CheckConstraint("level IN ('info','watch','critical')", name="alert_level"),
        CheckConstraint("acknowledged = FALSE OR acked_at IS NOT NULL", name="alert_ack"),
        Index("ix_alert_open", "acknowledged", "created_at"),
        # one live alert per aircraft+part — stops the 1.2s replay from spamming
        Index("uq_alert_live", "aircraft_id", "part_id",
              unique=True, postgresql_where=text("acknowledged = FALSE")),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    aircraft_id: Mapped[int] = mapped_column(ForeignKey("aircraft.id", ondelete="CASCADE"))
    part_id: Mapped[int] = mapped_column(ForeignKey("part.id"))
    level: Mapped[str] = mapped_column(String(8))
    message: Mapped[str] = mapped_column(Text)
    cycle: Mapped[int | None] = mapped_column(Integer)
    health: Mapped[float | None] = mapped_column(Numeric(4, 3))
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)
    acked_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    acked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.utcnow())

    aircraft: Mapped[Aircraft] = relationship(back_populates="alerts")
    part: Mapped[Part] = relationship(back_populates="alerts")
    acked_by_user: Mapped[User | None] = relationship(back_populates="alerts_acked")