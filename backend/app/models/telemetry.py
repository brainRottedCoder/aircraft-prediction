"""Append-only time-series tables — one row per aircraft per cycle."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db.session import Base
from .fleet import risk_level_enum

SENSOR_COLUMNS = ("s2", "s3", "s4", "s6", "s7", "s8", "s9", "s11",
                  "s12", "s13", "s14", "s15", "s17", "s20", "s21")


class HealthSnapshot(Base):
    __tablename__ = "health_snapshot"
    __table_args__ = (
        UniqueConstraint("aircraft_id", "part_id", "cycle"),
        CheckConstraint("health BETWEEN 0 AND 1", name="hs_health"),
        # serves "last N cycles" for every part in one ordered range scan
        Index("ix_hs_window", "aircraft_id", "part_id", "cycle"),
        Index("ix_hs_recent", "aircraft_id", "cycle"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    aircraft_id: Mapped[int] = mapped_column(ForeignKey("aircraft.id", ondelete="CASCADE"))
    part_id: Mapped[int] = mapped_column(ForeignKey("part.id"))
    cycle: Mapped[int] = mapped_column(Integer)
    health: Mapped[float] = mapped_column(Numeric(4, 3))
    risk_level: Mapped[str] = mapped_column(risk_level_enum)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.utcnow())

    aircraft: Mapped[Aircraft] = relationship(back_populates="snapshots")
    part: Mapped[Part] = relationship(back_populates="snapshots")


class EngineTelemetry(Base):
    """3 operating settings + the 15 informative C-MAPSS sensors (s6 included)."""

    __tablename__ = "engine_telemetry"
    __table_args__ = (
        UniqueConstraint("aircraft_id", "cycle"),
        Index("ix_tel_window", "aircraft_id", "cycle"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    aircraft_id: Mapped[int] = mapped_column(ForeignKey("aircraft.id", ondelete="CASCADE"))
    cycle: Mapped[int] = mapped_column(Integer)
    cmapss_unit_id: Mapped[int] = mapped_column(SmallInteger)
    setting_1: Mapped[float] = mapped_column(Numeric(10, 6))
    setting_2: Mapped[float] = mapped_column(Numeric(10, 6))
    setting_3: Mapped[float] = mapped_column(Numeric(10, 6))
    s2: Mapped[float] = mapped_column(Numeric(12, 4))
    s3: Mapped[float] = mapped_column(Numeric(12, 4))
    s4: Mapped[float] = mapped_column(Numeric(12, 4))
    s6: Mapped[float] = mapped_column(Numeric(12, 4))
    s7: Mapped[float] = mapped_column(Numeric(12, 4))
    s8: Mapped[float] = mapped_column(Numeric(12, 4))
    s9: Mapped[float] = mapped_column(Numeric(12, 4))
    s11: Mapped[float] = mapped_column(Numeric(12, 4))
    s12: Mapped[float] = mapped_column(Numeric(12, 4))
    s13: Mapped[float] = mapped_column(Numeric(12, 4))
    s14: Mapped[float] = mapped_column(Numeric(12, 4))
    s15: Mapped[float] = mapped_column(Numeric(12, 4))
    s17: Mapped[float] = mapped_column(Numeric(12, 4))
    s20: Mapped[float] = mapped_column(Numeric(12, 4))
    s21: Mapped[float] = mapped_column(Numeric(12, 4))
    regime: Mapped[int] = mapped_column(SmallInteger, default=0)
    source: Mapped[str] = mapped_column(String(16), default="cmapss")
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.utcnow())

    aircraft: Mapped[Aircraft] = relationship(back_populates="telemetry")


class ComponentHealth(Base):
    __tablename__ = "component_health"
    __table_args__ = (
        UniqueConstraint("aircraft_id", "cycle"),
        CheckConstraint(
            "fan BETWEEN 0 AND 1 AND hpc BETWEEN 0 AND 1 "
            "AND hpt BETWEEN 0 AND 1 AND lpt BETWEEN 0 AND 1",
            name="ch_range",
        ),
        Index("ix_ch_window", "aircraft_id", "cycle"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    aircraft_id: Mapped[int] = mapped_column(ForeignKey("aircraft.id", ondelete="CASCADE"))
    cycle: Mapped[int] = mapped_column(Integer)
    fan: Mapped[float] = mapped_column(Numeric(4, 3))
    hpc: Mapped[float] = mapped_column(Numeric(4, 3))
    hpt: Mapped[float] = mapped_column(Numeric(4, 3))
    lpt: Mapped[float] = mapped_column(Numeric(4, 3))
    combustor: Mapped[float | None] = mapped_column(Numeric(4, 3))
    lpc: Mapped[float | None] = mapped_column(Numeric(4, 3))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.utcnow())

    aircraft: Mapped[Aircraft] = relationship(back_populates="component_health")


class MlPrediction(Base):
    __tablename__ = "ml_prediction"
    __table_args__ = (
        UniqueConstraint("aircraft_id", "cycle"),
        CheckConstraint("rul BETWEEN 0 AND 125", name="mlp_rul"),
        Index("ix_mlp_window", "aircraft_id", "cycle"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    aircraft_id: Mapped[int] = mapped_column(ForeignKey("aircraft.id", ondelete="CASCADE"))
    cycle: Mapped[int] = mapped_column(Integer)
    rul: Mapped[int] = mapped_column(Integer)
    fan: Mapped[float | None] = mapped_column(Numeric(4, 3))
    hpc: Mapped[float | None] = mapped_column(Numeric(4, 3))
    hpt: Mapped[float | None] = mapped_column(Numeric(4, 3))
    lpt: Mapped[float | None] = mapped_column(Numeric(4, 3))
    top_sensors: Mapped[list] = mapped_column(JSONB)
    deviation: Mapped[dict] = mapped_column(JSONB)
    model_version: Mapped[str] = mapped_column(String(48))
    latency_ms: Mapped[float] = mapped_column(Numeric(8, 2))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.utcnow())
    aircraft: Mapped[Aircraft] = relationship(back_populates="predictions")
