// Server-backed fleet state.
//
// Everything the dashboard shows about health, RUL, spares, agencies and maintenance
// actions now comes from the API. Before this existed the browser computed all of it
// locally from a hardcoded wear curve, so the console rendered an invented fleet next
// to a real one: the model was predicting RUL into PostgreSQL while every panel showed
// `125 - cycle` arithmetic over a Math.random() seed.
//
// Three inputs feed this module:
//   * the fleet-wide read endpoints, polled on an interval (they are not pushed);
//   * `health.updated` / `cycle.tick` over the WebSocket, for the engine values that
//     change every 1.2 s, so the panels stay live between polls;
//   * `fetchEngineDetail` / `fetchPartDetail`, on demand for whatever is selected.

import { fleet, resolve, PART_CODES } from '../data/fleet.js';
import { emit, app } from './store.js';
import {
  fetchAircraftList,
  fetchAircraftDetail,
  fetchFleetSummary,
  fetchHeatmap,
  fetchFleetActions,
  fetchSchedule,
  fetchEngineDetail,
  fetchPartDetail,
} from '../lib/api.js';
import { clamp } from '../lib/math.js';

const HISTORY_MAX = 61;

// Fleet-wide reads do not change between ticks, so they are polled rather than
// pushed. 3 s is well inside the 1.2 s tick and keeps eight aircraft from turning
// into eight requests a second.
const POLL_MS = 3000;

let pollTimer = null;
let stopped = false;

// Part health keyed by the API's part codes, mapped onto the UI's `eng` key.
const asUiPart = (code) => (code === 'engine' ? 'eng' : code);

function blankDerived() {
  return {
    summary: null,
    heatmap: null,
    actions: [],
    schedule: [],
    fetchedAt: null,
  };
}

// Panels read fleet-wide aggregates from here rather than recomputing them over the
// local fleet, which is what produced numbers that disagreed with the API.
export const derived = blankDerived();

export function isServerBacked() {
  return derived.fetchedAt != null && apiReachable;
}

/* ── fleet-wide ─────────────────────────────────────────────────────────────── */

function applyAircraftList(body, issuedAt) {
  if (!body || !Array.isArray(body.items)) return;
  const seen = new Set();

  for (const item of body.items) {
    const e = resolve(item.code);
    if (!e) continue;                    // server knows an aircraft we have no slot for
    seen.add(e.id);
    e.serverId = item.id;
    e.name = item.name || item.code;
    e.tailNumber = item.tail_number;
    e.model = item.model;
    e.homeBase = item.home_base;
    e.missionReady = item.mission_ready;
    e.risk = item.risk;
    // Engine values change every 1.2s and arrive over the socket. A poll response can
    // land after a `health.updated` that was already newer — the request was issued
    // before the tick, the response arrives after it — and would otherwise roll the
    // display back to a stale cycle until the next tick. `issuedAt` is when this poll
    // went out, so anything the socket has applied since is strictly more recent.
    if (issuedAt == null || (e.wsEngineAt || 0) <= issuedAt) {
      if (item.rul != null) e.rul = item.rul;
      if (item.current_cycle != null) e.cycle = item.current_cycle;
      if (item.engine_health != null) e.engineHealth = item.engine_health;
    }
    if (item.parts) {
      for (const [code, risk] of Object.entries(item.parts)) {
        const k = asUiPart(code);
        if (k in e.partRisk) e.partRisk[k] = risk;
      }
    }
  }

  // An aircraft the server no longer lists must not keep displaying its last known
  // health as though it were current.
  for (const e of fleet) {
    if (seen.has(e.id)) continue;
    e.rul = null;
    e.engineHealth = null;
    for (const k of PART_CODES) e.partHealth[k] = null;
  }
}

function applyHeatmap(body) {
  if (!body || !Array.isArray(body.aircraft)) return;
  for (const row of body.aircraft) {
    const e = resolve(row.code);
    if (!e) continue;
    for (const cell of row.cells || []) {
      const k = asUiPart(cell.part);
      if (!(k in e.partHealth)) continue;
      if (cell.health != null) e.partHealth[k] = cell.health;
      if (cell.risk) e.partRisk[k] = cell.risk;
      if (cell.simulated != null) e.partSimulated[k] = cell.simulated;
    }
  }
}

function applyDetail(body) {
  if (!body) return;
  const e = resolve(body.code);
  if (!e) return;
  if (body.availability != null) e.availability = body.availability;
  for (const p of body.parts || []) {
    const k = asUiPart(p.part);
    if (!(k in e.partHealth)) continue;
    if (p.health != null) e.partHealth[k] = p.health;
    if (p.risk) e.partRisk[k] = p.risk;
    if (p.simulated != null) e.partSimulated[k] = p.simulated;
    if (p.rul != null) e.partRul[k] = p.rul;
    if (p.weakest_component) e.partWorstComponent[k] = p.weakest_component;
  }
}

function pushHistory(e, cycle, health) {
  if (cycle == null || health == null) return;
  const last = e.hist[e.hist.length - 1];
  // The replay revisits cycle numbers after wrapping, and the poll and the socket can
  // deliver the same cycle twice. Keyed on the last point so a repeat replaces rather
  // than appending a duplicate, which used to make the trend chart step sideways.
  if (last && last.cycle === cycle) {
    last.health = health;
    return;
  }
  e.hist.push({ cycle, health: clamp(health, 0, 1) });
  if (e.hist.length > HISTORY_MAX) e.hist.shift();
}

/**
 * Read every fleet-wide endpoint and fold it into the fleet entries.
 * @returns {Promise<boolean>} whether the API answered at all
 */
export async function refreshFleet() {
  // Stamped before the requests go out, not when they come back — the comparison in
  // applyAircraftList is against socket frames that arrived while this was in flight.
  const issuedAt = Date.now();
  const [list, heat, summary, actions, schedule] = await Promise.all([
    fetchAircraftList(),
    fetchHeatmap(),
    fetchFleetSummary(),
    fetchFleetActions(5),
    fetchSchedule(),
  ]);

  if (!list && !heat && !summary) return false;   // nothing came back; keep what we have

  applyAircraftList(list, issuedAt);
  applyHeatmap(heat);
  if (summary) {
    derived.summary = summary;
    if (summary.lowest_rul_aircraft) {
      const lo = resolve(summary.lowest_rul_aircraft.code);
      if (lo && summary.lowest_rul_aircraft.rul != null) lo.rul = summary.lowest_rul_aircraft.rul;
    }
  }
  derived.actions = actions?.items || [];
  derived.schedule = schedule?.items || [];
  derived.fetchedAt = Date.now();
  return true;
}

/* ── per-aircraft detail ────────────────────────────────────────────────────── */

let detailSeq = 0;

/**
 * GET /aircraft/{code} — per-part health, risk, RUL, weakest component, availability.
 *
 * Distinct from the engine endpoint: this one also carries the non-engine parts' own
 * RUL and worst component, which the heatmap does not include.
 */
export async function refreshAircraft(code) {
  const seq = ++detailSeq;
  const body = await fetchAircraftDetail(code);
  if (stopped || seq !== detailSeq) return;
  if (!body) return;
  applyDetail(body);
  emit();
}

export async function refreshEngine(code) {
  const e = resolve(code);
  if (!e) return;
  const seq = ++detailSeq;
  const body = await fetchEngineDetail(code, HISTORY_MAX - 1);
  if (stopped || seq !== detailSeq) return;    // a newer request superseded this one
  if (!body) return;

  e.cycle = body.current_cycle ?? e.cycle;
  if (body.rul != null) e.rul = body.rul;
  e.engineHealth = body.health ?? e.engineHealth;
  e.risk = body.risk ?? e.risk;
  e.missionReady = body.mission_ready ?? e.missionReady;
  e.weakestComponent = body.weakest_component;
  e.engineSpare = body.engine_spare;
  e.topSensors = body.top_sensors || [];
  e.componentSensorMap = body.component_sensor_map || {};
  e.modelInfo = body.model || null;
  if (body.components) {
    e.components = { ...body.components };
  }
  // Replace the trend with the server's own history rather than appending to it:
  // the server keeps a full per-cycle record, so there is no reason to reconstruct it.
  if (Array.isArray(body.history) && body.history.length) {
    e.hist = body.history
      .map((p) => ({ cycle: p.cycle, health: clamp(p.health, 0, 1) }))
      .slice(-HISTORY_MAX);
  }
  emit();
}

export async function refreshPart(code, part) {
  const e = resolve(code);
  if (!e) return null;
  const body = await fetchPartDetail(code, asUiPart(part) === 'eng' ? 'engine' : part);
  if (!body) return null;
  const k = asUiPart(body.part || part);
  if (body.health != null) e.partHealth[k] = body.health;
  if (body.risk) e.partRisk[k] = body.risk;
  e.partDetail = e.partDetail || {};
  e.partDetail[k] = body;
  emit();
  return body;
}

/* ── live engine values from the WebSocket ──────────────────────────────────── */

export function applyHealthEvent(payload) {
  const e = resolve(payload.aircraft);
  if (!e) return null;
  if (payload.health != null) {
    e.engineHealth = clamp(payload.health, 0, 1);
    pushHistory(e, payload.cycle, e.engineHealth);
  }
  if (payload.rul != null) e.rul = payload.rul;
  if (payload.risk) e.risk = payload.risk;
  if (payload.cycle != null) e.cycle = payload.cycle;
  // Marks these engine values as newer than any poll already in flight, so the response
  // of a request issued before this frame cannot overwrite them (see applyAircraftList).
  e.wsEngineAt = Date.now();
  return e;
}

/* ── lifecycle ──────────────────────────────────────────────────────────────── */

// Reachability of the read API, tracked independently of the WebSocket.
//
// These used to be the same flag. The consequence was that a WebSocket the server
// refused — an origin outside FDT_CORS_ORIGINS closes it with 4408, which is exactly
// what happens when the console is opened at http://127.0.0.1:8080 instead of
// http://localhost:8080 — left every read endpoint unpolled *and* started the offline
// fallback timer. The screen then showed a fabricated fleet with no indication that the
// real one was a single request away.
let apiReachable = false;

export function setApiReachable(ok) {
  if (apiReachable === ok) return;
  apiReachable = ok;
  app.apiReachable = ok;
  if (ok) app.onBackendChange?.();
}

export function startServerState() {
  stopped = false;

  const pump = async () => {
    if (stopped) return;
    try {
      if (await refreshFleet()) {
        setApiReachable(true);
        emit();
      } else if (apiReachable) {
        setApiReachable(false);
        emit();
      }
    } catch (err) {
      console.warn('[server-state] poll failed:', err);
    }
  };

  app.onBackendChange = pump;
  pollTimer = setInterval(pump, POLL_MS);
  pump();

  return () => {
    stopped = true;
    app.onBackendChange = null;
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = null;
  };
}
