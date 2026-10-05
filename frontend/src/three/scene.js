import * as THREE from 'three';
import { ctx } from './context.js';
import { JC } from '../data/parts.js';

// Renderer, camera, lighting/environment, the jet pivot (JP > JI) and the floor ring.
export function createScene(stage) {
  const R=new THREE.WebGLRenderer({antialias:true,alpha:true});
  R.setPixelRatio(Math.min(devicePixelRatio,1.5));R.outputEncoding=THREE.sRGBEncoding;R.toneMapping=THREE.ReinhardToneMapping;R.toneMappingExposure=1.15;
  R.localClippingEnabled=true;stage.insertBefore(R.domElement,stage.firstChild);
  const S=new THREE.Scene(),cam=new THREE.PerspectiveCamera(38,1,1,2000);
  (function env(){const c=document.createElement('canvas');c.width=512;c.height=256;const x=c.getContext('2d');
   const g=x.createLinearGradient(0,0,0,256);g.addColorStop(0,'#fff9f2');g.addColorStop(.5,'#b3a096');g.addColorStop(1,'#4a2a30');x.fillStyle=g;x.fillRect(0,0,512,256);
   x.fillStyle='#fff';[[60,50,90,26],[250,40,120,22],[420,60,70,30]].forEach(r=>x.fillRect(r[0],r[1],r[2],r[3]));
   const t=new THREE.CanvasTexture(c);t.mapping=THREE.EquirectangularReflectionMapping;t.encoding=THREE.sRGBEncoding;
   S.environment=new THREE.PMREMGenerator(R).fromEquirectangular(t).texture;})();
  const dl=new THREE.DirectionalLight(0xffffff,.9);dl.position.set(80,150,120);S.add(dl);S.add(new THREE.AmbientLight(0x8899aa,.3));
  const JP=new THREE.Group(),JI=new THREE.Group();JI.position.set(-JC[0],-JC[1],-JC[2]);JP.add(JI);S.add(JP);
  const ring=new THREE.Mesh(new THREE.RingGeometry(88,90,96),new THREE.MeshBasicMaterial({color:0x800020,transparent:true,opacity:.16,side:THREE.DoubleSide}));
  ring.rotation.x=-Math.PI/2;ring.position.y=-34;S.add(ring);
  Object.assign(ctx, { stage, R, S, cam, JP, JI, ring });
}

export function resize() {
  const { stage, R, cam } = ctx;
  const w = stage.clientWidth, h = stage.clientHeight;
  R.setSize(w, h);
  cam.aspect = w / h;
  cam.updateProjectionMatrix();
}
