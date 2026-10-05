import { useStore } from '../state/useStore.js';
import { selectAircraft } from '../state/store.js';
import { fleet } from '../data/fleet.js';
import { PARTS, KEYS } from '../data/parts.js';
import { C, hc } from '../lib/colors.js';
import { rul, ready, ph, worst } from '../lib/health.js';
import { fleetStats, pc } from '../lib/fleetStats.js';

// "Parts status" stacked bar + average health per subsystem.
//
// The bar counts come from /fleet/summary's risk_breakdown, so the total matches the
// critical-parts KPI instead of being recounted from the client's own thresholds.
function PartsMix() {
  const { partsMix } = fleetStats();
  const { healthy, watch, critical, total } = partsMix;
  const rows = KEYS.map((k) => {
    let sum = 0, n = 0, cr = 0;
    for (const e of fleet) {
      const v = ph(e, k);
      if (v == null) continue;
      sum += v; n++;
      if (v <= 0.4) cr++;
    }
    return [PARTS[k].name, n ? sum / n : null, cr];
  }).sort((a, b) => (b[1] ?? 2) - (a[1] ?? 2));

  return (
    <div id="hmix">
      <h3>{'Parts status, ' + total + ' parts'}</h3>
      <div className="stk">
        <i style={{ flex: healthy, background: C.ok }}></i>
        <i style={{ flex: watch, background: C.warn }}></i>
        <i style={{ flex: critical, background: C.bad }}></i>
      </div>
      <div className="stl">
        <span><b>{healthy}</b> healthy</span>
        <span><b>{watch}</b> watch</span>
        <span><b>{critical}</b> critical</span>
      </div>
      <h3 style={{ marginTop: 6 }}>Average health by subsystem</h3>
      {rows.map((r) => (
        <div className="hmr" key={r[0]}>
          <span>{r[0]}</span>
          <div className="bar">
            <i style={{ width: (r[1] ?? 0) * 100 + '%', background: hc(r[1] ?? 0) }}></i>
          </div>
          <span className="mono">{r[1] == null ? '--' : Math.round(r[1] * 100) + '%'}</span>
          <span className="cr" style={{ color: r[2] ? C.bad : 'var(--mut)' }}>{r[2] ? r[2] + ' crit' : 'ok'}</span>
        </div>
      ))}
    </div>
  );
}

export default function FleetHealth() {
  const app = useStore();
  const { n, nr } = fleetStats();
  return (
    <section className="pn s5 rv" id="health">
      <div className="hd"><h2>Fleet health</h2><p>Engine life left, per aircraft</p></div>
      <div className="hgrid">
        <div
          id="donut"
          style={{ '--p': (n ? (nr / n) * 100 : 0) + '%' }}
          data-tip={nr + ' of ' + n + ' aircraft are mission-ready, as reported by GET /api/v1/aircraft'}
        >
          <div id="dn">{n ? pc(nr / n) + '%' : '--'}</div>
        </div>
        <div>
          <div id="vbars">
            {fleet.map((e, i) => {
              const k = worst(e);
              const r = e.rul;
              const ok = ready(e);
              return (
                <button
                  key={e.id}
                  className={'vb ' + (i == app.sel ? 'on' : '')}
                  data-i={i}
                  data-tip={
                    e.id +
                    '\nEngine life left: ' + (r == null ? '--' : r) + ' of 125 cycles' +
                    '\nWeakest part: ' + (PARTS[k]?.name || k) + ', ' + pc(ph(e, k)) + '%' +
                    '\n' + (ok ? 'Mission-ready' : 'Not mission-ready') +
                    '\nClick to select'
                  }
                  onClick={() => selectAircraft(i)}
                >
                  <span className="trk">
                    <i className={ok ? '' : 'bad'} style={{ height: Math.max(4, ((r ?? 0) / 125) * 100) + '%' }}></i>
                  </span>
                  <em>{i + 1}</em>
                </button>
              );
            })}
          </div>
        </div>
      </div>
      <PartsMix />
    </section>
  );
}
