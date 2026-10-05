import { ctx } from '../context.js';
import { loadGlb } from '../loaders.js';
import { fitArrows } from '../arrows.js';

// Rafale-style fighter (public/models/rafale.glb). Landing-gear/instrument helper meshes are hidden.
export async function loadJet() {
  const o = await loadGlb('rafale.glb');
  if (ctx.disposed) return;
  o.traverse((n) => { if (/landingOn|instrGlass/.test(n.name)) n.visible = false; });
  o.position.set(0, 0, 0);
  ctx.JI.add(o);
  ctx.JET = o;
  fitArrows(o);
}
