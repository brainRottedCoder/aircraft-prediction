import * as THREE from 'three';
import { ctx } from './context.js';
import { KEYS, PARTS } from '../data/parts.js';
import { openPart } from '../state/store.js';

const { AR, LB } = ctx;

// Hotspot arrow of a part: position a, normal n, label anchor lp.
function place(k,a,n){const r=AR[k];r.a.copy(a);r.n.copy(n);r.g.position.copy(a);r.g.quaternion.setFromUnitVectors(new THREE.Vector3(0,1,0),n);r.lp.copy(a).addScaledVector(n,44);r.ok=true}

// One arrow + one HTML label button per part.
export function buildArrows() {
  const { stage, JI } = ctx;
  KEYS.forEach((k,i)=>{const p=PARTS[k],g=new THREE.Group(),mk=()=>new THREE.MeshBasicMaterial({color:0x800020,transparent:true,depthWrite:false});
   const md=mk(),mc=mk(),ml=new THREE.LineBasicMaterial({color:0x800020,transparent:true});
   const dot=new THREE.Mesh(new THREE.SphereGeometry(1.4,16,12),md),cone=new THREE.Mesh(new THREE.ConeGeometry(3.2,8,4),mc);cone.rotation.x=Math.PI;
   const ln=new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(0,12,0),new THREE.Vector3(0,44,0)]),ml);
   const rp=[0,1].map(()=>{const r=new THREE.Mesh(new THREE.RingGeometry(3.6,4.4,48),new THREE.MeshBasicMaterial({color:0x800020,transparent:true,side:THREE.DoubleSide,depthWrite:false}));r.rotation.x=-Math.PI/2;g.add(r);return r});
   const hit=new THREE.Mesh(new THREE.SphereGeometry(10,8,6),new THREE.MeshBasicMaterial({visible:false}));hit.position.y=8;hit.userData.k=k;
   g.add(dot,cone,ln,hit);g.visible=false;JI.add(g);
   AR[k]={g,cone,hit,rp,ms:[md,mc,ml],all:[md,mc,ml,...rp.map(r=>r.material)],a:new THREE.Vector3(),n:new THREE.Vector3(),lp:new THREE.Vector3(),ok:false,c:''};
   place(k,new THREE.Vector3(...p.a),new THREE.Vector3(...p.n).normalize());AR[k].ok=false;
   const b=document.createElement('button');b.className='hs';b.innerHTML='<i></i>'+p.name+' <b></b>';b.onclick=()=>openPart(k);stage.appendChild(b);LB[k]=b});
}

// Snap every arrow onto the real surface of the jet by ray-casting from outside (called once the jet has loaded).
export function fitArrows(o) {
  const { JI } = ctx;
 o.updateMatrixWorld(true);JI.updateMatrixWorld(true);const ms=[];o.traverse(m=>{if(m.isMesh&&m.visible&&!/glass/i.test(m.material.name||''))ms.push(m)});
 const inv=new THREE.Matrix4().copy(JI.matrixWorld).invert(),rc=new THREE.Raycaster();
 KEYS.forEach(k=>{const p=PARTS[k],d=new THREE.Vector3(...p.d).normalize(),tg=new THREE.Vector3(...p.t);
  rc.set(tg.clone().addScaledVector(d,400).applyMatrix4(JI.matrixWorld),d.clone().transformDirection(JI.matrixWorld).negate());
  const h=rc.intersectObjects(ms,false)[0];if(!h)return;
  const n=h.face.normal.clone().transformDirection(h.object.matrixWorld).transformDirection(inv).normalize();if(n.dot(d)<0)n.negate();n.lerp(d,.3).normalize();
  place(k,h.point.clone().applyMatrix4(inv),n)})}
