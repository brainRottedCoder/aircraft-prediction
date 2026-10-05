// API & WebSocket client — same-origin by design.
//
// In development Vite proxies /api and /ws to the FastAPI container, and in
// production nginx does the same (see vite.config.js and docker/frontend/nginx.conf).
// Deriving the base from window.location therefore needs no environment variable and no
// rebuild to point the app at another host: same-origin also removes CORS from the
// picture entirely, and keeps the JWT out of the WebSocket URL.

const API_BASE = `${window.location.origin}/api/v1`;
const WS_BASE = `${window.location.origin.replace(/^http/, 'ws')}/ws/fleet`;

const TOKEN_KEY = 'fdt.token';
const USER_KEY = 'fdt.user';

// The seeded demo accounts (docs/01 §7) are a documented fixture, not a secret — but they
// must never be used *silently*. This used to be an automatic fallback: with no token,
// every API call quietly POSTed commander/commander123 and the console came up already
// authenticated as the most privileged role. On a public deployment that is an open
// control room, and the "sign in" path in the UI was never reachable at all.
//
// So there is no implicit credential any more, and the demo sign-in is a build-time
// option: `import.meta.env` is inlined by Vite, so this folds to a constant and a
// production build deletes the whole branch — including the fixture password, which is
// otherwise readable in the bundle by anyone who loads the page.
const DEMO_ENABLED =
  import.meta.env?.VITE_DEMO_MODE === 'true' ||
  import.meta.env?.MODE === 'development';

let currentToken = sessionStorage.getItem(TOKEN_KEY);
let currentUser = readStoredUser();
let activeWs = null;
let reconnectTimer = null;
let reconnectAttempt = 0;
let consecutiveAuthFailures = 0;
let liveFilter = null;
let stopped = false;

// How many consecutive 4401 closes to tolerate before demanding a fresh sign-in. Without
// a bound, an expired session produced an endless reconnect loop at a 30 s period.
const MAX_AUTH_FAILURES = 2;

const listeners = new Set();

/** Subscribe to auth-state changes so the navbar badge reflects the real user. */
export function onAuthChange(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

function readStoredUser() {
  try {
    return JSON.parse(sessionStorage.getItem(USER_KEY) || 'null');
  } catch {
    return null;
  }
}

function emitAuth() {
  for (const fn of listeners) fn(currentUser);
}

function setSession(token, user) {
  currentToken = token;
  currentUser = user;
  if (token) sessionStorage.setItem(TOKEN_KEY, token);
  else sessionStorage.removeItem(TOKEN_KEY);
  if (user) sessionStorage.setItem(USER_KEY, JSON.stringify(user));
  else sessionStorage.removeItem(USER_KEY);
  emitAuth();
}

export function getUser() {
  return currentUser;
}

export function isDemoMode() {
  return DEMO_ENABLED;
}

export function logout() {
  setSession(null, null);
  consecutiveAuthFailures = 0;
}

/**
 * Exchange credentials for a session.
 *
 * @returns {Promise<{ok: true, user: object} | {ok: false, error: string}>} — never
 *   throws, and never signs in anybody the caller did not name.
 */
export async function login(username, password) {
  try {
    const res = await fetch(`${API_BASE}/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    });
    if (res.status === 401) {
      setSession(null, null);
      return { ok: false, error: 'Incorrect username or password.' };
    }
    if (!res.ok) {
      setSession(null, null);
      return { ok: false, error: `Sign-in failed (HTTP ${res.status}).` };
    }
    const data = await res.json();
    setSession(data.access_token, data.user);
    consecutiveAuthFailures = 0;
    return { ok: true, user: data.user };
  } catch (err) {
    console.warn('[API] login failed:', err);
    setSession(null, null);
    return { ok: false, error: 'Could not reach the server.' };
  }
}

/**
 * The explicit demo sign-in.
 *
 * Unreachable from a production build — see `DEMO_ENABLED`. The credentials live inside
 * this branch rather than in a module-level constant for that reason: a top-level object
 * is always retained in the bundle, so the password would ship regardless of the flag.
 */
export async function loginAsDemo() {
  if (!DEMO_ENABLED) {
    return { ok: false, error: 'Demo sign-in is not available in this build.' };
  }
  return login('commander', 'commander123');
}

/**
 * The token for an outgoing call, or null.
 *
 * Deliberately does *not* authenticate. It used to log in as the demo commander as a
 * side effect of "getting a token", which meant every fetch path could silently acquire
 * a privileged session. Null propagates: the caller gets a 401-shaped result and the app
 * shows the sign-in screen.
 */
async function ensureToken() {
  return currentToken;
}

async function request(path, { method = 'GET', body, retry = true } = {}) {
  const token = await ensureToken();
  if (!token) return { ok: false, status: 401, body: null, unauthenticated: true };

  const res = await fetch(`${API_BASE}${path}`, {
    method,
    headers: {
      Authorization: `Bearer ${token}`,
      ...(body ? { 'Content-Type': 'application/json' } : {}),
    },
    ...(body ? { body: JSON.stringify(body) } : {}),
  });

  // The JWT has a TTL. On expiry every subsequent call would silently return null
  // forever, because the token was never cleared — so clear it and let the one
  // in-flight retry re-authenticate.
  if (res.status === 401 && retry) {
    setSession(null, null);
    return request(path, { method, body, retry: false });
  }

  const payload = res.status === 204 ? null : await res.json().catch(() => null);
  return { ok: res.ok, status: res.status, body: payload };
}

export async function fetchAircraftList() {
  try {
    const { ok, body } = await request('/aircraft');
    return ok ? body : null;
  } catch (err) {
    console.warn('[API] fetchAircraftList failed:', err);
    return null;
  }
}

export async function fetchAircraftDetail(codeOrId) {
  try {
    const { ok, body } = await request(`/aircraft/${encodeURIComponent(codeOrId)}`);
    return ok ? body : null;
  } catch (err) {
    console.warn('[API] fetchAircraftDetail failed:', err);
    return null;
  }
}

export async function fetchFleetSummary() {
  try {
    const { ok, body } = await request('/fleet/summary');
    return ok ? body : null;
  } catch (err) {
    console.warn('[API] fetchFleetSummary failed:', err);
    return null;
  }
}

/**
 * Aircraft x part health matrix.
 *
 * This is the endpoint that makes the health heatmap real. The grid used to be
 * computed in the browser from a hardcoded wear curve, so every cell was invented
 * locally and had no relationship to the model or the database.
 */
export async function fetchHeatmap() {
  try {
    const { ok, body } = await request('/fleet/heatmap');
    return ok ? body : null;
  } catch (err) {
    console.warn('[API] fetchHeatmap failed:', err);
    return null;
  }
}

/** Worst parts across the fleet, with the action, spare, agency and turnaround. */
export async function fetchFleetActions(limit = 5) {
  try {
    const { ok, body } = await request(`/fleet/actions?limit=${limit}`);
    return ok ? body : null;
  } catch (err) {
    console.warn('[API] fetchFleetActions failed:', err);
    return null;
  }
}

/** One row per aircraft: worst part, action, due dates, spare status, agency, work order. */
export async function fetchSchedule() {
  try {
    const { ok, body } = await request('/maintenance/schedule');
    return ok ? body : null;
  } catch (err) {
    console.warn('[API] fetchSchedule failed:', err);
    return null;
  }
}

/**
 * Engine detail for one aircraft: per-module health, RUL, top sensors by real
 * z-score, the engine spare, and the last N cycles of history.
 */
export async function fetchEngineDetail(codeOrId, window = 60) {
  try {
    const { ok, body } = await request(
      `/aircraft/${encodeURIComponent(codeOrId)}/engine?window=${window}`,
    );
    return ok ? body : null;
  } catch (err) {
    console.warn('[API] fetchEngineDetail failed:', err);
    return null;
  }
}

/** Part detail: health, action, technical records, spare, agency, back-in-service breakdown. */
export async function fetchPartDetail(codeOrId, part) {
  try {
    const { ok, body } = await request(
      `/aircraft/${encodeURIComponent(codeOrId)}/parts/${encodeURIComponent(part)}`,
    );
    return ok ? body : null;
  } catch (err) {
    console.warn('[API] fetchPartDetail failed:', err);
    return null;
  }
}

export async function fetchAlerts() {
  try {
    const { ok, body } = await request('/alerts?limit=50');
    return ok ? body : null;
  } catch (err) {
    console.warn('[API] fetchAlerts failed:', err);
    return null;
  }
}

/**
 * Acknowledge a live alert.
 * @param {number} alertId numeric id from the backend — the path parameter is typed int,
 *   so a UI-side synthetic key cannot be sent.
 */
export async function postAcknowledgeAlert(alertId, note = null) {
  try {
    const { ok, body } = await request(`/alerts/${alertId}/ack`, {
      method: 'POST',
      body: { note },
    });
    return ok ? body : null;
  } catch (err) {
    console.warn('[API] postAcknowledgeAlert failed:', err);
    return null;
  }
}

/**
 * Raise a work order.
 *
 * `due_date` is required by WorkOrderCreate and `priority` is a three-value literal —
 * the previous payload omitted the date and sent "urgent", so every call was a 422 and
 * nothing was ever persisted. The due date is derived from the part's RUL so the order
 * lands where the maintenance plan expects it.
 */
export async function postCreateWorkOrder(aircraftCode, partCode, rul, notes = 'Raised from the Digital Twin') {
  const dueInDays = Number.isFinite(rul) ? Math.max(1, Math.min(180, Math.ceil(rul))) : 30;
  const dueDate = new Date(Date.now() + dueInDays * 86400000).toISOString().slice(0, 10);
  const priority = Number.isFinite(rul) && rul <= 20 ? 'high' : 'medium';

  try {
    const { ok, status, body } = await request('/work-orders', {
      method: 'POST',
      body: { aircraft: aircraftCode, part: partCode, due_date: dueDate, priority, notes },
    });
    if (!ok) {
      console.warn(`[API] work order rejected: HTTP ${status}`, body);
      return null;
    }
    return body;
  } catch (err) {
    console.warn('[API] postCreateWorkOrder failed:', err);
    return null;
  }
}

export function startLiveFleetSocket(onEvent, onStatus) {
  stopped = false;
  activeWs?.close();
  activeWs = null;
  clearTimeout(reconnectTimer);

  async function connect() {
    if (stopped) return;
    const token = await ensureToken();
    if (!token) {
      // Signed out. Retrying cannot help — there is nothing to authenticate with — and
      // this used to spin forever on a 30 s timer, quietly re-attempting the demo
      // credentials every half minute. Wait for an actual sign-in instead.
      onStatus?.(false);
      return;
    }

    try {
      // Browsers cannot set headers on a WebSocket handshake, so the JWT necessarily
      // travels in the query string. It is short-lived, and the endpoint additionally
      // origin-checks the connection.
      const ws = new WebSocket(
        `${WS_BASE}?token=${encodeURIComponent(token)}`,
      );
      activeWs = ws;

      ws.onopen = () => {
        reconnectAttempt = 0;
        consecutiveAuthFailures = 0;
        // keep-alive: the server answers `ping` with `pong`, which doubles as proof
        // the stream is live rather than merely open.
        ws.send(JSON.stringify({ type: 'ping', payload: {} }));
        // Subscriptions live only in the server's per-connection memory, so a reconnect
        // starts from "everything" again. Re-assert whatever this client asked for,
        // otherwise a filter silently stops filtering after one dropped connection.
        if (liveFilter?.length) {
          ws.send(JSON.stringify({ type: 'subscribe', payload: { aircraft: liveFilter } }));
        }
        onStatus?.(true);
      };

      ws.onmessage = (event) => {
        try {
          onEvent?.(JSON.parse(event.data));
        } catch (e) {
          console.error('[WS] malformed frame:', e);
        }
      };

      ws.onclose = (event) => {
        onStatus?.(false);
        activeWs = null;
        if (event.code === 4401) {
          // The token was rejected or expired. Drop it so the UI falls back to the
          // sign-in screen, and stop retrying: reconnecting with a token the server has
          // already refused can only fail again.
          consecutiveAuthFailures += 1;
          if (consecutiveAuthFailures >= MAX_AUTH_FAILURES) {
            console.warn('[WS] giving up after repeated 4401; sign in again');
            setSession(null, null);
            return;
          }
          setSession(null, null);
        }
        scheduleReconnect(connect);
      };

      ws.onerror = () => ws.close();
    } catch (e) {
      console.warn('[WS] connection error:', e);
      onStatus?.(false);
      scheduleReconnect(connect);
    }
  }

  connect();

  return () => {
    stopped = true;
    clearTimeout(reconnectTimer);
    activeWs?.close();
    activeWs = null;
  };
}

/**
 * Restrict this socket to a subset of the fleet, or pass `null` for everything.
 *
 * Applied immediately when a socket is open and remembered for the next `onopen`, since
 * the server holds the filter per connection. Events that name no aircraft — alerts being
 * acknowledged, spares reserved, bookings — are fleet-wide and always delivered.
 */
export function setLiveFleetFilter(codes) {
  liveFilter = codes?.length ? [...codes] : null;
  if (activeWs?.readyState === WebSocket.OPEN) {
    activeWs.send(JSON.stringify(
      liveFilter
        ? { type: 'subscribe', payload: { aircraft: liveFilter } }
        : { type: 'unsubscribe', payload: { aircraft: [] } },
    ));
  }
}

/** Exponential backoff with jitter, capped — a fixed 3 s retry storm-hammers a cold API. */
function scheduleReconnect(connect) {
  reconnectAttempt += 1;
  const delay = Math.min(30000, 1000 * 2 ** (reconnectAttempt - 1)) + Math.random() * 500;
  clearTimeout(reconnectTimer);
  reconnectTimer = setTimeout(connect, delay);
}