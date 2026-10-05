// Fleet-wide numbers, taken from the API's own aggregates where it provides them.
//
// These were recomputed locally from the client's fabricated fleet, so the KPI cards
// could disagree with the same figures on the aircraft list. /fleet/summary is the
// authoritative version of exactly this panel; the local derivation remains only for
// the disconnected case.

import { fleet } from '../data/fleet.js';
import { KEYS } from '../data/parts.js';
import { ready, rul, ph, worst } from './health.js';
import { derived } from '../state/server.js';

export const pc = (h) => (h == null ? '--' : Math.round(h * 100));
export const shortId = (e) => (e ? e.id.replace('Fighter-', 'F-') : '--');

// Average engine cycles left across the fleet, as a whole number for the KPI card.
const localAvg = () => {
  if (!fleet.length) return null;
  return Math.round(fleet.reduce((a, e) => a + rul(e), 0) / fleet.length);
};

export function fleetStats() {
  const s = derived.summary;
  const n = s?.total_aircraft ?? fleet.length;
  const serverBacked = s != null;

  // With no summary, fall back to counting every aircraft locally rather than counting
  // only the ones the API has answered for: an unanswered poll is not a grounded
  // aircraft, and filtering to `rul != null` reported a fully-populated fleet as
  // "0 of 8 mission-ready" the moment the backend went away.
  const nr = s?.mission_ready_count ?? fleet.filter(ready).length;
  const known = serverBacked ? fleet.filter((e) => e.rul != null) : fleet;

  const wa = known
    .map((e) => ({ e, k: worst(e), h: ph(e, worst(e)) }))
    .filter((x) => x.h != null)
    .sort((a, b) => a.h - b.h);

  const lo = s?.lowest_rul_aircraft
    ? fleet.find((e) => e.id === s.lowest_rul_aircraft.code)
    : known.reduce((a, e) => (!a || rul(e) < rul(a) ? e : a), null);

  return {
    n,
    nr,
    nCrit: s?.critical_parts ?? 0,
    avg: Math.round(s?.average_rul ?? localAvg() ?? 0),
    lo,
    loRul: s?.lowest_rul_aircraft?.rul ?? (lo ? rul(lo) : null),
    riskBreakdown: s?.risk_breakdown || null,
    availability: s?.avg_availability ?? null,
    serverBacked,
    wa,                      // each aircraft's weakest part, worst first
    w0: wa[0] || null,
    partsMix: partsMix(),
  };
}

// Count of parts in each risk band, fleet-wide. /fleet/summary already reports this
// as risk_breakdown, keyed the same way the stacked bar is drawn.
function partsMix() {
  const s = derived.summary?.risk_breakdown;
  if (s) return { healthy: s.healthy || 0, watch: s.watch || 0, critical: s.critical || 0, total: fleet.length * KEYS.length };
  const c = { healthy: 0, watch: 0, critical: 0 };
  let total = 0;
  for (const e of fleet) {
    for (const k of KEYS) {
      const h = ph(e, k);
      if (h == null) continue;
      total++;
      if (h > 0.7) c.healthy++; else if (h > 0.4) c.watch++; else c.critical++;
    }
  }
  return { ...c, total: total || fleet.length * KEYS.length };
}
