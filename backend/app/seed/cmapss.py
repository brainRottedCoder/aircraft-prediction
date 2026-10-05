"""C-MAPSS index — the raw cycle rows held in memory as dicts (docs/09 §1.1).

One subset is replayed; several may be staged. `replay` is the subset the fleet reads
and is what the model is asked about at inference time. The others are loaded so the
baselines artifact's four-subset layout resolves without a refetch, and so switching
replay subsets is a config change rather than a code change.

Unit ids are namespaced per subset (`FD001#7`) because every subset numbers its engines
from 1: without the prefix, FD002#7 and FD001#7 collide in one dict and the fleet would
silently replay the wrong engine.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np

from ..core.config import Settings, get_settings

log = logging.getLogger(__name__)

COLUMNS = (
    ["unit_id", "cycle", "setting_1", "setting_2", "setting_3"]
    + [f"sensor_{i}" for i in range(1, 22)]
)
SENSOR_KEYS = {
    "sensor_2": "s2", "sensor_3": "s3", "sensor_4": "s4", "sensor_6": "s6",
    "sensor_7": "s7", "sensor_8": "s8", "sensor_9": "s9", "sensor_11": "s11",
    "sensor_12": "s12", "sensor_13": "s13", "sensor_14": "s14", "sensor_15": "s15",
    "sensor_17": "s17", "sensor_20": "s20", "sensor_21": "s21",
}
UNIT_SEP = "#"
KNOWN_SUBSETS = ("FD001", "FD002", "FD003", "FD004")


def namespaced(subset: str, unit: int) -> str:
    return f"{subset}{UNIT_SEP}{unit}"


def split_unit(key: str) -> tuple[str | None, int | None]:
    """Inverse of `namespaced`. Returns (None, None) for a bare integer unit id."""
    if UNIT_SEP not in str(key):
        return None, None
    subset, _, unit = str(key).partition(UNIT_SEP)
    try:
        return subset, int(unit)
    except ValueError:
        return subset, None


class CMapss:
    """In-memory C-MAPSS rows, keyed `subset -> unit -> cycle -> row`."""

    def __init__(self) -> None:
        self.subsets: dict[str, dict[int, dict[int, dict]]] = {}
        self.rul_labels: dict[str, list[float]] = {}
        self.replay: str | None = None
        self.loaded = False

    # ── loading ───────────────────────────────────────────────────────────────
    def load(self, settings: Settings | None = None) -> CMapss:
        s = settings or get_settings()
        self.replay = s.replay_subset
        for subset in KNOWN_SUBSETS:
            path = s.cmapss_file(subset, "train")
            if not path.exists():
                continue
            rows = _read_train(path, subset)
            if rows:
                self.subsets[subset] = rows
            labels = _read_rul(s.cmapss_file(subset, "RUL"), subset)
            if labels is not None:
                self.rul_labels[subset] = labels

        replayed = self.subsets.get(self.replay or "")
        if not replayed:
            log.warning(
                "no C-MAPSS training file for replay subset %s in %s — replay disabled",
                self.replay, s.cmapss_dir,
            )
            return self

        self.loaded = True
        log.info(
            "C-MAPSS loaded: %s (%d units, replayed) | staged %s",
            self.replay, len(replayed),
            ", ".join(f"{k}={len(v)}" for k, v in sorted(self.subsets.items())) or "none",
        )
        return self

    # ── access ────────────────────────────────────────────────────────────────
    @property
    def rows(self) -> dict[int, dict[int, dict]]:
        """Units of the replayed subset, under their bare ids. What replay walks."""
        return self.subsets.get(self.replay or "", {})

    @property
    def units(self) -> list[int]:
        return sorted(self.rows)

    def row(self, unit_id: int | str, cycle: int) -> dict | None:
        subset, unit = split_unit(unit_id)
        subset = subset or self.replay or ""
        unit = int(unit) if unit is not None else int(unit_id)
        return self.subsets.get(subset, {}).get(unit, {}).get(cycle)

    def window(self, unit_id: int | str, end_cycle: int, size: int = 30) -> list[dict]:
        """Trailing `size` rows up to and including end_cycle."""
        row = self.row(unit_id, end_cycle)
        subset, unit = split_unit(unit_id)
        subset = subset or self.replay or ""
        unit = int(unit) if unit is not None else int(unit_id)
        engine = self.subsets.get(subset, {}).get(unit, {})
        out = [engine[c] for c in range(max(1, end_cycle - size + 1), end_cycle + 1)
               if c in engine]
        return out or ([row] if row else [])

    def describe(self) -> dict[str, Any]:
        return {
            "cmapss_loaded": self.loaded,
            "replay_subset": self.replay,
            "units": len(self.units),
            "subsets": {k: len(v) for k, v in sorted(self.subsets.items())},
            "rul_labels": sorted(self.rul_labels),
        }


def _read_train(path: Path, subset: str) -> dict[int, dict[int, dict]]:
    # reshape: loadtxt drops the row axis for a single-row file, and FD004 has
    # engine 1 alone in small extracts.
    data = np.loadtxt(path).reshape(-1, len(COLUMNS))
    units: dict[int, dict[int, dict]] = {}
    for raw in data:
        unit, cycle = int(raw[0]), int(raw[1])
        units.setdefault(unit, {})[cycle] = {
            "unit_id": unit,
            "unit_key": namespaced(subset, unit),
            "subset": subset,
            "cycle": cycle,
            "settings": {
                "setting_1": float(raw[2]),
                "setting_2": float(raw[3]),
                "setting_3": float(raw[4]),
            },
            "sensors": {
                alias: float(raw[COLUMNS.index(src)])
                for src, alias in SENSOR_KEYS.items()
            },
        }
    return units


def _read_rul(path: Path, subset: str) -> list[float] | None:
    """Official test-set labels, one per engine, in unit order."""
    if not path.exists():
        return None
    try:
        return [float(x) for x in np.loadtxt(path).reshape(-1)]
    except Exception as exc:  # noqa: BLE001 — a bad label file must not stop startup
        log.warning("RUL_%s.txt unreadable (%s)", subset, exc)
        return None


cmapss = CMapss()