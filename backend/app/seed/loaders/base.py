"""CSV loading helpers — generic, idempotent upsert on a natural key."""
from __future__ import annotations

import csv
from collections.abc import Callable, Iterable, Sequence
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...core.config import get_settings

Mapper = dict[str, Callable[[Any], Any]]


def rename(rows: Iterable[dict], mapping: dict[str, str]) -> list[dict]:
    """Rename source column names to model column names (e.g. agency_id -> agency_ref_id)."""
    out: list[dict] = []
    for row in rows:
        new = dict(row)
        for src, dst in mapping.items():
            if src in new:
                new[dst] = new.pop(src)
        out.append(new)
    return out


def read_csv(name: str) -> list[dict[str, str]]:
    path: Path = get_settings().raw_dir / name
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


# ── scalar converters ──────────────────────────────────────────────────────────
def _int(v: Any) -> int | None:
    if v is None or v == "":
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _decimal(v: Any) -> Decimal | None:
    f = _float(v)
    return Decimal(str(f)) if f is not None else None


def _date(v: Any) -> date | None:
    if not v:
        return None
    try:
        return datetime.strptime(v, "%Y-%m-%d").date()
    except ValueError:
        return None


def _text(v: Any) -> Any:
    """Empty string means absent, except where '' is meaningful."""
    return v if v not in (None, "") else None


def _identity(v: Any) -> Any:
    return v


# ── generic upsert ─────────────────────────────────────────────────────────────
def upsert_all(
    db: Session,
    model: type,
    rows: Iterable[dict],
    keys: Sequence[str],
    mapper: Mapper,
) -> int:
    """Insert-or-update `rows` keyed on `keys` (model column names).

    Existing rows are fetched once, so the query count is 1 + 1 regardless of row
    count — re-running the seed never duplicates and never grows query load.
    """
    rows = list(rows)
    if not rows:
        return 0

    key_set = set(keys)
    missing = key_set - set(mapper)
    if missing:
        raise ValueError(f"{model.__name__}: mapper must include key columns {missing}")

    existing: dict[tuple, Any] = {}
    if len(key_set) == 1:
        only = next(iter(key_set))
        for obj in db.scalars(select(model)):
            existing[(getattr(obj, only),)] = obj
    else:
        for obj in db.scalars(select(model)):
            existing[tuple(getattr(obj, k) for k in key_set)] = obj

    count = 0
    for row in rows:
        identity = tuple(_apply(row.get(k), mapper.get(k, _identity)) for k in key_set)
        values = {col: _apply(row.get(col), fn) for col, fn in mapper.items()}
        obj = existing.get(identity)
        if obj is None:
            obj = model(**values)      # mapper includes the key columns
            db.add(obj)
            existing[identity] = obj
        else:
            for column, value in values.items():
                setattr(obj, column, value)
        count += 1

    db.flush()
    return count


def _apply(raw: Any, fn: Callable[[Any], Any]) -> Any:
    try:
        return fn(raw)
    except (TypeError, ValueError):
        return None


__all__ = [
    "read_csv", "rename", "upsert_all", "_int", "_float", "_decimal", "_date", "_text", "_identity",
]