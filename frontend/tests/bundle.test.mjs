// Build-output assertions for the demo sign-in.
//
// The credential leak this guards against is not hypothetical: the demo shortcut was
// first implemented as a module-level constant plus a runtime `isDemoMode()` check, and
// Vite does not tree-shake a JSX branch — so a production bundle still contained the
// committed fixture password, readable by anyone who loaded the page. The UI was hidden
// but the secret shipped.
//
// These run the real build twice, because the property only exists after minification and
// dead-code elimination. A source-level check would pass on the broken version too.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync, readdirSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const frontend = fileURLToPath(new URL('..', import.meta.url));
const PASSWORD = 'commander123';
const SHORTCUT = 'Continue as the demo commander';

function build(viteDemoMode) {
  const out = mkdtempSync(join(tmpdir(), 'fdt-build-'));
  try {
    execFileSync('npm', ['run', 'build', '--', '--outDir', out, '--emptyOutDir'], {
      cwd: frontend,
      env: { ...process.env, VITE_DEMO_MODE: viteDemoMode },
      stdio: 'pipe',
    });
    const assets = join(out, 'assets');
    return readdirSync(assets)
      .filter((f) => f.endsWith('.js'))
      .map((f) => readFileSync(join(assets, f), 'utf8'))
      .join('\n');
  } finally {
    rmSync(out, { recursive: true, force: true });
  }
}

test('a production build does not ship the demo fixture password', () => {
  const bundle = build('false');
  assert.ok(bundle.length > 0, 'the build produced no JS to inspect');
  assert.ok(
    !bundle.includes(PASSWORD),
    'the committed demo password is present in a production bundle',
  );
  assert.ok(!bundle.includes(SHORTCUT), 'the demo shortcut copy survived the build');
});

test('an opted-in build still offers the demo sign-in', () => {
  const bundle = build('true');
  assert.ok(bundle.includes(PASSWORD), 'VITE_DEMO_MODE=true must keep the demo sign-in');
  assert.ok(bundle.includes(SHORTCUT));
});