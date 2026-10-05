// Auth behaviour of the API client, under node:test.
//
// This exists because the console used to have no sign-in path at all: `ensureToken()`
// silently POSTed the demo commander's credentials whenever no token was held, so every
// deployment — including a public one — came up already authenticated as the most
// privileged role. There was no JS test infrastructure, so nothing would have caught its
// removal.
//
// Run with `npm test` (node:test, no dependencies). The module under test reads
// `window`, `sessionStorage` and `fetch` at call time, so they are installed as globals
// before the dynamic import rather than mocked per-call.
import { test } from 'node:test';
import assert from 'node:assert/strict';

function makeStorage() {
  const map = new Map();
  return {
    getItem: (k) => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => map.set(k, String(v)),
    removeItem: (k) => map.delete(k),
    clear: () => map.clear(),
  };
}

function install({ token = null, user = null } = {}) {
  const store = makeStorage();
  if (token) store.setItem('fdt.token', token);
  if (user) store.setItem('fdt.user', JSON.stringify(user));

  const calls = [];
  globalThis.window = {
    location: { origin: 'http://localhost:8080' },
  };
  globalThis.sessionStorage = store;
  globalThis.fetch = async (url, init = {}) => {
    calls.push({ url: String(url), method: init?.method ?? 'GET' });
    if (String(url).endsWith('/auth/login')) {
      const body = JSON.parse(init.body);
      if (body.username === 'commander' && body.password === 'commander123') {
        return json(200, {
          access_token: 'test-token',
          user: { username: 'commander', role: 'commander', id: 1 },
        });
      }
      return json(401, { error: { code: 'UNAUTHORIZED' } });
    }
    if (init?.headers?.Authorization) return json(200, { items: [] });
    return json(401, { error: { code: 'UNAUTHORIZED' } });
  };
  return { calls, store };
}

function json(status, body) {
  return { ok: status < 400, status, json: async () => body };
}

/** Fresh module instance per test — the client keeps module-level session state. */
async function loadApi() {
  const url = new URL('../src/lib/api.js', import.meta.url);
  return import(`${url.href}?t=${Math.random()}`);
}

test('an unauthenticated client does not silently sign in as the demo commander', async () => {
  const { calls } = install({ token: null });
  const api = await loadApi();

  const list = await api.fetchAircraftList();

  assert.equal(list, null, 'the call must fail closed');
  const logins = calls.filter((c) => c.url.endsWith('/auth/login'));
  assert.deepEqual(logins, [], 'no credential may be sent without an explicit sign-in');
});

test('an anonymous request is not even put on the wire', async () => {
  const { calls } = install({ token: null });
  const api = await loadApi();

  await api.fetchAircraftList();
  await api.fetchAlerts();

  // Failing closed at the client is deliberate: with no token there is nothing to
  // authenticate with, so the read is abandoned rather than sent and bounced.
  assert.deepEqual(calls, []);
});

test('an explicit sign-in establishes a session', async () => {
  const { calls, store } = install({ token: null });
  const api = await loadApi();

  const result = await api.login('commander', 'commander123');

  assert.equal(result.ok, true);
  assert.equal(api.getUser().username, 'commander');
  assert.equal(store.getItem('fdt.token'), 'test-token');
  assert.equal(calls.filter((c) => c.url.endsWith('/auth/login')).length, 1);
});

test('bad credentials are reported and leave no session behind', async () => {
  install({ token: null });
  const api = await loadApi();

  const result = await api.login('commander', 'wrong-password');

  assert.equal(result.ok, false);
  assert.match(result.error, /Incorrect username or password/);
  assert.equal(api.getUser(), null);
});

test('logout clears the session', async () => {
  const { store } = install({
    token: 'test-token',
    user: { username: 'commander', role: 'commander', id: 1 },
  });
  const api = await loadApi();

  assert.ok(api.getUser());
  api.logout();

  assert.equal(api.getUser(), null);
  assert.equal(store.getItem('fdt.token'), null);
  assert.equal(store.getItem('fdt.user'), null);
});

test('the demo sign-in is not advertised in a production build', async () => {
  install({ token: null });
  const api = await loadApi();

  // `import.meta.env` is absent outside Vite, which is exactly the production case: the
  // fixture-account button must not be offered.
  assert.equal(api.isDemoMode(), false);
});