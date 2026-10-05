import * as THREE from 'three';
import { ctx, rt } from '../context.js';
import { ENG } from '../../data/parts.js';
import { comp } from '../../lib/health.js';
import { mkPart } from '../helpers.js';
import { fetchJson, fetchBuffer, gunzip } from '../loaders.js';

// Turbofan engine. Geometry is quantised mesh data (public/models/engine.bin.gz + engine.meta.json).
// A shader patch paints four damage zones (fan, HPC, HPT, LPT) red according to each module's health.

const { TG } = ctx;
const ZC = { fan: 0.87, hpc: 0.61, hpt: 0.415, lpt: 0.17 }; // zone centres along the engine axis (0..1)
let H, LEN, XMIN, EG, U;
let _vv;
const vec = () => _vv || (_vv = new THREE.Vector3());

function patch(m){m.onBeforeCompile=sh=>{Object.assign(sh.uniforms,U);
 sh.vertexShader=sh.vertexShader.replace('#include <common>','#include <common>\nvarying float vT;').replace('#include <begin_vertex>','#include <begin_vertex>\nvT=(position.x-('+(XMIN+H.c[0])+'))/'+LEN.toFixed(5)+';');
 sh.fragmentShader=sh.fragmentShader.replace('#include <common>','#include <common>\nvarying float vT;uniform vec4 uH;uniform float uT;\nfloat zn(float a,float b,float t){return smoothstep(a-.03,a+.03,t)*(1.-smoothstep(b-.03,b+.03,t));}')
 .replace('#include <color_fragment>','#include <color_fragment>\nfloat dmg=zn(0.,.35,vT)*(1.-uH.w)+zn(.35,.48,vT)*(1.-uH.z)+zn(.48,.74,vT)*(1.-uH.y)+zn(.74,1.01,vT)*(1.-uH.x);\ndiffuseColor.rgb=mix(diffuseColor.rgb,vec3(.9,.03,.03),smoothstep(.15,.6,dmg));')
 .replace('#include <emissivemap_fragment>','#include <emissivemap_fragment>\ntotalEmissiveRadiance+=vec3(1.,.04,.04)*smoothstep(.35,.65,dmg)*.45;');};}

export async function loadEngine() {
  const [meta, raw] = await Promise.all([fetchJson('engine.meta.json'), fetchBuffer('engine.bin.gz')]);
  const buf = await gunzip(raw);
  if (ctx.disposed) return;
  H = meta; LEN = H.len; XMIN = H.xmin;
  EG = new THREE.Group();
  U = { uH: { value: new THREE.Vector4(1, 1, 1, 1) }, uT: { value: 0 } };
  const p4 = (n) => (n + 3) & ~3; let o = 0;
   H.meshes.forEach(m=>{const P=new Int16Array(buf,o,m.nv*3);o+=p4(m.nv*6);const N=new Int8Array(buf,o,m.nv*3);o+=p4(m.nv*3);
    const I=m.w?new Uint32Array(buf,o,m.ni):new Uint16Array(buf,o,m.ni);o+=p4(m.ni*(m.w?4:2));
    const pf=new Float32Array(m.nv*3),nf=new Float32Array(m.nv*3);for(let i=0;i<pf.length;i++){pf[i]=P[i]/32767*H.hs+H.c[i%3];nf[i]=N[i]/127}
    const g=new THREE.BufferGeometry();g.setAttribute('position',new THREE.BufferAttribute(pf,3));g.setAttribute('normal',new THREE.BufferAttribute(nf,3));g.setIndex(new THREE.BufferAttribute(I,1));
    const mat=new THREE.MeshStandardMaterial({metalness:m.m,roughness:Math.max(.2,m.r),side:THREE.DoubleSide,envMapIntensity:1.1});mat.color.setRGB(m.c[0],m.c[1],m.c[2]);patch(mat);EG.add(new THREE.Mesh(g,mat))});
   EG.position.x=-H.c[0];const w=new THREE.Group();w.add(EG);w.scale.setScalar(15);mkPart('eng',w,0);
}

// One red "degraded" tag per engine module.
export function createEngineTags() {
  const { stage } = ctx;
  Object.keys(ENG).forEach(k=>{const t=document.createElement('div');t.className='tag';stage.appendChild(t);TG[k]=t});
}

export function hideEngineTags() {
  Object.values(TG).forEach((x) => (x.style.display = 'none'));
}

// Per-frame: push module healths into the shader and place the tags over degraded modules.
export function animateEngine(pr, e, t) {
  const { stage, cam } = ctx;
  const v = vec();
  // Module health now comes from the model's component_health, and is null until the
  // engine endpoint answers. `null < .6` is true, so an unanswered module used to tag
  // itself as fully degraded the moment the server stopped responding.
  const hs=Object.keys(ENG).map(k=>comp(e,k)??1);U.uH.value.set(hs[0],hs[1],hs[2],hs[3]);U.uT.value=t/1000;
     Object.keys(ENG).forEach((k,i)=>{const g=hs[i],tg=TG[k];if(g<.6&&rt.tz==1){v.set(XMIN+ZC[k]*LEN+H.c[0],.85,0);EG.localToWorld(v);v.project(cam);tg.style.display='block';tg.style.left=((v.x+1)/2*stage.clientWidth)+'px';tg.style.top=((1-v.y)/2*stage.clientHeight-8)+'px';tg.textContent=ENG[k]+' '+Math.round((1-g)*100)+'% degraded'}else tg.style.display='none'})
}
