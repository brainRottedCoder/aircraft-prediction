"""Fleet core — aircraft, part catalog, aircraft_part state."""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ENUM
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db.session import Base


class RiskLevel(StrEnum):
    healthy = "healthy"
    watch = "watch"
    critical = "critical"


risk_level_enum = ENUM("healthy", "watch", "critical", name="risk_level")


class Aircraft(Base):
    __tablename__ = "aircraft"
    __table_args__ = (
        CheckConstraint("current_cycle >= 1", name="aircraft_cycle"),
        CheckConstraint("rul BETWEEN 0 AND 125", name="aircraft_rul"),
        Index("ix_aircraft_mission_ready", "mission_ready"),
        Index("ix_aircraft_risk_level", "risk_level"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(16), unique=True)      # Fighter-01
    name: Mapped[str] = mapped_column(String(48))
    aircraft_ref_id: Mapped[int] = mapped_column(ForeignKey("aircraft_ref.id"), unique=True)
    cmapss_unit_id: Mapped[int] = mapped_column(SmallInteger, unique=True)
    tail_number: Mapped[str | None] = mapped_column(String(16))
    aircraft_model: Mapped[str | None] = mapped_column(String(16))
    home_base: Mapped[str | None] = mapped_column(String(16))

    current_cycle: Mapped[int] = mapped_column(Integer, default=1)
    rul: Mapped[int] = mapped_column(Integer, default=125)
    mission_ready: Mapped[bool] = mapped_column(Boolean, default=True)
    worst_part: Mapped[str | None] = mapped_column(String(16))
    risk_level: Mapped[str] = mapped_column(risk_level_enum, default="healthy")
    demo_offset: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.utcnow())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.utcnow(), onupdate=lambda: datetime.utcnow())

    aircraft_ref: Mapped[AircraftRef] = relationship(back_populates="aircraft")
    parts: Mapped[list[AircraftPart]] = relationship(
        back_populates="aircraft", cascade="all, delete-orphan"
    )
    work_orders: Mapped[list[WorkOrder]] = relationship(
        back_populates="aircraft", cascade="all, delete-orphan"
    )
    alerts: Mapped[list[Alert]] = relationship(
        back_populates="aircraft", cascade="all, delete-orphan"
    )
    bookings: Mapped[list[AgencyBooking]] = relationship(
        back_populates="aircraft", cascade="all, delete-orphan"
    )
    snapshots: Mapped[list[HealthSnapshot]] = relationship(
        back_populates="aircraft", cascade="all, delete-orphan"
    )
    telemetry: Mapped[list[EngineTelemetry]] = relationship(
        back_populates="aircraft", cascade="all, delete-orphan"
    )
    component_health: Mapped[list[ComponentHealth]] = relationship(
        back_populates="aircraft", cascade="all, delete-orphan"
    )
    predictions: Mapped[list[MlPrediction]] = relationship(
        back_populates="aircraft", cascade="all, delete-orphan"
    )


class Part(Base):
    __tablename__ = "part"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(16), unique=True)
    label: Mapped[str] = mapped_column(String(32))
    sort_order: Mapped[int] = mapped_column(SmallInteger)
    is_simulated: Mapped[bool] = mapped_column(Boolean, default=True)
    method: Mapped[str] = mapped_column(String(40))
    agency_id: Mapped[int | None] = mapped_column(ForeignKey("agency.id"))

    agency: Mapped[Agency | None] = relationship(back_populates="parts")
    aircraft_part: Mapped[list[AircraftPart]] = relationship(
        back_populates="part", cascade="all, delete-orphan"
    )
    work_orders: Mapped[list[WorkOrder]] = relationship(back_populates="part")
    alerts: Mapped[list[Alert]] = relationship(
        back_populates="part", cascade="all, delete-orphan"
    )
    bookings: Mapped[list[AgencyBooking]] = relationship(back_populates="part")
    snapshots: Mapped[list[HealthSnapshot]] = relationship(back_populates="part")


class AircraftPart(Base):
    __tablename__ = "aircraft_part"
    __table_args__ = (
        UniqueConstraint("aircraft_id", "part_id"),
        CheckConstraint("health BETWEEN 0 AND 1", name="ap_health"),
        CheckConstraint("back_in_service_days IS NULL OR back_in_service_days >= 0", name="ap_bis"),
        Index("ix_ap_aircraft", "aircraft_id"),
        Index("ix_ap_risk_level", "risk_level"),
        Index("ix_ap_health", "health"),
        # hot path: the summary card and the critical list
        Index("ix_ap_critical", "aircraft_id", postgresql_where=text("risk_level = 'critical'")),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    aircraft_id: Mapped[int] = mapped_column(ForeignKey("aircraft.id", ondelete="CASCADE"))
    part_id: Mapped[int] = mapped_column(ForeignKey("part.id"))
    health: Mapped[float] = mapped_column(Numeric(4, 3))
    risk_level: Mapped[str] = mapped_column(risk_level_enum)
    rul: Mapped[int | None] = mapped_column(Integer)
    do_by_cycle: Mapped[int | None] = mapped_column(Integer)
    worst_component: Mapped[str | None] = mapped_column(String(24))
    spare_id: Mapped[int | None] = mapped_column(ForeignKey("spare.id"))
    agency_id: Mapped[int | None] = mapped_column(ForeignKey("agency.id"))
    back_in_service_days: Mapped[int | None] = mapped_column(Integer)
    is_simulated: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.utcnow(), onupdate=lambda: datetime.utcnow())

    aircraft: Mapped[Aircraft] = relationship(back_populates="parts")
    part: Mapped[Part] = relationship(back_populates="aircraft_part")
    spare: Mapped[Spare | None] = relationship(back_populates="aircraft_part")
    agency: Mapped[Agency | None] = relationship(back_populates="assignments")


from .maintenance import Agency, Spare  # noqa: E402
from .reference import AircraftRef  # noqa: E402