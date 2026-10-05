// The maintenance plan.
//
// The rows come from GET /maintenance/schedule, which is the API's own version of this
// table: worst part per aircraft, the action, due cycle/date, spare status, agency,
// back-in-service days and any open work order. The previous implementation rebuilt all
// of that in the browser from a hardcoded spare table and three invented agencies, so
// the plan could name a different agency and lead time than the one the work order
// would actually be booked with.

import { fleet } from '../data/fleet.js';
import { PARTS } from '../data/parts.js';
import { app } from '../state/store.js';
import { hc } from './colors.js';
import { ph, rul, worst, partDetail, spare, agency, backIn } from './health.js';
import { derived } from '../state/server.js';

export const PLAN_HEADERS = ['Aircraft', 'Part', 'Risk', 'Action', 'Do by', 'Spare', 'Agency', 'Back in service'];

const label = (code) => PARTS[code === 'engine' ? 'eng' : code]?.name || code;

const riskOf = (health) => (health == null ? 'unknown' : health > 0.7 ? 'healthy' : health > 0.4 ? 'watch' : 'critical');

function spareText(row, e) {
  if (row) {
    if (row.spare_status === 'none') return 'no spare configured';
    if (row.spare_status === 'out_of_stock') return 'out of stock';
    if (row.spare_status === 'low') return `low (${row.spare_item || 'spare'})`;
    return row.spare_item ? `${row.spare_item} in stock` : 'in stock';
  }
  const sp = spare(e, row?.worst_part === 'engine' ? 'eng' : row?.worst_part);
  if (!sp) return '-';
  return sp.stock > 0 ? `${sp.stock} in stock` : 'out of stock';
}

export function planRows() {
  const rows = derived.schedule;

  if (rows.length) {
    return rows.map((r) => {
      const e = fleet.find((x) => x.id === r.aircraft) || null;
      const k = r.worst_part === 'engine' ? 'eng' : r.worst_part;
      const h = r.worst_health ?? (e ? ph(e, k) : null);
      const woKey = `${e ? e.idx : r.aircraft_id - 1}:${k}`;
      return {
        id: r.aircraft,
        part: label(r.worst_part),
        risk: r.risk || riskOf(h),
        color: hc(h ?? 0),
        action: r.action,
        wo: app.WO[woKey] || r.open_work_order || '',
        doBy: dueText(r),
        spare: spareText(r, e),
        spareBad: r.spare_status === 'out_of_stock',
        agency: r.agency || 'unassigned',
        back: r.back_in_service_days != null ? `~${r.back_in_service_days} days` : '-',
      };
    });
  }

  // Disconnected: keep the table populated from what the client still holds, and say
  // nothing about the API rather than inventing logistics numbers.
  return fleet.map((e) => {
    const k = worst(e), h = ph(e, k);
    return {
      id: e.id,
      part: label(k),
      risk: riskOf(h),
      color: hc(h ?? 0),
      action: riskOf(h) === 'healthy' ? 'Routine check' : riskOf(h) === 'watch' ? 'Plan inspection' : 'Replace now',
      wo: app.WO[`${e.idx}:${k}`] || '',
      doBy: e.rul == null ? '-' : `within ${Math.max(0, rul(e) - 10)} cycles`,
      spare: spareText(null, e) || '-',
      spareBad: false,
      agency: (e && agency(e, k)?.name) || 'unassigned',
      back: (e && backIn(e, k) != null) ? `~${backIn(e, k)} days` : '-',
    };
  });
}

function dueText(r) {
  if (r.do_by_date) return r.do_by_date;
  if (r.do_by_in_cycles != null) return `in ${Math.max(0, r.do_by_in_cycles)} cycles`;
  if (r.do_by_cycle != null) return `cycle ${r.do_by_cycle}`;
  return '-';
}

// CSV text of the maintenance plan (same cell text as shown in the table).
export function planCsv() {
  const q = (v) => '"' + String(v ?? '').replace(/"/g, '""') + '"';
  const rows = planRows().map((r) => [r.id, r.part, r.risk, r.action + (r.wo ? ' ' + r.wo : ''), r.doBy, r.spare, r.agency, r.back]);
  return [PLAN_HEADERS, ...rows].map((row) => row.map(q).join(',')).join('\n');
}

export function downloadPlanCsv() {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([planCsv()], { type: 'text/csv' }));
  a.download = 'maintenance-plan.csv';
  a.click();
}
