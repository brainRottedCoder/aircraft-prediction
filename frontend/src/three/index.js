import { ctx, rt, resetContext } from './context.js';
import { app, setStatus } from '../state/store.js';
import { createScene, resize } from './scene.js';
import { buildHydraulics } from './models/hydraulics.js';
import { buildFuelSystem } from './models/fuelSystem.js';
import { buildArrows } from './arrows.js';
import { createEngineTags, loadEngine } from './models/engine.js';
import { loadJet } from './models/jet.js';
import { loadLandingGear } from './models/landingGear.js';
import { loadRadar } from './models/radar.js';
import { attachInteraction } from './interaction.js';
import { startLoop } from './loop.js';

// Builds the whole 3D twin inside `stage` (a DOM element) and returns { dispose }.
export function createTwin(stage) {
  resetContext();
  createScene(stage);
  buildHydraulics();      // procedural models are ready immediately
  buildFuelSystem();
  buildArrows();          // hotspot arrows + HTML labels
  createEngineTags();
  const detach = attachInteraction();
  resize();
  startLoop();

  // Real models stream in from /public/models.
  Promise.all([loadJet(), loadLandingGear(), loadRadar(), loadEngine()])
    .then(() => { if (!ctx.disposed) setStatus('Drag to rotate. Click a label to inspect a part.'); })
    .catch((e) => { if (!ctx.disposed) setStatus('Could not load a model: ' + e.message); });

  return {
    dispose() {
      ctx.disposed = true;
      cancelAnimationFrame(rt.raf);
      detach();
      stage.querySelectorAll('canvas, .hs, .tag, .fstat').forEach((n) => n.remove());
      ctx.R?.dispose();
    },
  };
}
