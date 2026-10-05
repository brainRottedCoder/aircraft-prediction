import { fleet, resolve } from '../data/fleet.js';
import { clamp } from '../lib/math.js';
import { scr } from '../lib/screening.js';
import { app, emit, setBackendStatus } from './store.js';
import { applyHealthEvent, refreshFleet, startServerState } from './server.js';
import { startLiveFleetSocket, fetchAlerts } from '../lib/api.js';

// Pull the open alerts so the Alerts panel can acknowledge against real backend ids, and
// so acknowledgements made by *other* operators disappear here too.
//
// This used to run only on WebSocket (re)connect. Combined with the client discarding
// `alert.acked`, an ack by a second operator stayed on screen — stale, and still
// acknowledging — until this console's own socket happened to drop and reconnect.
function hydrateAlerts() {
  return fetchAlerts().then((res) => {
    if (!res || !Array.isArray(res.items)) return;
    app.serverAlerts = res.items;
    emit();
  }).catch(() => {});
}

// Local tick used only when the backend is unreachable, so the console still moves.
// Nothing here touches the server-backed fields: `lib/health.js` falls back to its own
// arithmetic only while an aircraft has no server values, so this stays an offline
// crutch rather than a second source of truth racing the real one.
function fallbackTick() {
  if (scr.on) return;
  app.cycle++;
  fleet.forEach((e) => {
    if (e.rul != null) return;                  // server-backed: leave it alone
    const d = clamp((e._offlineWear ?? 0.3) + 0.0035 + Math.random() * 0.002, 0, 0.99);
    e._offlineWear = d;
    e.hist.push({ cycle: e._offlineCycle = (e._offlineCycle ?? 0) + 1, health: clamp(1 - d, 0, 1) });
    if (e.hist.length > 61) e.hist.shift();
    if (d >= 0.99) { e._offlineWear = 0.05; e.hist = e.hist.map(() => 0.95); }
  });
  emit();
}

// Work-order mutations, all of which invalidate the derived schedule and action list.
const WORK_ORDER_EVENTS = new Set(['work_order.created', 'work_order.updated']);

// Alerts move on other operators' clocks, and nothing about this console's socket makes
// that visible, so they get their own (slower) poll rather than riding the 3 s fleet one.
const ALERT_POLL_MS = 10000;

// Record where the newest prediction came from. A fallback tick while the backend is
// connected used to be indistinguishable from a model-backed one: a healthy engine's
// linear guess is roughly right, so `rul = 125 - cycle` looked like a real number.
function trackModel(payload) {
  const m = payload && payload.model;
  if (!m) return;
  const prev = app.model;
  app.model = {
    version: m.version ?? null,
    fallback: !!m.fallback,
    degraded: !!m.degraded,
    reason: m.reason ?? null,
    staleTicks: m.fallback ? (prev.fallback ? prev.staleTicks + 1 : 1) : 0,
  };
}

export function startSimulation() {
  let fallbackTimer = null;
  let alertTimer = null;
  const stopServerState = startServerState();

  // 1. Initial snapshot, then the socket for live values
  const stopSocket = startLiveFleetSocket(
    (event) => {
      if (!event || !event.type) return;

      if (event.type === 'cycle.tick') {
        // The replay publishes one cycle.tick per aircraft, not one per fleet pass
        // (replay.py advances every aircraft inside a single tick), so the last one to
        // arrive was whichever aircraft happened to be processed last. The navbar showed
        // that as "the fleet cycle", which was meaningless. Leave the cycle counter to
        // the per-aircraft `current_cycle` the API reports instead of inventing a fleet
        // cycle from an event that does not describe one.
        return;
      } else if (event.type === 'health.updated') {
        trackModel(event.payload);
        if (applyHealthEvent(event.payload || {})) emit();
      } else if (event.type === 'alert.raised') {
        const a = event.payload;
        if (a && a.id) {
          if (!app.serverAlerts) app.serverAlerts = [];
          if (!app.serverAlerts.some((x) => x.id === a.id)) {
            app.serverAlerts.unshift(a);
          }
          emit();
        }
      } else if (event.type === 'alert.acked') {
        // Somebody acknowledged it — often not this console. Dropping it here is what
        // makes a shared alert board behave like one.
        const id = event.payload?.id;
        if (id != null && Array.isArray(app.serverAlerts)) {
          const next = app.serverAlerts.filter((x) => x.id !== id);
          if (next.length !== app.serverAlerts.length) {
            app.serverAlerts = next;
            emit();
          }
        }
      } else if (WORK_ORDER_EVENTS.has(event.type)
                 || event.type === 'spare.reserved'
                 || event.type === 'booking.created') {
        // Work orders, stock levels and agency bookings are all derived across tables on
        // the server, so re-read rather than patching a projection here — removing the
        // latency the socket exists to remove is the entire point of using it.
        refreshFleet().then((ok) => { if (ok) emit(); }).catch(() => {});
      }
    },
    (connected) => {
      setBackendStatus(connected);

      // The alert poll is driven by reachability, not by the socket: `alert.raised` only
      // fires when this process raises one, and alerts move on other operators' clocks.
      // Gating it on `connected` alone left the panel frozen whenever the WebSocket was
      // unavailable — a refused origin (4408), say — even though every read worked.
      if (connected || app.apiReachable) {
        if (!alertTimer) alertTimer = setInterval(hydrateAlerts, ALERT_POLL_MS);
      } else if (alertTimer) {
        clearInterval(alertTimer);
        alertTimer = null;
      }

      if (connected) {
        if (fallbackTimer) {
          clearInterval(fallbackTimer);
          fallbackTimer = null;
        }
        // Re-read the authoritative values rather than trusting whatever the offline
        // fallback happened to leave behind.
        refreshFleet().then((ok) => { if (ok) emit(); }).catch(() => {});
        hydrateAlerts();
      } else if (!fallbackTimer && !app.apiReachable) {
        // Only fall back to inventing data when the read API is unreachable too. A
        // refused WebSocket on its own — an origin outside the allowlist closes it with
        // 4408 — used to be enough to start this timer, so the console quietly showed
        // a fabricated fleet while every real number was one poll away.
        fallbackTimer = setInterval(fallbackTick, 1200);
        app.model = { version: null, fallback: true, degraded: true,
                      reason: 'backend disconnected', staleTicks: 0 };
        emit();
      }
    }
  );

  return () => {
    stopSocket();
    stopServerState();
    if (fallbackTimer) clearInterval(fallbackTimer);
    if (alertTimer) clearInterval(alertTimer);
  };
}
