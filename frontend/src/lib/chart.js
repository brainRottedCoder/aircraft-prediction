import { clamp } from './math.js';
import { hc } from './colors.js';
import { ph } from './health.js';

// Engine-health history drawn on the inspector's <canvas id="hc">.
//
// `e.hist` is a list of {cycle, health} points from the API, oldest first. It used to be
// a list of bare numbers seeded locally with Math.random(), and `clamp(p)` was being
// handed the point object — which clamped to NaN, so this chart drew nothing at all.
export function drawHistoryChart(e) {
  const c = document.getElementById('hc');
  if (!c) return;
  const x = c.getContext('2d'), w = c.width, h = c.height;
  x.clearRect(0, 0, w, h);
  x.strokeStyle = 'rgba(128,0,32,.12)';
  for (let i = 0; i < 4; i++) { x.beginPath(); x.moveTo(0, (i * h) / 3); x.lineTo(w, (i * h) / 3); x.stroke(); }

  const d = (e.hist || []).filter((p) => p && p.health != null);
  if (d.length < 2) return;

  const col = hc(ph(e, 'eng') ?? 1);
  x.beginPath();
  d.forEach((p, i) => {
    const px = (i / (d.length - 1)) * w;
    const py = h - clamp(p.health, 0, 1) * h;
    i ? x.lineTo(px, py) : x.moveTo(px, py);
  });
  x.strokeStyle = col; x.lineWidth = 3; x.stroke();
  x.lineTo(w, h); x.lineTo(0, h); x.fillStyle = col + '22'; x.fill();
}
