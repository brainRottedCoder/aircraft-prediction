import { useStore } from '../state/useStore.js';
import { fleet } from '../data/fleet.js';
import { PARTS } from '../data/parts.js';
import { fleetStats, pc, shortId } from '../lib/fleetStats.js';
import { rul, ready } from '../lib/health.js';

export default function Kpis() {
  const app = useStore();
  const { n, nr, nCrit, avg, lo, loRul, w0, availability, serverBacked } = fleetStats();
  const grounded = fleet.filter((e) => e.rul != null && !ready(e));
  const cycles = fleet.reduce((a, e) => Math.max(a, e.cycle || 0), 0);

  return (
    <div className="kpis">
      <div className="kp" data-tip={'Mission-ready, as reported by GET /api/v1/aircraft.\nOver 30 engine cycles left and no part at or below 40% health.'}>
        <div className="kpi" id="avail">{nr + ' of ' + n}<small>Mission-ready</small></div>
        <p className="kd" id="d-avail">
          {nr == n ? 'Every aircraft can fly' : 'Grounded: ' + grounded.map(shortId).join(', ')}
        </p>
      </div>
      <div className="kp" data-tip="Parts at or below 40% health, counted across the whole fleet by GET /api/v1/fleet/summary.">
        <div className="kpi" id="crit">{nCrit}<small>Critical parts</small></div>
        <p className="kd" id="d-crit">
          {w0 ? <>Lowest: {shortId(w0.e)} {PARTS[w0.k]?.name.toLowerCase()}, {pc(w0.h)}%</> : 'Awaiting fleet data'}
        </p>
      </div>
      <div className="kp" data-tip="Mean remaining engine life across the fleet, straight from the ML model's RUL per aircraft.">
        <div className="kpi" id="life">{serverBacked ? avg : '--'}<small>Avg engine cycles left</small></div>
        <p className="kd" id="d-life">
          {lo ? <>Lowest: {shortId(lo)}, {loRul != null ? loRul : rul(lo)} cycles</> : 'Awaiting fleet data'}
        </p>
      </div>
      <div className="kp">
        <div className="kpi" id="cyc">{cycles || '--'}<small>Highest fleet cycle</small></div>
        <p className="kd" id="d-cyc">
          {availability != null ? <>Fleet availability {pc(availability)}%</> : <>Advancing one cycle per aircraft every 1.2 s</>}
        </p>
      </div>
    </div>
  );
}
