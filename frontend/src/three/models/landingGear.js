import { ctx } from '../context.js';
import { loadGlb } from '../loaders.js';
import { mkPart } from '../helpers.js';
import { setupGlb, finGlb } from '../subFaults.js';

// Landing gear (public/models/landing-gear.glb) with tyre / strut / actuator damage zones.
export async function loadLandingGear() {
  const o = await loadGlb('landing-gear.glb');
  if (ctx.disposed) return;
  setupGlb(o, 'gear');
  mkPart('gear', o, 60);
  finGlb(o, 'gear');
}
