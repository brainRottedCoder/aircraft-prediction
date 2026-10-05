"""Domain exceptions and their HTTP mapping (docs/02 §6.2)."""
from __future__ import annotations

from typing import Any


class DomainError(Exception):
    status_code = 400
    code = "DOMAIN_ERROR"

    def __init__(self, message: str, detail: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail or {}


class NotFoundError(DomainError):
    status_code = 404
    code = "NOT_FOUND"


class ConflictError(DomainError):
    status_code = 409
    code = "CONFLICT"


class BusinessRuleError(DomainError):
    status_code = 422
    code = "BUSINESS_RULE_VIOLATION"


class ForbiddenError(DomainError):
    status_code = 403
    code = "FORBIDDEN"


class AuthenticationError(DomainError):
    status_code = 401
    code = "UNAUTHORIZED"


class ModelUnavailableError(DomainError):
    status_code = 503
    code = "MODEL_UNAVAILABLE"


class OutOfStockError(ConflictError):
    code = "OUT_OF_STOCK"


class NoSlotAvailableError(ConflictError):
    code = "NO_SLOT_AVAILABLE"


class DuplicateWorkOrderError(ConflictError):
    code = "DUPLICATE_WORK_ORDER"