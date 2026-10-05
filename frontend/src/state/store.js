import { applyThemeColors } from '../lib/colors.js';
import { postAcknowledgeAlert, postCreateWorkOrder, getUser, onAuthChange } from '../lib/api.js';
import { fleet } from '../data/fleet.js';

// Single mutable app state shared by the React UI and the three.js scene.
// React subscribes through useStore(); the 3D render loop just reads `app` every frame.
export const app = {
  sel: 0,                 // selected aircraft index
  cycle: 0,               // simulated fleet cycle
  cur: 'eng',             // part currently inspected (or last inspected)
  tgt: 0,                 // 1 while zoomed into a part, 0 while looking at the whole aircraft
  fc: 0,                  // forecast offset in cycles (0, 10, 20, 30)
  view: 'auto',           // camera preset: auto | top | side | under | free
  theme: 'light',
  status: 'Loading models...',
  WO: {},                 // work orders:  "aircraftIndex:part" -> server reference
  ACK: {},                // acknowledged alerts: "aircraftIndex:part" -> 1
  loaded: {},             // parts whose 3D model is ready: { eng: true, ... }
  backendConnected: false,// WebSocket open: live per-tick engine values
  apiReachable: false,   // read endpoints answering: polled fleet-wide state
  onBackendChange: null,  // set by state/server.js to re-poll on (re)connect
  // Provenance of the most recent health prediction, from the WS payload. The API can
  // answer a single tick from `rul = 125 - cycle` while the model itself stays loaded
  // and /healthz stays green, so this is the only place the UI learns that a number
  // on screen is not the model's. `null` until the first event arrives.
  model: { version: null, fallback: false, degraded: false, reason: null, staleTicks: 0 },
  // Real signed-in identity. Previously a hardcoded stub that never changed, so the
  // role badge lied about who was operating the console.
  user: getUser(),
  serverAlerts: [],       // live alerts pushed over the WebSocket, newest first
};

// Keep the navbar in sync with the session even when auth changes outside an action
// (token expiry re-login, explicit sign-out).
onAuthChange((user) => {
  if (user?.username !== app.user?.username) {
    app.user = user;
    emit();
  }
});

let version = 0;
const subs = new Set();
export const subscribe = (fn) => { subs.add(fn); return () => subs.delete(fn); };
export const getVersion = () => version;
export const emit = () => { version++; subs.forEach((fn) => fn()); };

/* ---------- actions ---------- */

export function selectAircraft(i) {
  app.sel = i;
  emit();
}

export function openPart(k) {
  if (!app.loaded[k]) return;
  app.cur = k;
  app.tgt = 1;
  emit();
  const d = document.getElementById('detail')?.getBoundingClientRect();
  if (d && d.top > innerHeight * 0.55) scrollBy({ top: d.top - innerHeight * 0.5, behavior: 'smooth' });
}

export function closePart() {
  app.tgt = 0;
  emit();
}

// Used by the health heat map: select the aircraft, zoom into the part and scroll the 3D view into sight.
export function inspectInTwin(i, k) {
  app.sel = i;
  openPart(k);
  document.getElementById('twin')?.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

export function setView(v) {
  app.view = v;
  if (app.tgt) app.tgt = 0;
  emit();
}

// Dragging the scene switches to the free camera (no part is closed).
export function freeView() {
  if (app.view !== 'free') { app.view = 'free'; emit(); }
}

export function setForecast(n) {
  app.fc = n;
  emit();
}

/** Backend part codes differ from the frontend's short keys for the engine. */
const PART_CODE = { eng: 'engine' };

/**
 * Raise a work order for the selected aircraft's part.
 *
 * The optimistic local reference is replaced by the server's once the POST succeeds.
 * The previous payload violated WorkOrderCreate (no `due_date`, `priority: "urgent"`
 * is not a valid literal) so every call 422'd and the fabricated `WO-1001` was the only
 * reference the operator ever saw.
 */
export function createWorkOrder(key) {
  if (app.WO[key]) return;

  const [, partKey] = key.split(':');
  const plane = byKey(key);
  const part = PART_CODE[partKey] || partKey;
  // Placeholder only; replaced on success, and reverted on failure.
  app.WO[key] = 'saving…';
  emit();

  // The due date is derived from the part's own RUL where the API has one, so the
  // order lands where the maintenance plan says it should.
  const partRul = plane?.partRul?.[partKey] ?? plane?.rul;
  postCreateWorkOrder(plane.id, part, partRul).then((res) => {
    if (res) {
      app.WO[key] = res.reference || res.code || 'created';
    } else {
      // Not 'offline': the request was made and refused, so 'failed' is the truth.
      delete app.WO[key];
    }
    emit();
  });
}

// Work orders and acknowledgements are keyed "aircraftIndex:part", but the aircraft
// order comes from the client's FLEET_CODES rather than from the server, so resolve
// through the same map the rest of the UI uses instead of indexing blindly.
function byKey(key) {
  const [planeIdx] = key.split(':');
  return fleet[Number(planeIdx)] || null;
}

/**
 * Acknowledge a critical part.
 *
 * `key` is the UI-side "aircraftIndex:part" identity. The backend endpoint is typed
 * `int`, so the numeric id is looked up from the alerts pushed over the WebSocket —
 * previously the UI key itself was POSTed to the alert route, which 404'd every time.
 *
 * The aircraft is matched on the `serverId` the API reported rather than on
 * `index + 1`, which assumed the client list order and the database's happened to
 * agree and silently acknowledged the wrong aircraft when they did not.
 * The optimistic UI state is reverted if the server refuses.
 */
export function acknowledge(key) {
  const [, partKey] = key.split(':');
  const plane = byKey(key);
  const serverPart = PART_CODE[partKey] || partKey;
  const serverAlert = app.serverAlerts.find(
    (a) => a.aircraft_id === plane?.serverId && a.part === serverPart,
  );

  app.ACK[key] = 1;
  emit();

  if (!serverAlert) return;            // no matching live alert: local state only
  postAcknowledgeAlert(serverAlert.id).then((res) => {
    if (!res) {
      delete app.ACK[key];
      emit();
    }
  });
}

export function setBackendStatus(connected) {
  const was = app.backendConnected;
  app.backendConnected = connected;
  emit();
  // The read endpoints are polled rather than pushed, so a reconnect has to kick one
  // immediately or the panels sit on pre-disconnect numbers.
  if (connected && !was) app.onBackendChange?.();
}

export function setUser(user) {
  app.user = user;
  emit();
}

export function toggleTheme() {
  const dark = document.documentElement.dataset.theme !== 'dark';
  document.documentElement.dataset.theme = dark ? 'dark' : 'light';
  app.theme = dark ? 'dark' : 'light';
  applyThemeColors(dark);
  emit();
}

export function setStatus(s) {
  app.status = s;
  emit();
}
