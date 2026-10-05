import * as THREE from 'three';
import { ctx } from '../context.js';
import { mm, mkPart } from '../helpers.js';
import { sfTags, sfHalos } from '../subFaults.js';

// Hydraulics: procedural pump, actuator rod and animated fluid lines (the original used stand-in geometry too).

// Registers sub-fault zones: pump (left), servo actuator (centre), lines (tubes).
function setupHyd(h) {
  const { PR } = ctx;
 const g=[[],[],[]];h.traverse(o=>{if(!o.isMesh||o.material.isMeshBasicMaterial)return;o.material=o.material.clone();const x=new THREE.Vector3();o.getWorldPosition(x);h.worldToLocal(x);g[o.geometry.type=='TubeGeometry'?2:(x.x<-31?0:1)].push(o.material)});
 const an=[[-40,5,0],[0,6,0],[16,17,-9]];Object.assign(PR.hyd,{obj:h,mg:g,sf:{an:an.map(p=>new THREE.Vector3(...p)),halos:sfHalos(h,an,9),tags:sfTags()}})}

export function buildHydraulics() {
  const { PR } = ctx;
 const h=new THREE.Group(),steel=mm(0x7d8a99,.85,.32),dark=mm(0x2b3540,.7,.5),chrome=mm(0xe6edf5,1,.12),gold=mm(0xf5a524,.5,.35);
  const cyl=(r,l,m,sg=40)=>{const o=new THREE.Mesh(new THREE.CylinderGeometry(r,r,l,sg),m);o.rotation.z=Math.PI/2;return o},add=(o,x=0,y=0,z=0,p=h)=>{o.position.set(x,y,z);p.add(o);return o};
  add(cyl(7,34,steel));add(cyl(8,3,dark),-17.5);add(cyl(8.4,4,dark),17.5);for(let i=0;i<5;i++)add(cyl(7.4,.8,dark),-12+i*6);
  const rg=new THREE.Group();add(cyl(2.6,38,chrome),19,0,0,rg);add(cyl(3.6,3,dark),38.5,0,0,rg);add(new THREE.Mesh(new THREE.TorusGeometry(4,1.8,14,28),dark),43,0,0,rg);add(cyl(1,7,chrome,12),43,0,0,rg).rotation.set(Math.PI/2,0,0);add(rg,0,0,0);
  add(new THREE.Mesh(new THREE.TorusGeometry(4,1.8,14,28),dark),-24);add(cyl(1,8,chrome,12),-24).rotation.set(Math.PI/2,0,0);
  add(new THREE.Mesh(new THREE.BoxGeometry(16,6,10),dark),-4,9.5);add(cyl(3.4,12,steel,24),-4,16).rotation.set(0,0,0);add(new THREE.Mesh(new THREE.CylinderGeometry(3.4,3.4,12,24),steel),-4,16);add(new THREE.Mesh(new THREE.CylinderGeometry(2.4,2.4,3,24),gold),-4,23);
  add(cyl(1.6,30,steel,16),2,-9.5,5.5);add(cyl(2.2,4,gold,16),-14,-9.5,5.5);
  add(cyl(5.5,13,steel),-38,5);add(cyl(6,3,dark),-45,5);add(cyl(4,5,gold),-32.5,5);for(let i=0;i<7;i++){const q=i/7*Math.PI*2;add(new THREE.Mesh(new THREE.CylinderGeometry(.9,.9,2,10),chrome),-44.5,5+Math.cos(q)*4,Math.sin(q)*4).rotation.z=Math.PI/2}
  const tube=(pts,col)=>{const c=new THREE.CatmullRomCurve3(pts.map(p=>new THREE.Vector3(...p)),false,'catmullrom',.3);h.add(new THREE.Mesh(new THREE.TubeGeometry(c,48,1.1,10),mm(col,.3,.45)));
   const ms=[];for(let i=0;i<12;i++){const m=new THREE.Mesh(new THREE.SphereGeometry(.95,10,8),new THREE.MeshBasicMaterial({color:col==0xd23a3a?0xffb0a0:0xa8d8ff,transparent:true}));h.add(m);ms.push(m)}return {c,ms}};
  const fl=[{...tube([[-8,12,3],[-14,17,9],[-22,17,9],[-26,11,5],[-24,3,2],[-20,1,0],[-18,3,0]],0xd23a3a),s:.35},
   {...tube([[0,12,-3],[6,18,-9],[16,17,-9],[20,10,-5],[19,3,-2],[17.5,3,0]],0x2f7fd0),s:-.35},
   {...tube([[-38,10,0],[-34,16,6],[-22,19,7],[-12,16,5],[-8,12,3]],0xd23a3a),s:.45},
   {...tube([[0,12,-3],[-2,6,-8],[-14,-3,-10],[-30,-2,-8],[-40,2,-4],[-42,5,0]],0x2f7fd0),s:-.45}];
  h.position.x=-2;mkPart('hyd',h,60);PR.hyd.rod=rg;PR.hyd.flows=fl;PR.hyd.spin.rotation.set(.3,-.6,0);setupHyd(h);
}

// Moves the actuator rod and the fluid markers along the hose curves.
export function animateHydraulics(pr, t, po) {
  const ph1 = t / 1000;
  pr.rod.position.x = (1 + Math.sin(t / 800)) * 7;
  pr.flows.forEach((f) => f.ms.forEach((m, j) => {
    const u = ((ph1 * f.s + j / f.ms.length) % 1 + 1) % 1;
    f.c.getPointAt(u, m.position);
    m.material.opacity = po;
  }));
}
