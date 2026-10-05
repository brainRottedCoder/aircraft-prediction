import * as THREE from 'three';
import { ctx } from './context.js';
import { app } from '../state/store.js';

// Standard PBR material used by the procedural models.
export const mm = (c,m=.8,r=.4)=>new THREE.MeshStandardMaterial({color:c,metalness:m,roughness:r});

// Wrap a model in a root/spin group (optionally scaled to `size`), hidden until it is inspected.
export function mkPart(k, obj, size) {
  const { S, PR } = ctx;
 const root=new THREE.Group(),spin=new THREE.Group();
 if(size){const b=new THREE.Box3().setFromObject(obj),c=b.getCenter(new THREE.Vector3()),s=b.getSize(new THREE.Vector3());obj.position.sub(c);const w=new THREE.Group();w.add(obj);w.scale.setScalar(size/Math.max(s.x,s.y,s.z));spin.add(w)}else spin.add(obj);
 root.add(spin);root.visible=false;S.add(root);PR[k]={root,spin};app.loaded[k]=true}

// Fade a whole model in or out.
export function setOp(root,op){root.visible=op>.01;root.traverse(o=>{if(!o.isMesh)return;(Array.isArray(o.material)?o.material:[o.material]).forEach(m=>{if(m.userData.o0===undefined){m.userData.o0=m.opacity;m.userData.t0=m.transparent}
 m.opacity=m.userData.o0*op;m.transparent=m.userData.t0||op<.99;m.depthWrite=!(m.userData.t0||op<.99)})})}
