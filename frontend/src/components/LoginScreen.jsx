import { useState } from 'react';
import { isDemoMode, login, loginAsDemo } from '../lib/api.js';

// Explicit sign-in. This screen exists because the app used to have none: `ensureToken`
// quietly POSTed the demo commander's credentials whenever no token was present, so the
// console always came up already authenticated as the most privileged role and no
// deployment could turn that off. A real deployment now has to say who you are.
export default function LoginScreen({ onSignedIn }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  async function submit(event) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    const result = await login(username.trim(), password);
    setBusy(false);
    if (result.ok) {
      setPassword('');
      onSignedIn?.(result.user);
    } else {
      setError(result.error);
    }
  }

  async function useDemo() {
    if (busy) return;
    setBusy(true);
    setError(null);
    const result = await loginAsDemo();
    setBusy(false);
    if (result.ok) onSignedIn?.(result.user);
    else setError(result.error);
  }

  return (
    <main className="wrap login-wrap">
      <div className="login-card">
        <p className="login-eyebrow">Fleet Digital Twin</p>
        <h1 className="login-title">Sign in</h1>
        <p className="login-sub">
          This console is read-write and role-gated. Authenticate to continue.
        </p>

        <form onSubmit={submit} className="login-form">
          <label className="login-field">
            <span>Username</span>
            <input
              name="username"
              autoComplete="username"
              autoFocus
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              required
            />
          </label>
          <label className="login-field">
            <span>Password</span>
            <input
              name="password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </label>

          {error ? (
            <p className="login-error" role="alert">
              {error}
            </p>
          ) : null}

          <button type="submit" className="login-submit" disabled={busy}>
            {busy ? 'Signing in…' : 'Sign in'}
          </button>
        </form>

        {isDemoMode() ? (
          <>
            <p className="login-divider">or</p>
            <button type="button" className="login-demo" onClick={useDemo} disabled={busy}>
              Continue as the demo commander
            </button>
            <p className="login-note">
              Seeded fixture accounts (<code>commander</code>, <code>officer</code>,{' '}
              <code>viewer</code>). Visible here only because this build has demo mode on.
              Set <code>VITE_DEMO_MODE=false</code> to remove it, and change the passwords
              before exposing a deployment.
            </p>
          </>
        ) : null}
      </div>
    </main>
  );
}