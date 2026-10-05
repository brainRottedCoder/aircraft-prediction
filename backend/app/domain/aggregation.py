"""Aggregation helpers — series, matrices, rollups. Pure."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from .rules import PART_ORDER


def fleet_series(
    snapshots: dict[int, list[dict[str, Any]]],
    window: int,
    weakest_aircraft: int | None,
) -> dict[str, list[dict[str, Any]]]:
    """Two aligned series over the same cycles (docs/06 §2 /fleet/summary).

    snapshots: aircraft_id -> [{cycle, health, part}, …] oldest first
    """
    cycles: set[int] = set()
    for rows in snapshots.values():
        cycles.update(r["cycle"] for r in rows[-window:])
    ordered = sorted(cycles)[-window:]

    by_aircraft_cycle: dict[int, dict[int, float]] = defaultdict(dict)
    for aircraft_id, rows in snapshots.items():
        for r in rows:
            if r["cycle"] in ordered:
                by_aircraft_cycle[aircraft_id][r["cycle"]] = r["health"]

    fleet_avg = []
    weakest = []
    for cycle in ordered:
        values = [m[cycle] for m in by_aircraft_cycle.values() if cycle in m]
        fleet_avg.append(
            {"cycle": cycle, "value": round(sum(values) / len(values), 3) if values else 0.0}
        )
        wm = by_aircraft_cycle.get(weakest_aircraft or -1, {})
        weakest.append({"cycle": cycle, "value": round(wm.get(cycle, 0.0), 3)})
    series: dict[str, Any] = {"fleet_avg_health": fleet_avg,
                               "weakest_aircraft_health": weakest}
    return {"window": len(ordered), **series}


def heatmap_matrix(parts: list[dict[str, Any]]) -> dict[str, Any]:
    """aircraft × part cells, always 5 parts per aircraft."""
    by_part = {p["part"]: p for p in parts}
    cells = [
        {
            "part": code,
            "health": by_part[code]["health"] if code in by_part else None,
            "risk": by_part[code]["risk"] if code in by_part else None,
            "simulated": by_part[code].get("simulated") if code in by_part else None,
        }
        for code in PART_ORDER
    ]
    return {"parts": list(PART_ORDER), "cells": cells,
            "legend": {"healthy": "> 0.70", "watch": "0.40 - 0.70", "critical": "<= 0.40"}}


def rank_worst_parts(parts: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
    """Globally worst parts across the fleet, ascending by health."""
    ordered = sorted(parts, key=lambda p: (p["health"], PART_ORDER.index(p["part"])))
    out = []
    for rank, p in enumerate(ordered[:limit], start=1):
        out.append({"rank": rank, **p})
    return out


def rollup(aircraft: list[dict[str, Any]], parts: list[dict[str, Any]]) -> dict[str, Any]:
    """Fleet summary counters (docs/06 §2)."""
    risks: defaultdict[str, int] = defaultdict(int)
    for p in parts:
        risks[p["risk"]] += 1
    ruls = [a["rul"] for a in aircraft]
    lowest = min(aircraft, key=lambda a: a["rul"], default=None)
    return {
        "mission_ready_count": sum(1 for a in aircraft if a["mission_ready"]),
        "total_aircraft": len(aircraft),
        "critical_parts": risks["critical"],
        "average_rul": round(sum(ruls) / len(ruls), 1) if ruls else 0.0,
        "lowest_rul_aircraft": (
            {"id": lowest["id"], "code": lowest["code"], "rul": lowest["rul"],
             "name": lowest["name"]}
            if lowest
            else None
        ),
        "risk_breakdown": {k: risks.get(k, 0) for k in ("healthy", "watch", "critical")},
    }


