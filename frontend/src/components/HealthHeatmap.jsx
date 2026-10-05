import { useStore } from '../state/useStore.js';
import { inspectInTwin } from '../state/store.js';
import { fleet } from '../data/fleet.js';
import { PARTS, KEYS } from '../data/parts.js';
import { rk } from '../lib/colors.js';
import { ph, partRisk, partDetail, spare, agency, backIn } from '../lib/health.js';
import { pc, shortId } from '../lib/fleetStats.js';

// Aircraft x part grid, from GET /api/v1/fleet/heatmap. Clicking a cell opens that part
// in 3D.
//
// This grid used to be computed in the browser from the local wear curve, so every
// number in it was invented and no cell could be traced to the database. Risk comes
// from the API too, rather than being re-thresholded locally — the backend's bands are
// >0.70 / 0.40-0.70 / <=0.40 and the UI now matches them.
export default function HealthHeatmap() {
  const app = useStore();
  return (
    <section className="pn s7 rv">
      <div className="hd"><h2>Health by part</h2><p>Select a cell to inspect that part in 3D</p></div>
      <div className="scroll">
        <table id="heat">
          <thead>
            <tr>
              <th></th>
              {KEYS.map((k) => <th key={k}>{PARTS[k].name.split(' ')[0]}</th>)}
            </tr>
          </thead>
          <tbody>
            {fleet.map((e, i) => (
              <tr key={e.id}>
                <th className={i == app.sel ? 'on' : ''}>{shortId(e)}</th>
                {KEYS.map((k) => {
                  const h = ph(e, k);
                  const r = partRisk(e, k) || rk(h ?? 1);
                  const d = partDetail(e, k);
                  const sp = spare(e, k);
                  const ag = agency(e, k);
                  const bk = backIn(e, k);
                  const tip = [
                    e.id + ', ' + PARTS[k].name,
                    h == null ? 'Health: awaiting data' : 'Health ' + pc(h) + '% (' + r + ')',
                    e.partSimulated?.[k] ? 'Derived from maintenance burden, not the engine model' : 'From the ML model',
                    sp ? 'Spare: ' + sp.item_name + ', ' + (sp.stock > 0 ? sp.stock + ' in stock' : 'out of stock') + (sp.lead_time_days ? ', ' + sp.lead_time_days + ' day lead time' : '') : null,
                    ag ? ag.name + ': free slot in ' + ag.free_slot_days + ' days, turnaround ' + ag.turnaround_days + ' days' : null,
                    bk != null ? 'Back in service in about ' + bk + ' days' : null,
                    d?.action && r !== 'healthy' ? 'Action: ' + d.action : null,
                    'Click to inspect in 3D',
                  ].filter(Boolean).join('\n');
                  return (
                    <td key={k}>
                      <button
                        className={'cell ' + r}
                        data-i={i}
                        data-k={k}
                        data-tip={tip}
                        onClick={() => inspectInTwin(i, k)}
                      >
                        {h == null ? '--' : pc(h)}
                      </button>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
