"""Reference tables — loaded verbatim from the Drive CSVs, never mutated at runtime."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import (
    Date,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db.session import Base


class AircraftRef(Base):
    __tablename__ = "aircraft_ref"

    __table_args__ = (Index("ix_aircraft_ref_model", "aircraft_model"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    aircraft_id: Mapped[str] = mapped_column(String(8), unique=True)
    tail_number: Mapped[str] = mapped_column(String(16))
    aircraft_model: Mapped[str] = mapped_column(String(16))
    engine_id: Mapped[str] = mapped_column(String(16))
    engine_model: Mapped[str] = mapped_column(String(16))
    manufacture_date: Mapped[date | None] = mapped_column(Date)
    induction_date: Mapped[date | None] = mapped_column(Date)
    home_base: Mapped[str | None] = mapped_column(String(16))
    total_flight_hours: Mapped[int | None] = mapped_column(Integer)
    total_cycles: Mapped[int | None] = mapped_column(Integer)
    current_status: Mapped[str | None] = mapped_column(String(24))

    aircraft: Mapped[Aircraft | None] = relationship(back_populates="aircraft_ref")
    component_refs: Mapped[list[ComponentRef]] = relationship(
        back_populates="aircraft_ref", cascade="all, delete-orphan"
    )
    flight_ops: Mapped[list[FlightOpsMonthly]] = relationship(
        back_populates="aircraft_ref", cascade="all, delete-orphan"
    )


class ComponentRef(Base):
    __tablename__ = "component_ref"

    __table_args__ = (
        Index("ix_component_ref_aircraft", "aircraft_ref_id"),
        Index("ix_component_ref_name", "component_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    component_id: Mapped[str] = mapped_column(String(12), unique=True)
    aircraft_ref_id: Mapped[int] = mapped_column(ForeignKey("aircraft_ref.id", ondelete="CASCADE"))
    engine_id: Mapped[str] = mapped_column(String(16))
    component_name: Mapped[str] = mapped_column(String(48))
    component_system: Mapped[str] = mapped_column(String(24))
    installation_date: Mapped[date | None] = mapped_column(Date)
    operating_hours: Mapped[int | None] = mapped_column(Integer)
    operating_cycles: Mapped[int | None] = mapped_column(Integer)
    life_limit_hours: Mapped[int | None] = mapped_column(Integer)
    life_limit_cycles: Mapped[int | None] = mapped_column(Integer)
    health_status: Mapped[str | None] = mapped_column(String(16))
    times_replaced: Mapped[int] = mapped_column(SmallInteger, default=0)

    aircraft_ref: Mapped[AircraftRef] = relationship(back_populates="component_refs")
    spares: Mapped[list[Spare]] = relationship(back_populates="component_ref")

    @property
    def implied_health(self) -> float | None:
        """1 - operating_cycles/life_limit_cycles — cross-check for ML health."""
        if not self.life_limit_cycles:
            return None
        return round(1 - self.operating_cycles / self.life_limit_cycles, 3)


class FlightOpsMonthly(Base):
    __tablename__ = "flight_ops_monthly"
    __table_args__ = (
        UniqueConstraint("aircraft_ref_id", "month"),
        Index("ix_flight_ops_aircraft", "aircraft_ref_id", "month"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    aircraft_ref_id: Mapped[int] = mapped_column(ForeignKey("aircraft_ref.id", ondelete="CASCADE"))
    month: Mapped[str] = mapped_column(String(7))
    flight_hours: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    sorties: Mapped[int | None] = mapped_column(SmallInteger)
    flight_cycles: Mapped[int | None] = mapped_column(SmallInteger)
    avg_sortie_duration_hours: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    high_stress_sorties: Mapped[int | None] = mapped_column(SmallInteger)
    days_available: Mapped[int | None] = mapped_column(SmallInteger)
    days_unavailable: Mapped[int | None] = mapped_column(SmallInteger)

    aircraft_ref: Mapped[AircraftRef] = relationship(back_populates="flight_ops")


class TechnicalRecord(Base):
    __tablename__ = "technical_record"

    __table_args__ = (
        Index("ix_techrec_aircraft", "aircraft_ref_id", "event_date"),
        # partial: only ~60% of rows carry a fault_type, and it drives non-engine health
        Index("ix_techrec_fault", "fault_type",
              postgresql_where=text("fault_type IS NOT NULL")),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    record_id: Mapped[str] = mapped_column(String(12), unique=True)
    aircraft_ref_id: Mapped[int] = mapped_column(ForeignKey("aircraft_ref.id"))
    component_ref_id: Mapped[int | None] = mapped_column(ForeignKey("component_ref.id"))
    agency_ref_id: Mapped[str | None] = mapped_column(String(8))   # soft ref, see docs/05 §3.4
    part_ref_id: Mapped[str | None] = mapped_column(String(8))
    event_date: Mapped[date] = mapped_column(Date)
    maintenance_type: Mapped[str | None] = mapped_column(String(32))
    fault_type: Mapped[str | None] = mapped_column(String(40))
    action: Mapped[str | None] = mapped_column(Text)
    downtime_hours: Mapped[Decimal | None] = mapped_column(Numeric(7, 1))
    cost_inr: Mapped[int | None] = mapped_column()
    flight_hours_at_event: Mapped[Decimal | None] = mapped_column(Numeric(9, 1))
    cycles_at_event: Mapped[int | None] = mapped_column(Integer)
    outcome: Mapped[str | None] = mapped_column(String(16))

    snags: Mapped[list[Snag]] = relationship(back_populates="technical_record")


class Snag(Base):
    __tablename__ = "snag"

    __table_args__ = (
        Index("ix_snag_aircraft", "aircraft_ref_id", "date_reported"),
        Index("ix_snag_severity", "severity"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    snag_id: Mapped[str] = mapped_column(String(12), unique=True)
    technical_record_id: Mapped[int | None] = mapped_column(
        ForeignKey("technical_record.id", ondelete="SET NULL")
    )
    aircraft_ref_id: Mapped[int] = mapped_column(ForeignKey("aircraft_ref.id"))
    date_reported: Mapped[date] = mapped_column(Date)
    reported_by_role: Mapped[str | None] = mapped_column(String(16))
    snag_text: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(String(8))
    resolution_text: Mapped[str | None] = mapped_column(Text)

    technical_record: Mapped[TechnicalRecord | None] = relationship(back_populates="snags")


from .fleet import Aircraft  # noqa: E402
from .maintenance import Spare  # noqa: E402