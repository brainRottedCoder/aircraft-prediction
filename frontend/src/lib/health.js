// Health accessors.
//
// These used to be a local model of their own: a hardcoded wear curve, per-module
// degradation weights, sine/cosine offsets and hardcoded spares and agencies, with the
// server's RUL consulted only when the forecast was at zero. The result was a second,
// disagreeing source of truth next to the ML model. Now every value comes from the
// API, and the local arithmetic survives only as an explicit offline fallback so a
// dropped backend degrades visibly (the navbar says "Simulated") rather than showing
// stale numbers as though they were live.

import { clamp } from './math.js';
import { ENG, PARTS, KEYS } from '../data/parts.js';
import { fleet } from '../data/fleet.js';
import { app } from '../state/store.js';
import { derived } from '../state/server.js';

/* ── offline fallback ───────────────────────────────────────────────────────── */

// Only used while the backend is unreachable, so the console still moves. Never mixed
// with server values: `usingFallback` gates it per aircraft.
const OFFLINE = [0.15, 0.35, 0.55, 0.8, 0.92, 0.25, 0.7, 0.1];

const offlineWear = (e) => {
  if (e._offlineWear == null) e._offlineWear = OFFLINE[e.idx % OFFLINE.length];
  return e._offlineWear;
};

export const usingFallback = (e) => e.rul == null;

/* ── engine ─────────────────────────────────────────────────────────────────── */

// The forecast control has no server equivalent: the API reports the cycle it is on,
// not a projection. Rather than quietly reusing a fabricated curve, the offset is
// applied to RUL as a straight-line projection and labelled as such in the tooltips.
export const fcCycles = () => app.fc;

export const rul = (e) => {
  if (usingFallback(e)) return Math.round(125 * (1 - clamp(offlineWear(e) + app.fc * 0.0045, 0, 0.99)));
  return Math.max(0, e.rul - app.fc);
};

// Engine module health straight from the model's component_health, z-scored per regime.
export const comp = (e, c) => {
  const v = e.components?.[c];
  if (v != null) return clamp(v, 0, 1);
  if (usingFallback(e)) return clamp(1 - offlineWear(e) * 1.1, 0, 1);
  return null;
};

export const weakestModule = (e) => {
  const known = Object.entries(e.components || {}).filter(([, v]) => v != null);
  if (known.length) return known.reduce((a, b) => (b[1] < a[1] ? b[0] : a[0]))[0];
  return e.weakestComponent || 'fan';
};

/* ── parts ──────────────────────────────────────────────────────────────────── */

// Top-level part health, per part, from /fleet/heatmap and /aircraft/{code}.
export const ph = (e, k) => {
  const v = e.partHealth?.[k];
  if (v != null) return clamp(v, 0, 1);
  if (usingFallback(e)) {
    const base = clamp(offlineWear(e), 0, 0.99);
    return k === 'eng' ? clamp(1 - base) : clamp(1 - base * (PARTS[k]?.f ?? 0.5));
  }
  return null;
};

export const partRisk = (e, k) => e.partRisk?.[k] || null;

// Health of the three sub-components of a subsystem. The API reports component health
// for the engine only, so the non-engine breakdown stays derived — from the part's own
// health, not from an independent wear curve.
const SV = [[-0.22, 0.1, 0.06], [0.08, -0.24, 0.1], [0.1, 0.08, -0.26]];
export const subH = (e, k, i) => {
  const h = ph(e, k);
  if (h == null) return null;
  return clamp(h + (k === 'fuel' ? SV[e.idx % 3][i] : (i - 1) * 0.06));
};

/* ── readiness ──────────────────────────────────────────────────────────────── */

// The API decides mission readiness (rul > 30 and no part at or below 40%). Locally
// this re-derived the same rule, which meant the KPI could contradict the badge on the
// same aircraft.
export const ready = (e) => (e.missionReady != null ? e.missionReady : localReady(e));

function localReady(e) {
  return rul(e) > 30 && KEYS.every((k) => (ph(e, k) ?? 1) > 0.4);
}

export const worst = (e) => {
  if (e.partHealth) {
    const known = KEYS.filter((k) => ph(e, k) != null);
    if (known.length) return known.reduce((a, k) => ((ph(e, k) ?? 1) < (ph(e, a) ?? 1) ? k : a));
  }
  return 'eng';
};

/* ── spares and agencies ────────────────────────────────────────────────────── */

export const partDetail = (e, k) => e.partDetail?.[k] || null;

export const spare = (e, k) => {
  const d = partDetail(e, k);
  if (d?.spare) return d.spare;
  if (k === 'eng' && e.engineSpare) return e.engineSpare;
  return null;
};

export const stk = (e, k) => spare(e, k)?.stock ?? null;

export const agency = (e, k) => partDetail(e, k)?.agency || null;

// Back-in-service days, from the API's own slot + turnaround + lead-time arithmetic.
export const backIn = (e, k) => {
  const d = partDetail(e, k);
  if (d?.back_in_service_days != null) return d.back_in_service_days;
  return null;
};

export { ENG, PARTS, KEYS, fleet };
