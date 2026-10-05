import * as THREE from 'three';
import { ctx, rt } from './context.js';
import { app, openPart, closePart, freeView } from '../state/store.js';
import { KEYS } from '../data/parts.js';
import { clamp } from '../lib/math.js';
import { resize } from './scene.js';

// Mouse / touch / keyboard control of the scene. Returns a function that removes every listener.
export function attachInteraction() {
  const { stage, R, cam } = ctx;
  const { AR, PR } = ctx;
  const raf = new THREE.Raycaster(), mv = new THREE.Vector2();
  const off = [];
  const on = (target, type, fn, opts) => { target.addEventListener(type, fn, opts); off.push(() => target.removeEventListener(type, fn, opts)); };

  on(window, 'keydown', (e) => { if (e.key == 'Escape') closePart(); });
  on(stage,'pointerdown',e=>{if(e.target.closest('.hs,#back,#views'))return;rt.drag=true;rt.lx=e.clientX;rt.ly=e.clientY;rt.moved=0;if(rt.tz==0)freeView()});
  on(window,'pointerup',e=>{const was=rt.drag;rt.drag=false;if(was&&rt.moved<5&&app.tgt==0&&rt.tz==0&&e.target==R.domElement){const r=stage.getBoundingClientRect();mv.set((e.clientX-r.left)/r.width*2-1,-((e.clientY-r.top)/r.height)*2+1);raf.setFromCamera(mv,cam);
    const h=raf.intersectObjects(KEYS.filter(k=>AR[k].g.visible).map(k=>AR[k].hit))[0];if(h)openPart(h.object.userData.k)}});
  on(window,'pointermove',e=>{if(!rt.drag)return;const dx=e.clientX-rt.lx,dy=e.clientY-rt.ly;rt.moved+=Math.abs(dx)+Math.abs(dy);rt.lx=e.clientX;rt.ly=e.clientY;
   if(rt.tz==0){rt.yaw+=dx*.008;rt.pitch=clamp(rt.pitch+dy*.005,-1.35,1.35)}else if(rt.tz==1&&PR[app.cur]){const q=PR[app.cur].spin;q.rotation.y+=dx*.008;q.rotation.x=clamp(q.rotation.x+dy*.005,-.9,.9)}});
  on(stage,'wheel',e=>{e.preventDefault();rt.zf=clamp(rt.zf+e.deltaY*.0008,.6,1.5)},{passive:false});
  on(window, 'resize', resize);
  return () => off.forEach((f) => f());
}
