// The fleet as the UI sees it: one entry per aircraft, in a stable display order,
// with every health number left null until the backend supplies it.
//
// This file used to invent the fleet outright — a hardcoded `d0` wear array, plus
// per-aircraft `bias`/`off` offsets built from sin/cos and a 61-point history seeded
// with Math.random(). Every panel then derived health, RUL, spares and agencies from
// that, so the dashboard showed a plausible-looking fleet that had no relationship to
// the model, the replayed C-MAPSS telemetry or the database. The array is now only a
// skeleton: `state/server.js` fills these fields from /api/v1, and the WebSocket
// updates the engine values between polls.

export const FLEET_CODES = [
  'Fighter-01', 'Fighter-02', 'Fighter-03', 'Fighter-04',
  'Fighter-05', 'Fighter-06', 'Fighter-07', 'Fighter-08',
];

export const PART_CODES = ['eng', 'radar', 'gear', 'hyd', 'fuel'];

// The API calls the engine part "engine"; the UI has always called it "eng" (PARTS is
// keyed by the 3D model names). Every per-part map here is keyed by the UI name, so the
// translation happens once, in state/server.js. Getting this wrong is silent: the map
// simply gains an unused "engine" key and the engine cell renders as unknown forever.
const blankParts = () => ({ eng: null, radar: null, gear: null, hyd: null, fuel: null });
const blankFlags = (v) => ({ eng: v, radar: v, gear: v, hyd: v, fuel: v });

const entry = (code, idx) => ({
  id: code,
  idx,
  serverId: null,

  // ── from GET /aircraft ──────────────────────────────────────────────────────
  name: code,
  tailNumber: null,
  model: null,
  homeBase: null,
  rul: null,            // engine remaining useful life, cycles
  cycle: null,          // current replayed cycle
  risk: null,           // engine risk band from the API
  missionReady: null,   // the API's readiness verdict, not a local recomputation
  engineHealth: null,

  // ── from GET /fleet/heatmap and GET /aircraft/{code} ────────────────────────
  partHealth: blankParts(),
  partRisk: blankParts(),
  partSimulated: blankFlags(false),
  partRul: blankParts(),
  partWorstComponent: blankParts(),
  partDetail: {},
  availability: null,

  // ── from GET /aircraft/{code}/engine ────────────────────────────────────────
  components: { fan: null, hpc: null, hpt: null, lpt: null },
  weakestComponent: null,
  engineSpare: null,
  topSensors: [],
  componentSensorMap: {},
  modelInfo: null,

  hist: [],             // engine health per cycle, oldest first
});

export const fleet = FLEET_CODES.map(entry);

// Index lookups. The replay seeds the eight aircraft in this order, but nothing should
// depend on that: `resolve` re-links entries to server rows by `code` whenever the
// server disagrees, so a different seed order or a partially-seeded fleet still lines
// up.
export function resolve(code) {
  return fleet.find((e) => e.id === code) || null;
}
