import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';

// Models are plain files in /public/models (they used to be base64 strings inside the HTML).
export const assetUrl = (name) => `${import.meta.env.BASE_URL}models/${name}`;

export async function fetchBuffer(name) {
  const r = await fetch(assetUrl(name));
  if (!r.ok) throw new Error(`${name}: HTTP ${r.status}`);
  return r.arrayBuffer();
}

export async function fetchJson(name) {
  const r = await fetch(assetUrl(name));
  if (!r.ok) throw new Error(`${name}: HTTP ${r.status}`);
  return r.json();
}

let gltf;
// Load a .glb from /public/models and resolve with its scene.
export async function loadGlb(name) {
  const buf = await fetchBuffer(name);
  gltf = gltf || new GLTFLoader();
  return new Promise((ok, no) => gltf.parse(buf, '', (g) => ok(g.scene), no));
}

// Gunzip an ArrayBuffer. If a server already decoded it (Content-Encoding: gzip) it is returned as is.
export async function gunzip(buf) {
  const u8 = new Uint8Array(buf);
  if (u8[0] !== 0x1f || u8[1] !== 0x8b) return buf;
  return new Response(new Blob([u8]).stream().pipeThrough(new DecompressionStream('gzip'))).arrayBuffer();
}
