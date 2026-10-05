import { ctx } from '../context.js';
import { loadGlb } from '../loaders.js';
import { mkPart } from '../helpers.js';
import { setupGlb, finGlb } from '../subFaults.js';

// Radar and avionics (public/models/radar.glb) with antenna / gimbal / transmitter damage zones.
export async function loadRadar() {
  const o = await loadGlb('radar.glb');
  if (ctx.disposed) return;
  setupGlb(o, 'radar');
  mkPart('radar', o, 60);
  finGlb(o, 'radar');
}
