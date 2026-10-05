import { useStore } from '../state/useStore.js';
import { acknowledge } from '../state/store.js';
import { fleet } from '../data/fleet.js';
import { PARTS } from '../data/parts.js';
import { pc } from '../lib/fleetStats.js';

const nameOf = (part) => PARTS[part === 'engine' ? 'eng' : part]?.name || part;

// Live alerts, from GET /api/v1/alerts and the `alert.raised` WebSocket event.
//
// This panel used to ignore the API completely and rebuild its own list by thresholding
// the browser's fabricated part health at 40%, then POST acknowledgements to alert ids
// it had invented. Every alert on screen was therefore local, and none of them were
// the alerts the backend had actually raised — which is also why acknowledging one
// could silently do nothing.
export default function Alerts() {
  const app = useStore();
  const alerts = (app.serverAlerts || [])
    .filter((a) => !a.acknowledged)
    .slice()
    .sort((x, y) => (x.health ?? 1) - (y.health ?? 1));

  return (
    <section className="pn s12 rv" id="alerts">
      <div className="hd"><h2>Alerts</h2><p>Raised by the fleet API, worst first</p></div>
      <ul id="alist">
        {alerts.slice(0, 8).map((a) => {
          const e = fleet.find((x) => x.serverId === a.aircraft_id || x.id === a.aircraft);
          // Acknowledging is keyed by the UI's own index:part identity, so it survives
          // the list being re-sorted and re-fetched.
          const key = e ? `${e.idx}:${a.part === 'engine' ? 'eng' : a.part}` : `${a.aircraft_id - 1}:${a.part}`;
          const acked = a.acknowledged || app.ACK[key];
          return (
            <li key={a.id} className={(a.level || 'critical') + (acked ? ' ack' : '')}>
              <span>
                <b>{a.aircraft || (e && e.id)}</b>{' ' + nameOf(a.part) + ', '}
                <span className="mono">{pc(a.health)}%</span>
                <small>
                  {acked
                    ? 'Acknowledged'
                    : a.message || (a.level === 'critical' ? 'Critical, needs action' : 'Watch')}
                </small>
              </span>
              {acked ? null : <button className="wo" onClick={() => acknowledge(key)}>Acknowledge</button>}
            </li>
          );
        })}
        {alerts.length ? null : (
          <li>
            <span className="note">
              {app.apiReachable ? 'No open alerts.' : 'Not connected — alerts are unavailable offline.'}
            </span>
          </li>
        )}
      </ul>
    </section>
  );
}
