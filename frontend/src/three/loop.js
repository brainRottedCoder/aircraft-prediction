import * as THREE from 'three';
import { ctx, rt } from './context.js';
import { app } from '../state/store.js';
import { fleet } from '../data/fleet.js';
import { KEYS } from '../data/parts.js';
import { clamp, sm, ease } from '../lib/math.js';
import { hc } from '../lib/colors.js';
import { ph } from '../lib/health.js';
import { setOp } from './helpers.js';
import { VIEW_TARGETS } from './config.js';
import { animateEngine, hideEngineTags } from './models/engine.js';
import { animateFuel } from './models/fuelSystem.js';
import { animateHydraulics } from './models/hydraulics.js';
import { subFaultAnim, hideFaultTags, hideSubFaultTags } from './subFaults.js';

// Render loop: auto-rotation / view presets, hotspot labels, whole-jet <-> part transition, per-part animation.
export function startLoop() {
  const { stage, R, S, cam, JP, JI } = ctx;
  const { PR, AR, LB } = ctx;
  const P0=new THREE.Vector3(72,56,168),P2=new THREE.Vector3(0,12,105),T0=new THREE.Vector3(),v=new THREE.Vector3(),aw=new THREE.Vector3(),nw=new THREE.Vector3(),pp=new THREE.Vector3(),np=new THREE.Vector3(),tt=new THREE.Vector3();

  function frame(t){rt.raf=requestAnimationFrame(frame);
   const dt=Math.min(.05,(t-rt.lt)/1000||.016);rt.lt=t;
   rt.tz=clamp(rt.tz+(app.tgt?1:-1)*(rt.tz==app.tgt?0:dt/1.5));if(Math.abs(rt.tz-app.tgt)<.001)rt.tz=app.tgt;
   const e=fleet[app.sel],fit=Math.max(1,1.25/cam.aspect),calm=1-sm(0,.3,rt.tz),bob=Math.sin(t/950);
   if(app.view=='auto'){rt.yaw=-.5+.45*Math.sin(t/4200);rt.pitch=.2+.5*Math.sin(t/6300)}else if(app.view!='free'){const VT=VIEW_TARGETS[app.view]||[0,0],k=1-Math.exp(-dt*4);rt.yaw+=(VT[0]-rt.yaw)*k;rt.pitch+=(VT[1]-rt.pitch)*k}
   JP.rotation.set(rt.pitch,rt.yaw,0);JP.position.y=0;JP.updateMatrixWorld(true);
   const ph1=t/1000;
   KEYS.forEach((k,i)=>{const h=ph(e,k),c=hc(h),a=AR[k],L=LB[k];
    if(!a.ok){L.style.opacity=0;L.style.pointerEvents='none';return}
    if(a.c!==c){a.c=c;a.all.forEach(m=>m.color.set(c))}
    // ph() is null until the API answers for that part; it must render as unknown
    // rather than as NaN% (or, worse, as a failed part once hc() started returning a
    // neutral colour for null).
    L.style.setProperty('--c',c);L.lastChild.textContent=h==null?'--':Math.round(h*100)+'%';
    aw.copy(a.a);JI.localToWorld(aw);v.copy(a.lp);JI.localToWorld(v);nw.copy(a.n).transformDirection(JI.matrixWorld);
    const face=nw.dot(pp.copy(cam.position).sub(aw).normalize()),vis=sm(-.05,.3,face)*(1-sm(0,.25,rt.tz));
    a.cone.position.y=7;a.ms.forEach(m=>m.opacity=vis);
    a.rp.forEach(r=>{r.visible=false});
    a.g.visible=vis>.05;v.project(cam);
    L.style.transform='translate('+(Math.max(80,Math.min(stage.clientWidth-80,(v.x+1)/2*stage.clientWidth))|0)+'px,'+((1-v.y)/2*stage.clientHeight|0)+'px) translate(-50%,-50%) scale('+(.85+.15*vis)+')';
    L.style.opacity=vis;L.style.pointerEvents=vis>.5?'auto':'none';L.tabIndex=vis>.5?0:-1;a.sx=Math.max(80,Math.min(stage.clientWidth-80,(v.x+1)/2*stage.clientWidth));a.sy=(1-v.y)/2*stage.clientHeight;a.vis=vis});
   const vl=KEYS.filter(k=>AR[k].ok&&AR[k].vis>.05).sort((p,q)=>AR[p].sy-AR[q].sy);for(let i=1;i<vl.length;i++){const A=AR[vl[i-1]],B=AR[vl[i]];if(Math.abs(B.sx-A.sx)<150&&B.sy-A.sy<30){B.sy=A.sy+30;LB[vl[i]].style.transform='translate('+(B.sx|0)+'px,'+(B.sy|0)+'px) translate(-50%,-50%)'}}
   const op=1-sm(.25,.6,rt.tz),po=sm(.5,.9,rt.tz);
   if(ctx.JET&&Math.abs(op-rt.lastJ)>.005){setOp(ctx.JET,op);rt.lastJ=op}
   if(PR[app.cur]){if(Math.abs(po-rt.lastP)>.005||rt.lastP<0||rt.lastK!=app.cur){Object.keys(PR).forEach(k=>setOp(PR[k].root,k==app.cur?po:0));rt.lastP=po;rt.lastK=app.cur}}
   if(rt.tz==0&&rt.lastP!=0){Object.keys(PR).forEach(k=>setOp(PR[k].root,0));rt.lastP=0}
   aw.copy(AR[app.cur].a);JI.localToWorld(aw);
   pp.copy(P0).multiplyScalar(fit*rt.zf);np.copy(pp).sub(aw).normalize().multiplyScalar(48).add(aw);
   if(rt.tz<.5){const u=ease(rt.tz/.5);cam.position.lerpVectors(pp,np,u);tt.lerpVectors(T0,aw,u)}
   else{const u=ease((rt.tz-.5)/.5);cam.position.lerpVectors(np,v.copy(P2).multiplyScalar(fit*rt.zf),u);tt.lerpVectors(aw,T0,u)}
   cam.lookAt(tt);
    const pr = PR[app.cur];
    if (pr && rt.tz > .5) {
      if (!rt.drag && rt.tz == 1) pr.spin.rotation.y += .004;
      if (app.cur == 'eng') {
        hideFaultTags();
        animateEngine(pr, e, t);
      } else {
        hideEngineTags();
        if (app.cur == 'fuel' && pr.f) { hideSubFaultTags(); animateFuel(pr, t, dt, po); } else hideFaultTags();
        if (app.cur != 'fuel' && pr.sf) subFaultAnim(pr, t, dt, po);
        if (app.cur == 'hyd' && pr.rod) animateHydraulics(pr, t, po);
      }
    } else {
      hideEngineTags();
      hideFaultTags();
    }
    R.render(S, cam);
  }
  rt.raf = requestAnimationFrame(frame);
}
