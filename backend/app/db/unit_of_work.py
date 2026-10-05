"""Unit of work — every mutation runs inside this context and is audited.

Guarantees (docs/02 §5.2, spec 51):
  * the entity row and its audit_log row commit or roll back together
  * pessimistic row locks for stock and agency slots
  * `as_of()` gives handlers the timestamp to stamp on denormalised columns
"""
from __future__ import annotations

import enum
from datetime import UTC, date, datetime
from decimal import Decimal
from types import TracebackType
from typing import Any

from sqlalchemy.orm import Session

from ..core.logging import request_id_var


def jsonable(value: Any) -> Any:
    """Coerce a snapshot into something JSONB accepts.

    Audit rows capture ORM-adjacent dicts that may carry date, datetime or enum
    instances; JSONB stores none of those.
    """
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items() if v is not None}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, Decimal):
        return float(value)
    return value


class UnitOfWork:
    def __init__(self, session: Session) -> None:
        self.session = session
        self._now: datetime | None = None

    def __enter__(self) -> UnitOfWork:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if exc_type is not None:
            self.session.rollback()
        else:
            self.session.commit()

    # ── helpers ────────────────────────────────────────────────────────────────
    def as_of(self) -> datetime:
        """Single timestamp per unit of work, so all rows agree."""
        if self._now is None:
            self._now = datetime.now(UTC)
        return self._now

    def flush(self) -> None:
        self.session.flush()

    def audit(
        self,
        *,
        entity: str,
        entity_id: str | int,
        action: str,
        actor_name: str,
        actor_id: int | None = None,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
    ) -> None:
        from ..models.auth import AuditLog

        self.session.add(
            AuditLog(
                entity=entity,
                entity_id=str(entity_id),
                action=action,
                actor_id=actor_id,
                actor_name=actor_name,
                at=self.as_of(),
                before=jsonable(before),
                after=jsonable(after),
                request_id=request_id_var.get(),
            )
        )

    def lock(self, model: type, pk: Any) -> Any | None:
        """SELECT ... FOR UPDATE — used by spare reserve and agency booking."""
        return (
            self.session.query(model)
            .filter(model.id == pk)
            .with_for_update()
            .one_or_none()
        )