"""Replay engine — one C-MAPSS cycle per aircraft every 1.2 s (docs/09 §1)."""
from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from ..core.config import Settings, get_settings
from ..core.logging import Timer
from ..db.session import get_sessionmaker
from ..domain.health import EmaState, engine_health_from_rul
from ..domain.rules import do_by_cycle, risk_level
from ..ml import inference
from ..models.alert import Alert
from ..models.fleet import Aircraft
from ..repositories import telemetry_repo as telemetry
from ..seed.cmapss import cmapss
from .bus import bus
from .events import alert_raised, cycle_tick, health_updated

log = logging.getLogger(__name__)

EMA: dict[int, EmaState] = {}


class ReplayEngine:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.task: asyncio.Task | None = None
        self.paused = False
        self.tick_count = 0
        self.running = False

    # ── lifecycle ─────────────────────────────────────────────────────────────
    async def start(self) -> None:
        if not self.settings.demo_mode or not cmapss.loaded:
            log.info("replay engine not started (demo_mode=%s, cmapss=%s)",
                     self.settings.demo_mode, cmapss.loaded)
            return
        self.running = True
        self.task = asyncio.create_task(self._loop(), name="replay")

    async def stop(self) -> None:
        self.running = False
        if self.task:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
            self.task = None

    async def tick_once(self) -> None:
        if not cmapss.loaded:
            log.warning("tick requested but C-MAPSS is not loaded — nothing to advance")
            return
        await self._tick()

    async def _loop(self) -> None:
        while self.running:
            if not self.paused:
                try:
                    await self._tick()
                except Exception:  # noqa: BLE001 — a bad tick must not kill the loop
                    log.exception("replay tick failed")
            await asyncio.sleep(self.settings.demo_tick_seconds)

    # ── one fleet pass ────────────────────────────────────────────────────────
    async def _tick(self) -> None:
        if not cmapss.loaded:
            return
        with Timer() as t:
            for aircraft in self._active():
                await self._advance(aircraft)
        self.tick_count += 1
        if self.tick_count % 50 == 0:
            log.info("replay tick %d in %sms", self.tick_count, t.ms)

    def _active(self) -> list[Aircraft]:
        session = get_sessionmaker(self.settings)
        with session() as db:
            return list(db.scalars(select(Aircraft).where(Aircraft.is_active.is_(True))))

    def _resume_cycle(self, unit: Any) -> int:
        """Where a wrapped engine restarts, so the feature window is already full.

        Wrapping to cycle 1 gave `cmapss.window` a single row, and `build_window`
        rejects anything under five cycles — so the first four ticks after every wrap
        were answered from `rul = 125 - cycle`. That is not a rounding artefact: the
        model said 118 at cycle 5 while the fallback had just been claiming 121, and
        the degradation was invisible because a healthy engine's linear guess is
        roughly right. Each wrap therefore produced a visible step *and* a silent
        fallback, once per engine lifetime.

        Restarting at `ml_window` skips the first 29 cycles of an engine's life —
        the least informative ones, all healthy and well above the RUL cap — and
        buys a complete window, so every prediction is model-backed. The unit is
        still walked to genuine end of life before wrapping.
        """
        target = max(1, self.settings.ml_window)
        if cmapss.row(unit, target) is not None:
            return target
        # Pathological unit shorter than the window; fall back to the old behaviour
        # and let build_window report the short window rather than inventing data.
        return 1

    async def _advance(self, aircraft: Aircraft) -> None:
        unit = aircraft.cmapss_unit_id
        next_cycle = aircraft.current_cycle + 1
        row = cmapss.row(unit, next_cycle)

        if row is None:                                   # wrap at end of life
            next_cycle = self._resume_cycle(unit)
            row = cmapss.row(unit, next_cycle)
            aircraft.current_cycle = next_cycle
            EMA[aircraft.id] = EmaState.empty()          # reset smoothing (docs/09 §1.2)
            log.info("%s wrapped to cycle %d", aircraft.code, next_cycle)

        window = cmapss.window(unit, next_cycle, self.settings.ml_window)
        prediction = await asyncio.to_thread(inference.predict, window, current_cycle=next_cycle)

        raw_health = engine_health_from_rul(prediction["rul"])
        state = EMA.setdefault(aircraft.id, EmaState.empty())
        health = round(state.update("engine", raw_health), 3)
        risk = risk_level(health)

        previous_risk = aircraft.risk_level
        now = datetime.now(UTC)

        session = get_sessionmaker(self.settings)
        with session() as db:                              # ONE transaction per aircraft-tick
            row_obj = db.get(Aircraft, aircraft.id)
            if row_obj is None:  # aircraft deleted mid-replay
                log.warning("aircraft %s vanished, skipping tick", aircraft.code)
                return
            engine_part = telemetry.engine_part(db)

            telemetry.write_engine_prediction(
                db,
                aircraft=row_obj,
                part=engine_part,
                prediction=prediction,
                health=health,
                risk=risk,
                cycle=next_cycle,
                do_by=do_by_cycle(prediction["rul"], next_cycle, risk),
                source="cmapss",
                cmapss_unit_id=unit,
                telemetry_rows=[{"cycle": next_cycle, **row}],
                recorded_at=now,
            )
            row_obj.updated_at = now
            db.commit()

        await bus.publish(cycle_tick(next_cycle))
        await bus.publish(health_updated(
            aircraft.code, aircraft.id, "engine", health, risk, prediction["rul"],
            next_cycle, model=prediction.get("model"),
        ))

        if risk != previous_risk and risk != "healthy":
            await self._raise_alert(aircraft, risk, health, prediction["rul"],
                                    next_cycle, now)

    async def _raise_alert(self, aircraft: Aircraft, risk: str, health: float,
                           rul: int, cycle: int, now: datetime) -> None:
        """One live alert per aircraft+part (uq_alert_live)."""
        session = get_sessionmaker(self.settings)
        with session() as db:
            part = telemetry.engine_part(db)
            existing = db.scalar(
                select(Alert).where(
                    Alert.aircraft_id == aircraft.id,
                    Alert.part_id == part.id,
                    Alert.acknowledged.is_(False),
                )
            )
            message = (
                f"Engine health {health:.2f} — RUL {rul}. "
                f"{'Replace now' if risk == 'critical' else 'Plan inspection'}."
            )
            if existing:
                existing.level = risk
                existing.message = message
                existing.health = health
                existing.cycle = cycle
            else:
                existing = Alert(
                    aircraft_id=aircraft.id, part_id=part.id, level=risk,
                    message=message, cycle=cycle, health=health, created_at=now,
                )
                db.add(existing)
            db.commit()
            payload = {
                "id": existing.id, "aircraft": aircraft.code, "aircraft_id": aircraft.id,
                "part": "engine", "level": risk, "message": message,
                "health": health, "rul": rul, "cycle": cycle,
            }
        await bus.publish(alert_raised(payload))

    # ── controls ──────────────────────────────────────────────────────────────
    def status(self) -> dict:
        return {
            "running": self.running,
            "paused": self.paused,
            "tick": self.tick_count,
            "interval_seconds": self.settings.demo_tick_seconds,
            **cmapss.describe(),
            "subscribers": bus.subscriber_count,
            "dropped_events": bus.dropped,
        }

    def pause(self) -> None:
        self.paused = True

    def resume(self) -> None:
        self.paused = False


engine = ReplayEngine()


async def start_replay() -> None:
    await engine.start()


async def stop_replay() -> None:
    await engine.stop()


