import * as THREE from 'three';
import { ctx, rt } from '../context.js';
import { app } from '../../state/store.js';
import { fleet } from '../../data/fleet.js';
import { clamp } from '../../lib/math.js';
import { subH } from '../../lib/health.js';
import { mm, mkPart } from '../helpers.js';
import { FMSG } from '../config.js';

const _v = new THREE.Vector3();

// Fuel system: procedural tank with a fuel level, two boost pumps, valves and flowing lines.
export function buildFuelSystem() {
  const { PR, stage } = ctx;
 {const f=new THREE.Group(),V3=THREE.Vector3,steel=mm(0x93a0ad,.9,.26),dark=mm(0x1f2730,.75,.45),brass=mm(0xb98a3c,.9,.3),chrome=mm(0xeef3f8,1,.1),white=mm(0xe9edf1,.1,.6),gT=new THREE.Group(),gP=new THREE.Group(),gV=new THREE.Group(),gB=new THREE.Group();f.add(gT,gP,gV,gB);
 const cyl=(r,l,m,sg=40)=>{const o=new THREE.Mesh(new THREE.CylinderGeometry(r,r,l,sg),m);o.rotation.z=Math.PI/2;return o},cz=(r,l,m,sg=32)=>{const o=new THREE.Mesh(new THREE.CylinderGeometry(r,r,l,sg),m);o.rotation.x=Math.PI/2;return o},cy=(r,l,m,sg=28)=>new THREE.Mesh(new THREE.CylinderGeometry(r,r,l,sg),m),bx=(w,h,d,m)=>new THREE.Mesh(new THREE.BoxGeometry(w,h,d),m),add=(o,x=0,y=0,z=0,p=f)=>{o.position.set(x,y,z);p.add(o);return o},nog=m=>(m.userData.nog=1,m);
 const glass=nog(new THREE.MeshStandardMaterial({color:0xa8d8ee,metalness:.1,roughness:.06,transparent:true,opacity:.14,depthWrite:false,side:THREE.DoubleSide})),fm=nog(new THREE.MeshStandardMaterial({color:0xf2a51f,roughness:.2,transparent:true,opacity:.62,side:THREE.DoubleSide,depthWrite:false})),plane=new THREE.Plane();fm.clippingPlanes=[plane];
 const dome=(r,m,x)=>{const o=new THREE.Mesh(new THREE.SphereGeometry(r,40,24,0,Math.PI*2,0,Math.PI/2),m);o.rotation.z=x>0?-Math.PI/2:Math.PI/2;return add(o,x,0,0,gT)};
 add(cyl(14,46,glass,56),0,0,0,gT);dome(14,glass,23);dome(14,glass,-23);add(cyl(13.4,46,fm,56),0,0,0,gT);dome(13.4,fm,23);dome(13.4,fm,-23);
 const sg=add(new THREE.Group(),0,3,0,gT),surf=new THREE.Mesh(new THREE.PlaneGeometry(45,1),nog(new THREE.MeshStandardMaterial({color:0xffc24a,roughness:.1,transparent:true,opacity:.78,side:THREE.DoubleSide,depthWrite:false})));surf.rotation.x=-Math.PI/2;sg.add(surf);
 [-11.5,0,11.5].forEach(x=>add(cyl(13.6,.5,nog(new THREE.MeshStandardMaterial({color:0x9fb0c0,metalness:.5,roughness:.4,transparent:true,opacity:.28,side:THREE.DoubleSide,depthWrite:false})),32),x,0,0,gT));
 [-17,-6,6,17].forEach(x=>{const r=new THREE.Mesh(new THREE.TorusGeometry(14.5,.85,12,64),steel);r.rotation.y=Math.PI/2;add(r,x,0,0,gT)});
 [[0,14.9,0],[0,0,14.9],[0,0,-14.9]].forEach(p=>add(cyl(.6,44,steel,12),p[0],p[1],p[2],gT));
 [-10,10].forEach(x=>{const a=new THREE.Mesh(new THREE.TorusGeometry(14.7,1.2,8,40,Math.PI),dark);a.rotation.set(0,Math.PI/2,Math.PI);add(a,x,0,0,gT)});
 [-8,0,8].forEach(x=>{add(cy(.5,26,chrome,10),x,1,0,gT);add(cy(1.5,1.4,brass,14),x,14.8,0,gT);add(bx(2.8,2,2.8,dark),x,16.4,0,gT);add(new THREE.Mesh(new THREE.SphereGeometry(1,12,10),brass),x,-11.8,0).parent&&gT.add(f.children.pop())});
 add(cy(5.6,1.2,steel,40),9,15,0,gT);for(let i=0;i<8;i++){const q=i/8*Math.PI*2;add(cy(.6,1,brass,6),9+Math.cos(q)*4.4,15.9,Math.sin(q)*4.4,gT)}
 add(cy(3.2,5,dark,24),9,18,0,gT);add(cy(3.9,2,brass,24),9,21.2,0,gT);add(cy(1.8,6,steel,16),-9,17.5,0,gT);add(cy(2.6,1.4,brass,16),-9,21,0,gT);
 add(bx(40,2,22,dark),0,-16.8,0,gT);[-14,14].forEach(x=>add(bx(5,3,20,steel),x,-14.6,0,gT));[-17,17].forEach(x=>[-8,8].forEach(z=>add(cy(1,1.4,brass,6),x,-15.4,z,gT)));add(cy(1.8,3,brass,16),-18,-15.8,0,gT);
 const imps=[],pump=(x,g)=>{add(cz(5,12,dark),x,-26,-5,g);for(let i=0;i<4;i++)add(cz(5.6,.7,steel),x,-26,-2-i*2.4,g);add(cz(7,5,steel),x,-26,3,g);add(cz(5.9,.5,nog(new THREE.MeshStandardMaterial({color:0xcfe8f5,metalness:.1,roughness:.05,transparent:true,opacity:.26,depthWrite:false}))),x,-26,6.1,g);
  const im=add(new THREE.Group(),x,-26,4.6,g);for(let i=0;i<8;i++){const q=i/8*Math.PI*2,b=bx(4.2,.9,2.2,chrome);b.position.set(Math.cos(q)*3.4,Math.sin(q)*3.4,0);b.rotation.z=q+.45;im.add(b)}im.add(cz(1.6,2.6,brass));imps.push(im)};
 pump(-9,gP);pump(9,gB);add(bx(36,2,14,steel),0,-33,-1,gB);
 add(cz(3.4,1.4,dark),0,-19.6,8,gP);add(cz(2.9,.2,white),0,-19.6,8.8,gP);const nd=add(new THREE.Group(),0,-19.6,9,gP);add(bx(.35,2.5,.15,mm(0xff3b3b,.3,.5)),0,1.1,0,nd);
 const at=(c,u,o)=>{o.position.copy(c.getPointAt(u));o.quaternion.setFromUnitVectors(new V3(0,1,0),c.getTangentAt(u));return o};
 const tube=(pts,r=1.1)=>{const c=new THREE.CatmullRomCurve3(pts.map(q=>new V3(...q)),false,'catmullrom',.25);f.add(new THREE.Mesh(new THREE.TubeGeometry(c,64,r,12),nog(new THREE.MeshStandardMaterial({color:0xcdd7df,metalness:.2,roughness:.1,transparent:true,opacity:.3,depthWrite:false}))));
  const ms=[];for(let i=0;i<10;i++){const m=new THREE.Mesh(new THREE.SphereGeometry(.8,10,8),new THREE.MeshBasicMaterial({color:0xffc34d,transparent:true}));f.add(m);ms.push(m)}
  [.02,.5,.98].forEach(u=>f.add(at(c,u,cy(r*1.5,1.3,brass,16))));return {c,ms,ph:0}};
 const L=[tube([[-18,-15,0],[-20,-20,1],[-19,-25,3],[-16,-26,3]]),tube([[-2,-26,3],[2,-30,3],[10,-30,3],[16,-26,3]]),tube([[16,-26,3],[22,-26,3],[26,-22,3],[28,-14,1],[28,-6,0],[28,4,0],[34,4,0]]),tube([[-9,22,0],[-16,26,0],[-26,26,3],[-30,18,4],[-30,8,3],[-24.5,0,0]],.9)];
 [34.5,36.5].forEach(x=>{const r=new THREE.Mesh(new THREE.TorusGeometry(2.6,.8,10,24),steel);r.rotation.y=Math.PI/2;add(r,x,4,0)});
 const cB=L[2].c,fp=cB.getPointAt(.2),sp=cB.getPointAt(.5),mp=cB.getPointAt(.8);
 add(cy(4.2,12,steel,32),fp.x,fp.y-9,fp.z,gV);add(cy(5,2.4,brass,32),fp.x,fp.y-2.4,fp.z,gV);add(cy(2.2,3,dark,20),fp.x,fp.y-16,fp.z,gV);const dpi=add(cy(.9,2.6,new THREE.MeshBasicMaterial({color:0x3ddc84}),12),fp.x,fp.y+1.8,fp.z,gV);
 add(new THREE.Mesh(new THREE.SphereGeometry(3.8,24,18),steel),sp.x,sp.y,sp.z,gV);add(bx(5,4,4,dark),sp.x+5,sp.y,sp.z,gV);add(cyl(.8,4,chrome,10),sp.x+2.6,sp.y,sp.z,gV);add(cy(3,7,steel,24),mp.x,mp.y,mp.z,gV);add(bx(4,3,3,dark),mp.x+4,mp.y,mp.z,gV);
 const led=(x,y,z,g)=>add(new THREE.Mesh(new THREE.SphereGeometry(.9,12,10),new THREE.MeshBasicMaterial({color:0x3ddc84})),x,y,z,g),leds=[led(0,18.2,0,gT),led(-9,-19.4,-5,gP),led(fp.x+3.4,fp.y-2.4,fp.z+3.4,gV)];
 mkPart('fuel',f,60);
 const mats=g=>{const a=[];g.traverse(o=>{if(o.isMesh&&o.material.isMeshStandardMaterial&&!o.material.userData.nog){o.material=o.material.clone();a.push(o.material)}});return a};
 const halo=(x,y,z,r)=>{const m=new THREE.Mesh(new THREE.SphereGeometry(r,24,16),new THREE.MeshBasicMaterial({color:0xff2a2a,transparent:true,opacity:0,depthWrite:false}));add(m,x,y,z);return m};
 const drips=[0,1,2,3,4].map(()=>add(new THREE.Mesh(new THREE.SphereGeometry(.7,8,6),new THREE.MeshBasicMaterial({color:0xff5a3a,transparent:true})),10,-15,5)),pud=add(new THREE.Mesh(new THREE.CircleGeometry(5,24),new THREE.MeshBasicMaterial({color:0xff3a2a,transparent:true,opacity:0,side:THREE.DoubleSide,depthWrite:false})),10,-34,5);pud.rotation.x=-Math.PI/2;
 const tg=(c)=>{const d=document.createElement('div');d.className=c;stage.appendChild(d);return d};
 ctx.FT={t:tg('tag'),p:tg('tag'),v:tg('tag'),s:tg('fstat')};
 Object.assign(PR.fuel,{f,imp:imps[0],impB:imps[1],sg,surf,plane,flows:L,mT:mats(gT),mP:mats(gP),mV:mats(gV),leds,halos:[halo(10,-12,5,10),halo(-9,-26,2,12),halo(sp.x,sp.y,sp.z,9)],dpi,nd,drips,pud,anc:[new V3(10,-14,5),new V3(-9,-20,2),sp.clone()]});PR.fuel.spin.rotation.set(.28,-.55,0);}
}

// Fuel level, leak drips, pump speed, status LEDs and fault tags depend on the three sub-component healths.
export function animateFuel(pr, t, dt, po) {
  const { stage, cam } = ctx;
 const e=fleet[app.sel],H=[0,1,2].map(i=>subH(e,'fuel',i)??1),f=pr.f,pul=.5+.5*Math.sin(t/180),ph1=t/1000;
 const lvl=-14+28*(.62-.22*(1-clamp(H[0]/.6)))+Math.sin(t/900)*.5;pr.sg.position.y=lvl;pr.sg.rotation.set(.03*Math.sin(t/800),0,.03*Math.sin(t/1100+1));pr.surf.scale.y=2*Math.sqrt(Math.max(1,196-lvl*lvl));
 f.updateMatrixWorld(true);pr.plane.setFromNormalAndCoplanarPoint(new THREE.Vector3(0,-1,0).transformDirection(f.matrixWorld),new THREE.Vector3(0,lvl,0).applyMatrix4(f.matrixWorld));
 [pr.mT,pr.mP,pr.mV].forEach((ms,i)=>{const bad=H[i]<.6,g=bad?(.3+.5*pul)*(1.1-H[i]/.6*.6):0;ms.forEach(m=>m.emissive.setRGB(g*1.7,g*.05,g*.05))});
 const pf=.25+.75*clamp(H[1]/.6);pr.imp.rotation.z-=dt*9*pf;pr.impB.rotation.z-=dt*6;pr.nd.rotation.z=1.2-2.4*clamp(H[1]);
 pr.leds.forEach((l,i)=>l.material.color.setHex(H[i]<.4?(pul>.5?0xff2a2a:0x6a1010):H[i]<.7?0xf2b134:0x3ddc84));
 pr.halos.forEach((m,i)=>m.material.opacity=po*(H[i]<.6?.16+.2*pul:0));pr.dpi.position.y=pr.dpi.userData.y0=(pr.dpi.userData.y0??pr.dpi.position.y);pr.dpi.position.y+=H[2]<.6?2.2:0;pr.dpi.material.color.setHex(H[2]<.6?0xff2a2a:0x3ddc84);
 const sp=[.4*pf,.45*Math.min(pf,.35+.65*clamp(H[2]/.6))*(H[2]<.6?.65+.35*Math.sin(t/140):1),.45*pf,.3];
 pr.flows.forEach((q,i)=>{q.ph+=dt*sp[i];q.ms.forEach((m,j)=>{q.c.getPointAt(((q.ph+j/q.ms.length)%1+1)%1,m.position);m.material.opacity=po;m.material.color.setHex(H[i==0?0:i==3?0:i==1?1:2]<.4&&i==2?0xff6a3a:0xffc34d)})});
 const leak=H[0]<.6,sev=1-clamp(H[0]/.6);pr.drips.forEach((d,j)=>{const u=((ph1*(.7+.5*sev)+j/5)%1);d.visible=leak;d.position.y=-15-u*u*18;d.material.opacity=po});pr.pud.material.opacity=po*(leak?.25+.4*sev:0);
 const w=stage.clientWidth,h2=stage.clientHeight,bad=H.some(x=>x<.6);
 [ctx.FT.t,ctx.FT.p,ctx.FT.v].forEach((d,i)=>{if(H[i]<.6&&rt.tz==1){_v.copy(pr.anc[i]);f.localToWorld(_v);_v.project(cam);d.style.display='block';d.style.left=((_v.x+1)/2*w)+'px';d.style.top=((1-_v.y)/2*h2-30)+'px';d.style.background='#d4162f';d.textContent=FMSG[i]+', '+Math.round(H[i]*100)+'%'}else d.style.display='none'});
 const s=ctx.FT.s;s.style.display=rt.tz==1?'block':'none';s.textContent=bad?'Fault detected in fuel system':'Fuel system normal';s.style.color=bad?'#ff4d5e':'#3ddc84';s.style.borderColor=bad?'#ff4d5e':'#3ddc84'}
