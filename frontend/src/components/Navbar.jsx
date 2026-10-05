import { useStore } from '../state/useStore.js';
import { toggleTheme } from '../state/store.js';
import { fleet } from '../data/fleet.js';
import { rul } from '../lib/health.js';
import { logout } from '../lib/api.js';

// A per-tick fallback (rejected window, failed inference) leaves the model loaded and
// /healthz green, so the numbers on screen look model-backed either way. Say which.
//
// The stream and the read API are reported separately: the fleet-wide panels come from
// polling and work perfectly well without the WebSocket, so a refused socket should read
// as "no live stream", not as "nothing here is real".
function ModelBadge({ model, connected, apiReachable }) {
  if (!apiReachable && !connected) {
    return (
      <span className="backend-badge connecting" title="No backend connection. Health, RUL and trends on screen are generated in the browser.">
        <span className="dot amber" />
        Simulated
      </span>
    );
  }
  if (!model.version) {
    return (
      <span className="backend-badge connecting" title="Connected, but no prediction has arrived yet.">
        <span className="dot amber" />
        Awaiting model
      </span>
    );
  }
  if (model.fallback) {
    const why = model.reason ? ` (${model.reason})` : '';
    return (
      <span className="backend-badge connecting" title={`The last prediction came from the deterministic rul = 125 - cycle curve, not model ${model.version}${why}. See GET /healthz.`}>
        <span className="dot amber" />
        Fallback{model.staleTicks > 1 ? ` ${model.staleTicks}t` : ''}
      </span>
    );
  }
  if (!connected) {
    return (
      <span className="backend-badge connecting" title="Fleet data is being polled, but the WebSocket is not open, so engine values update on the poll interval rather than every 1.2 s.">
        <span className="dot amber" />
        Polling
      </span>
    );
  }
  return (
    <span className="backend-badge online" title={`RUL and health from model ${model.version}, z-scored per regime.`}>
      <span className="dot green" />
      Model {model.version}
    </span>
  );
}

export default function Navbar() {
  const app = useStore();
  const sel = fleet[app.sel];
  // "Fleet cycle" was taken from the last `cycle.tick` to arrive, but the replay
  // publishes one per aircraft per pass — so it was whichever aircraft happened to be
  // processed last, and it jumped backwards whenever that one wrapped. The selected
  // aircraft's own cycle is a real, unambiguous number.
  const cycle = sel?.cycle;
  return (
    <header className="nav">
      <div className="logo"></div>
      <b>Fleet digital twin</b>
      <span id="live">
        {sel
          ? `${sel.id}, cycle ${cycle ?? '--'}, ${rul(sel)} left${app.fc ? ` (forecast +${app.fc})` : ''}`
          : 'Fleet digital twin'}
      </span>
      <span className={'backend-badge ' + (app.backendConnected ? 'online' : 'connecting')} title="FastAPI + PostgreSQL + NASA C-MAPSS Telemetry Stream">
        <span style={{ width: 8, height: 8, borderRadius: '50%', background: app.backendConnected ? '#10b981' : '#f59e0b', display: 'inline-block' }}></span>
        {app.backendConnected ? 'Backend Live' : 'Connecting...'}
      </span>
      <ModelBadge model={app.model} connected={app.backendConnected} apiReachable={app.apiReachable} />
      {app.user && (
        <span className="role-badge" title={`Signed in as ${app.user.username} (${app.user.role})`}>
          {app.user.username} · {app.user.role}
        </span>
      )}
      {/* The console has always had a role badge, but no way to end the session — there
          was no session to end, because sign-in was implicit. Now that it is explicit,
          signing out has to be reachable from here. */}
      {app.user && (
        <button type="button" className="signout" onClick={logout} title="End this session">
          Sign out
        </button>
      )}
      <nav className="links">
        <a href="#twin">Aircraft</a>
        <a href="#health">Health</a>
        <a href="#attention">Attention</a>
        <a href="#plan">Plan</a>
      </nav>
      <button id="theme" className="wo" onClick={toggleTheme}>{app.theme === 'dark' ? 'Light' : 'Dark'}</button>
    </header>
  );
}
