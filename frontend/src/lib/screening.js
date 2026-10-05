// "Reading sensors" effect: when the inspector changes, its numbers scramble and then lock one by one.
// It mutates the DOM text of the inspector directly, so the Inspector component freezes its React output while `scr.on`.

export const scr = { on: false, key: '', prev: [], iv: 0 };

// Elements whose numbers take part in the effect.
export const RSEL = '.kpi,.row>span:last-child,.mono,.dv';

export const prefersReducedMotion = () =>
  typeof matchMedia !== 'undefined' && matchMedia('(prefers-reduced-motion:reduce)').matches;

export function startScreening(s, { onStart, onDone }) {
  clearInterval(scr.iv);
  const set = new Set();
  s.querySelectorAll(RSEL).forEach((el) => {
    const w = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    let n;
    while ((n = w.nextNode())) if (/\d/.test(n.nodeValue)) set.add(n);
  });
  const nd = [...set], og = nd.map((n) => n.nodeValue), bars = [...s.querySelectorAll('.bar i')], bw = bars.map((b) => b.style.width);
  const N = nd.length + bars.length, st = Math.min(60, 900 / Math.max(1, N)), t0 = performance.now(), lk = new Array(N).fill(0);
  const rnd = (t) => {
    if (/cycles/.test(t)) return t.replace(/\d+/, 1 + ((Math.random() * 124) | 0));
    let f = 1;
    return t.replace(/\d/g, (c) => { const r = f && c != '0' ? 1 + ((Math.random() * 9) | 0) : (Math.random() * 10) | 0; f = 0; return r; });
  };
  scr.on = true;
  onStart();
  scr.iv = setInterval(() => {
    const t = performance.now() - t0;
    let done = 1;
    nd.forEach((n, i) => {
      if (t >= 450 + i * st) { if (!lk[i]) { lk[i] = 1; n.nodeValue = og[i]; n.parentElement.classList.add('lock'); } }
      else { done = 0; n.nodeValue = rnd(og[i]); }
    });
    bars.forEach((b, j) => {
      const i = nd.length + j;
      if (t >= 450 + i * st) { if (!lk[i]) { lk[i] = 1; b.style.width = bw[j]; } }
      else { done = 0; b.style.width = 10 + Math.random() * 85 + '%'; }
    });
    if (done) {
      clearInterval(scr.iv);
      scr.on = false;
      onDone();
      scr.prev = [...s.querySelectorAll(RSEL)].map((x) => x.textContent);
    }
  }, 40);
}

export function stopScreening() {
  clearInterval(scr.iv);
  scr.on = false;
}
