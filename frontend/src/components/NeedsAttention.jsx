import { useStore } from '../state/useStore.js';
import { inspectInTwin } from '../state/store.js';
import { PARTS } from '../data/parts.js';
import { derived } from '../state/server.js';
import { pc } from '../lib/fleetStats.js';

const nameOf = (code) => PARTS[code === 'engine' ? 'eng' : code]?.name.toLowerCase() || code;

// The worst parts across the fleet, from GET /api/v1/fleet/actions: the API's own
// ranking, each with the action it implies plus the real spare, agency and turnaround.
//
// Previously this listed each aircraft's weakest part as computed in the browser, with
// the accessory text ("no spare in stock, back in about 11 days") derived from a
// hardcoded parts table — so it could recommend an action the maintenance plan on the
// same screen contradicted.
export default function NeedsAttention() {
  useStore();
  const items = derived.actions;

  return (
    <section className="pn s7 rv" id="attention">
      <div className="hd"><h2>Needs attention</h2><p>Worst parts across the fleet, worst first</p></div>
      <ul id="acts">
        {items.map((a) => {
          const sp = a.spare;
          const ag = a.agency;
          return (
            <li key={a.aircraft + ':' + a.part} className={a.risk}>
              <b>{a.aircraft}</b>{', ' + nameOf(a.part) + ' at ' + pc(a.health) + '%'}
              <small>
                {a.action + '. '}
                {sp
                  ? (sp.stock > 0
                      ? `${sp.item_name}: ${sp.stock} in stock`
                      : `${sp.item_name}: out of stock, ${sp.lead_time_days} day lead time`) + '. '
                  : ''}
                {ag ? `${ag.name}, back in about ${a.back_in_service_days} days. ` : ''}
                {a.simulated ? 'Derived from maintenance burden, not the engine model.' : 'From the ML model.'}
              </small>
            </li>
          );
        })}
        {items.length ? null : (
          <li><span className="note">{derived.fetchedAt ? 'No parts below threshold.' : 'Connecting to the fleet API…'}</span></li>
        )}
      </ul>
    </section>
  );
}
