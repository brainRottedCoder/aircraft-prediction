// Status colours. `C` is mutated when the theme changes (see applyThemeColors).
export const COLORS_LIGHT = { ok: '#4f8a6b', warn: '#c8902e', bad: '#800020', mut: '#8d8d8d' };
export const COLORS_DARK = { ok: '#6fbf94', warn: '#e0a64a', bad: '#e0506b', mut: '#7a7a7a' };

export const C = { ...COLORS_LIGHT };

// null means "the API has not answered yet". It must not fall through to `bad`, which
// is what `null > 0.7` did — an unanswered part was drawn as a failed one — and it must
// be a concrete colour rather than a CSS var, because these values are handed to
// three.js materials, which reject `var(--mut)` with "Unknown color".
export const hc = (h) => (h == null ? C.mut : h > 0.7 ? C.ok : h > 0.4 ? C.warn : C.bad);
export const rk = (h) => (h == null ? 'unknown' : h > 0.7 ? 'healthy' : h > 0.4 ? 'watch' : 'critical');

export function applyThemeColors(dark) {
  Object.assign(C, dark ? COLORS_DARK : COLORS_LIGHT);
}
