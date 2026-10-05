"""Maintenance tables — agencies, spares, work orders, bookings, stock, alerts."""
from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import ENUM
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db.session import Base
from .auth import User
from .fleet import Aircraft, Part


class WorkOrderStatus(StrEnum):
    open = "open"
    in_progress = "in_progress"
    done = "done"


class PriorityLevel(StrEnum):
    low = "low"
    medium = "medium"
    high = "high"


class StockReason(StrEnum):
    reserve = "reserve"
    restock = "restock"
    adjust = "adjust"
    returned = "return"


work_order_status_enum = ENUM("open", "in_progress", "done", name="work_order_status")
priority_enum = ENUM("low", "medium", "high", name="priority_level")
stock_reason_enum = ENUM("reserve", "restock", "adjust", "return", name="stock_reason")


class Agency(Base):
    __tablename__ = "agency"
    __table_args__ = (
        CheckConstraint("turnaround_days > 0", name="agency_turn"),
        CheckConstraint("monthly_capacity_slots > 0", name="agency_cap"),
        Index("ix_agency_specialisation", "specialisation"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    agency_ref_id: Mapped[str] = mapped_column(String(8), unique=True)
    name: Mapped[str] = mapped_column(String(96))
    type: Mapped[str | None] = mapped_column(String(32))
    location: Mapped[str | None] = mapped_column(String(32))
    specialisation: Mapped[str] = mapped_column(String(24))
    turnaround_days: Mapped[int] = mapped_column(SmallInteger)
    monthly_capacity_slots: Mapped[int] = mapped_column(SmallInteger)
    cost_multiplier: Mapped[float] = mapped_column(Numeric(3, 2), default=1.0)
    free_slot_days: Mapped[int] = mapped_column(SmallInteger, default=1)

    parts: Mapped[list[Part]] = relationship(back_populates="agency")
    assignments: Mapped[list[AircraftPart]] = relationship(back_populates="agency")
    bookings: Mapped[list[AgencyBooking]] = relationship(back_populates="agency")


class Spare(Base):
    __tablename__ = "spare"
    __table_args__ = (
        CheckConstraint("stock >= 0", name="spare_stock"),
        Index("ix_spare_component_name", "component_name"),
        Index("ix_spare_low_stock", "criticality",
              postgresql_where=text("stock <= minimum_stock")),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    part_ref_id: Mapped[str] = mapped_column(String(8), unique=True)
    item_name: Mapped[str] = mapped_column(String(96))
    component_ref_id: Mapped[int | None] = mapped_column(ForeignKey("component_ref.id"))
    component_name: Mapped[str | None] = mapped_column(String(48))
    aircraft_model: Mapped[str | None] = mapped_column(String(16))
    stock: Mapped[int] = mapped_column(Integer, default=0)
    minimum_stock: Mapped[int] = mapped_column(Integer, default=0)
    reorder_quantity: Mapped[int] = mapped_column(Integer, default=0)
    supplier: Mapped[str | None] = mapped_column(String(64))
    lead_time_days: Mapped[int] = mapped_column(SmallInteger)
    unit_cost_inr: Mapped[int | None] = mapped_column(Integer)
    storage_location: Mapped[str | None] = mapped_column(String(32))
    last_restock_date: Mapped[date | None] = mapped_column(Date)
    criticality: Mapped[str | None] = mapped_column(String(8))

    component_ref: Mapped[ComponentRef | None] = relationship(back_populates="spares")
    movements: Mapped[list[StockMovement]] = relationship(back_populates="spare")
    aircraft_part: Mapped[list[AircraftPart]] = relationship(back_populates="spare")

    @property
    def in_stock(self) -> bool:
        return self.stock > 0


class WorkOrder(Base):
    __tablename__ = "work_order"
    __table_args__ = (
        CheckConstraint("status <> 'done' OR completed_at IS NOT NULL", name="wo_completed"),
        # spec 35 permits one open order per aircraft+part — enforced, not convention
        Index("uq_wo_open_aircraft_part", "aircraft_id", "part_id",
              unique=True, postgresql_where=text("status <> 'done'")),
        Index("ix_wo_aircraft", "aircraft_id", "status"),
        Index("ix_wo_open_due", "due_date", postgresql_where=text("status <> 'done'")),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    reference: Mapped[str] = mapped_column(String(16), unique=True)
    aircraft_id: Mapped[int] = mapped_column(ForeignKey("aircraft.id", ondelete="CASCADE"))
    part_id: Mapped[int] = mapped_column(ForeignKey("part.id"))
    action: Mapped[str] = mapped_column(String(48))
    due_date: Mapped[date] = mapped_column(Date)
    due_cycle: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(work_order_status_enum, default="open")
    priority: Mapped[str] = mapped_column(priority_enum, default="medium")
    source_ref: Mapped[str | None] = mapped_column(String(12))
    notes: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.utcnow())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.utcnow(), onupdate=lambda: datetime.utcnow())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    aircraft: Mapped[Aircraft] = relationship(back_populates="work_orders")
    part: Mapped[Part] = relationship(back_populates="work_orders")
    created_by_user: Mapped[User | None] = relationship(back_populates="work_orders")
    bookings: Mapped[list[AgencyBooking]] = relationship(back_populates="work_order")
    movements: Mapped[list[StockMovement]] = relationship(back_populates="work_order")


class AgencyBooking(Base):
    __tablename__ = "agency_booking"

    __table_args__ = (
        Index("ix_booking_agency", "agency_id", "eta_date"),
        Index("ix_booking_open", "eta_date",
              postgresql_where=text("completed_at IS NULL")),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    agency_id: Mapped[int] = mapped_column(ForeignKey("agency.id"))
    aircraft_id: Mapped[int] = mapped_column(ForeignKey("aircraft.id", ondelete="CASCADE"))
    part_id: Mapped[int] = mapped_column(ForeignKey("part.id"))
    work_order_id: Mapped[int | None] = mapped_column(
        ForeignKey("work_order.id", ondelete="SET NULL")
    )
    booked_on: Mapped[date] = mapped_column(Date, default=lambda: datetime.utcnow().date())
    slot_days: Mapped[int] = mapped_column(Integer)
    turnaround_days: Mapped[int] = mapped_column(Integer)
    lead_time_days: Mapped[int] = mapped_column(Integer, default=0)
    eta_date: Mapped[date] = mapped_column(Date)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.utcnow())

    agency: Mapped[Agency] = relationship(back_populates="bookings")
    aircraft: Mapped[Aircraft] = relationship(back_populates="bookings")
    part: Mapped[Part] = relationship(back_populates="bookings")
    work_order: Mapped[WorkOrder | None] = relationship(back_populates="bookings")
    created_by_user: Mapped[User | None] = relationship()


class StockMovement(Base):
    __tablename__ = "stock_movement"
    __table_args__ = (
        CheckConstraint("delta <> 0", name="stock_delta"),
        Index("ix_stock_spare", "spare_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    spare_id: Mapped[int] = mapped_column(ForeignKey("spare.id", ondelete="CASCADE"))
    delta: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(stock_reason_enum)
    work_order_id: Mapped[int | None] = mapped_column(
        ForeignKey("work_order.id", ondelete="SET NULL")
    )
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.utcnow())

    spare: Mapped[Spare] = relationship(back_populates="movements")
    work_order: Mapped[WorkOrder | None] = relationship(back_populates="movements")
    user: Mapped[User | None] = relationship()


from .fleet import AircraftPart  # noqa: E402
from .reference import ComponentRef  # noqa: E402