// Shared handles of the running 3D scene. Everything the original single-file script kept in globals lives here.
//   ctx.stage / R / S / cam ... DOM stage, renderer, scene, camera (set by createScene)
//   ctx.PR   inspected-part registry  { eng, gear, radar, hyd, fuel }
//   ctx.AR   hotspot arrows           ctx.LB  HTML label buttons     ctx.TG  engine damage tags
//   ctx.FT / SF  fuel / sub-fault tags
//   rt       per-frame runtime values (camera yaw/pitch, zoom, transition progress...)
import { app } from '../state/store.js';

const freshRuntime = () => ({
  tz: 0,          // 0 = whole aircraft, 1 = zoomed into a part (animated transition)
  zf: 1,          // zoom factor (mouse wheel)
  drag: false, lx: 0, ly: 0, moved: 0,
  yaw: -0.5, pitch: 0.2,
  lt: 0,          // last frame time
  lastJ: -1, lastP: -1, lastK: '',
  raf: 0,
});

export const ctx = {
  stage: null, R: null, S: null, cam: null, JP: null, JI: null, ring: null,
  PR: {}, AR: {}, LB: {}, TG: {},
  FT: null, SF: null, JET: null,
  rt: freshRuntime(),
  disposed: false,
};
export const rt = ctx.rt;

export function resetContext() {
  ['PR', 'AR', 'LB', 'TG'].forEach((k) => Object.keys(ctx[k]).forEach((p) => delete ctx[k][p]));
  Object.assign(ctx, { stage: null, R: null, S: null, cam: null, JP: null, JI: null, ring: null, FT: null, SF: null, JET: null, disposed: false });
  Object.assign(ctx.rt, freshRuntime());
  Object.keys(app.loaded).forEach((k) => delete app.loaded[k]);
}
