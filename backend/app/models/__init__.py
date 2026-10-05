"""Import every model so Alembic autogenerate sees the full metadata."""
from .alert import Alert
from .auth import AuditLog, User, UserRole
from .fleet import Aircraft, AircraftPart, Part, RiskLevel
from .maintenance import (
    Agency,
    AgencyBooking,
    PriorityLevel,
    Spare,
    StockMovement,
    StockReason,
    WorkOrder,
    WorkOrderStatus,
)
from .reference import AircraftRef, ComponentRef, FlightOpsMonthly, Snag, TechnicalRecord
from .telemetry import (
    SENSOR_COLUMNS,
    ComponentHealth,
    EngineTelemetry,
    HealthSnapshot,
    MlPrediction,
)

__all__ = [
    "Alert", "AuditLog", "User", "UserRole",
    "Aircraft", "AircraftPart", "Part", "RiskLevel",
    "Agency", "AgencyBooking", "PriorityLevel", "Spare", "StockMovement",
    "StockReason", "WorkOrder", "WorkOrderStatus",
    "AircraftRef", "ComponentRef", "FlightOpsMonthly", "Snag", "TechnicalRecord",
    "SENSOR_COLUMNS", "ComponentHealth", "EngineTelemetry", "HealthSnapshot", "MlPrediction",
]