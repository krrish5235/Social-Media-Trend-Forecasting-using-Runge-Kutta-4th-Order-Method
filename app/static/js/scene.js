// Three.js stage with two scenes: the growth surface and the channel skyline.
// All data-to-geometry math lives in geometry.js (unit tested); this file only wires it to WebGL.
import * as THREE from '../vendor/three/three.module.min.js';
import { OrbitControls } from '../vendor/three/OrbitControls.js';
import { buildSurface, buildSkyline, dayTicks, niceTicks, compact, exampleGrid, timeFraction } from './geometry.js';

const C = { ice: 0x7de3ff, iceDeep: 0x3b8bd9, ember: 0xffa15c, rose: 0xff5d8f, grid1: 0x4b3d80, grid2: 0x2a2150 };
const VIEWS = {
  surface: { pos: [6.6, 3.9, 8.2], target: [0, 1.3, 0] },
  skyline: { pos: [0, 8.2, 10.8], target: [0, 1.0, 0] },
};
const reducedMotion = () => !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;

function makeLabel(text, { size = 0.32, color = '#cfc8e8', weight = 600, anchorX = 0.5 } = {}) {
  const fontPx = 56;
  const pad = 10;
  const measure = document.createElement('canvas').getContext('2d');
  const font = `${weight} ${fontPx}px Figtree, system-ui, sans-serif`;
  measure.font = font;
  const w = Math.ceil(measure.measureText(text).width) + pad * 2;
  const h = fontPx + pad * 2;
  const canvas = document.createElement('canvas');
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext('2d');
  ctx.font = font;
  ctx.fillStyle = color;
  ctx.textBaseline = 'middle';
  ctx.fillText(text, pad, h / 2);
  const map = new THREE.CanvasTexture(canvas);
  map.colorSpace = THREE.SRGBColorSpace;
  const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map, transparent: true, depthWrite: false, depthTest: false }));
  sprite.center.set(anchorX, 0.5);
  sprite.scale.set((size * w) / fontPx, (size * h) / fontPx, 1);
  sprite.renderOrder = 10;
  return sprite;
}

function disposeObject(obj) {
  obj.traverse((o) => {
    o.geometry?.dispose();
    const mats = Array.isArray(o.material) ? o.material : o.material ? [o.material] : [];
    mats.forEach((m) => {
      m.map?.dispose();
      m.dispose();
    });
  });
}

let starTexture = null;
function roundDot() {
  if (!starTexture) {
    const c = document.createElement('canvas');
    c.width = c.height = 32;
    const ctx = c.getContext('2d');
    const g = ctx.createRadialGradient(16, 16, 0, 16, 16, 16);
    g.addColorStop(0, 'rgba(255,255,255,1)');
    g.addColorStop(1, 'rgba(255,255,255,0)');
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, 32, 32);
    starTexture = new THREE.CanvasTexture(c);
  }
  return starTexture;
}

function makeStars(count = 300) {
  const pos = new Float32Array(count * 3);
  for (let i = 0; i < count; i += 1) {
    const r = 14 + Math.random() * 10;
    const theta = Math.random() * Math.PI * 2;
    const phi = Math.acos(2 * Math.random() - 1);
    pos[i * 3] = r * Math.sin(phi) * Math.cos(theta);
    pos[i * 3 + 1] = Math.abs(r * Math.cos(phi)) * 0.7;
    pos[i * 3 + 2] = r * Math.sin(phi) * Math.sin(theta);
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  const mat = new THREE.PointsMaterial({ color: 0xb4a8ff, size: 3, map: roundDot(), transparent: true, opacity: 0.6, sizeAttenuation: false, depthWrite: false });
  return new THREE.Points(geo, mat);
}

function tube(points, color, radius = 0.035) {
  if (points.length < 2) return null;
  const curve = new THREE.CatmullRomCurve3(points);
  const geo = new THREE.TubeGeometry(curve, Math.max(8, points.length * 2), radius, 8, false);
  return new THREE.Mesh(geo, new THREE.MeshBasicMaterial({ color }));
}

export class Stage {
  constructor(canvas, hooks = {}) {
    this.canvas = canvas;
    this.container = canvas.parentElement;
    this.hooks = hooks;
    this.supported = false;
    try {
      this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
    } catch (err) {
      return;
    }
    this.supported = true;
    this.active = 'surface';
    this.visible = true;
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    this.renderer.setClearColor(0x000000, 0);

    this.scenes = { surface: new THREE.Scene(), skyline: new THREE.Scene() };
    this.animators = { surface: [], skyline: [] };
    this.stars = {};
    for (const name of Object.keys(this.scenes)) {
      this.stars[name] = makeStars();
      this.scenes[name].add(this.stars[name]);
    }
    const sky = this.scenes.skyline;
    sky.add(new THREE.AmbientLight(0xffffff, 0.75));
    const sun = new THREE.DirectionalLight(0xffffff, 1.6);
    sun.position.set(5, 10, 6);
    sky.add(sun);

    this.camera = new THREE.PerspectiveCamera(42, 1, 0.1, 100);
    this.controls = new OrbitControls(this.camera, canvas);
    Object.assign(this.controls, { enableDamping: true, dampingFactor: 0.07, minDistance: 4, maxDistance: 24, maxPolarAngle: Math.PI * 0.49 });
    this.controls.autoRotate = !reducedMotion();
    this.controls.autoRotateSpeed = 0.7;
    this.controls.addEventListener('start', () => {
      this.controls.autoRotate = false;
      this.hooks.onAutoRotate?.(false);
    });

    this.raycaster = new THREE.Raycaster();
    this.pointer = new THREE.Vector2(2, 2);
    this.pointerInside = false;
    this.hit = null;
    this.downAt = null;
    canvas.addEventListener('pointermove', (e) => this._onMove(e));
    canvas.addEventListener('pointerleave', () => {
      this.pointerInside = false;
      this._setHit(null);
    });
    canvas.addEventListener('pointerdown', (e) => (this.downAt = [e.clientX, e.clientY]));
    canvas.addEventListener('pointerup', (e) => {
      const d = this.downAt && Math.hypot(e.clientX - this.downAt[0], e.clientY - this.downAt[1]);
      if (d != null && d < 5 && this.active === 'skyline' && this.hit) this.hooks.onSelect?.(this.hit.userData.item);
    });
    canvas.addEventListener('webglcontextlost', (e) => {
      e.preventDefault();
      this.hooks.onFallback?.();
    });

    new ResizeObserver(() => this._resize()).observe(this.container);
    new IntersectionObserver(([entry]) => (this.visible = entry.isIntersecting)).observe(this.container);
    this.clock = new THREE.Clock();
    this._resize();
    this._frame = this._frame.bind(this);
    this._raf = requestAnimationFrame(this._frame);
    this.setExample();
  }

  // -- public API ----------------------------------------------------------
  show(name) {
    if (!this.supported) return;
    this.active = name;
    this._setHit(null);
    this.resetView();
  }

  resetView() {
    if (!this.supported) return;
    const { pos, target } = VIEWS[this.active];
    const aspect = this.camera.aspect || 1;
    const k = aspect < 1 ? 1 / aspect : 1; // pull back on portrait screens so the scene keeps its width
    this.camera.position.set(pos[0] * k, pos[1] * k, pos[2] * k);
    this.controls.target.set(...target);
    this.controls.update();
  }

  setAutoRotate(on) {
    if (this.supported) this.controls.autoRotate = on && !reducedMotion();
  }

  setExample() {
    if (this.supported) this._buildSurface(exampleGrid(), [], 42000, true);
  }

  setReport(report) {
    if (!this.supported) return;
    this._buildSurface(report.forecast.grid, report.history, report.forecast.current_views, false);
    this._buildSkyline(report);
    this.resetView();
  }

  dispose() {
    cancelAnimationFrame(this._raf);
    this.controls?.dispose();
    for (const name of Object.keys(this.scenes)) this._clear(name);
    this.renderer?.dispose();
  }

  // -- internals -----------------------------------------------------------
  _resize() {
    const w = this.container.clientWidth;
    const h = this.container.clientHeight;
    if (!w || !h) return;
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    this.resetView();
  }

  _clear(name) {
    const scene = this.scenes[name];
    for (const child of [...scene.children]) {
      if (child.userData.dynamic) {
        scene.remove(child);
        disposeObject(child);
      }
    }
    this.animators[name] = [];
  }

  _dynamicGroup(name) {
    this._clear(name);
    const g = new THREE.Group();
    g.userData.dynamic = true;
    this.scenes[name].add(g);
    return g;
  }

  _buildSurface(grid, history, currentViews, isExample) {
    const root = this._dynamicGroup('surface');
    const S = buildSurface(grid);
    const { width, height, depth, tMax, vMax } = S.dims;

    // Surface coloured cool (already happened) to warm (still to come), edges dimmed.
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.BufferAttribute(S.positions, 3));
    geo.setIndex(new THREE.BufferAttribute(S.indices, 1));
    const colors = new Float32Array(S.positions.length);
    const col = new THREE.Color();
    const futureSpan = Math.max(1, S.n - 1 - S.nowIndex);
    for (let v = 0; v < S.cols.length; v += 1) {
      const i = S.cols[v];
      if (i <= S.nowIndex) col.setHex(C.iceDeep).lerp(new THREE.Color(C.ice), i / Math.max(1, S.nowIndex));
      else col.setHex(C.ember).lerp(new THREE.Color(C.rose), (i - S.nowIndex) / futureSpan);
      col.multiplyScalar(1 - 0.5 * Math.abs(S.qs[v]));
      colors.set([col.r, col.g, col.b], v * 3);
    }
    geo.setAttribute('color', new THREE.BufferAttribute(colors, 3));
    root.add(new THREE.Mesh(geo, new THREE.MeshBasicMaterial({ vertexColors: true, transparent: true, opacity: 0.6, side: THREE.DoubleSide, depthWrite: false })));

    const lineGeo = new THREE.BufferGeometry();
    lineGeo.setAttribute('position', new THREE.BufferAttribute(S.linePositions, 3));
    root.add(new THREE.LineSegments(lineGeo, new THREE.LineBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.14 })));

    // Median path: cool = model's reconstruction of the past, warm = forecast.
    const pts = S.median.map((p) => new THREE.Vector3(...p));
    [tube(pts.slice(0, S.nowIndex + 1), C.ice), tube(pts.slice(S.nowIndex), C.ember)].forEach((m) => m && root.add(m));

    // Recorded view counts.
    const stride = Math.max(1, Math.ceil(history.length / 80));
    const dotGeo = new THREE.SphereGeometry(0.06, 14, 10);
    const dotMat = new THREE.MeshBasicMaterial({ color: 0xeafaff });
    history.forEach((h, k) => {
      if (k % stride !== 0 && k !== history.length - 1) return;
      const dot = new THREE.Mesh(dotGeo, dotMat);
      dot.position.set(...S.toWorld(h.age_days, h.views));
      root.add(dot);
    });

    // "Today" marker: plane, pulsing dot.
    const planeGeo = new THREE.PlaneGeometry(depth * 1.08, height * 1.15);
    const plane = new THREE.Mesh(planeGeo, new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.05, side: THREE.DoubleSide, depthWrite: false }));
    const edges = new THREE.LineSegments(new THREE.EdgesGeometry(planeGeo), new THREE.LineBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.3 }));
    for (const o of [plane, edges]) {
      o.rotation.y = Math.PI / 2;
      o.position.set(S.nowX, height * 0.575, 0);
      root.add(o);
    }
    const nowY = (currentViews / vMax) * height;
    const marker = new THREE.Mesh(new THREE.SphereGeometry(0.11, 20, 16), new THREE.MeshBasicMaterial({ color: 0xfff1e0 }));
    marker.position.set(S.nowX, nowY, 0);
    const halo = new THREE.Mesh(new THREE.SphereGeometry(0.2, 20, 16), new THREE.MeshBasicMaterial({ color: C.ember, transparent: true, opacity: 0.35, depthWrite: false }));
    halo.position.copy(marker.position);
    root.add(marker, halo);
    if (!reducedMotion()) this.animators.surface.push((t) => halo.scale.setScalar(1 + 0.4 * Math.sin(t * 2.4)));

    // Floor, back-wall guides and labels.
    const floor = new THREE.GridHelper(12, 24, C.grid1, C.grid2);
    floor.material.transparent = true;
    floor.material.opacity = 0.55;
    root.add(floor);

    const guides = [];
    const yTicks = niceTicks(vMax, 4);
    for (const v of yTicks) {
      const y = (v / vMax) * height;
      guides.push(-width / 2, y, -depth / 2, width / 2, y, -depth / 2);
      const lab = makeLabel(compact(v), { size: 0.3, anchorX: 1 });
      lab.position.set(-width / 2 - 0.2, y, -depth / 2);
      root.add(lab);
    }
    const guideGeo = new THREE.BufferGeometry();
    guideGeo.setAttribute('position', new THREE.Float32BufferAttribute(guides, 3));
    root.add(new THREE.LineSegments(guideGeo, new THREE.LineBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.1 })));

    for (const d of dayTicks(tMax)) {
      const lab = makeLabel(`${d}d`, { size: 0.3 });
      lab.position.set((timeFraction(d, tMax) - 0.5) * width, 0.02, depth / 2 + 0.5);
      root.add(lab);
    }
    const titles = [
      ['Days since upload', 0, -0.05, depth / 2 + 1.7, 0.36],
      ['Views', -width / 2 - 0.2, (yTicks[yTicks.length - 1] / vMax) * height + 0.5, -depth / 2, 0.36],
      ['High case', width / 2 + 0.25, 0.02, depth / 2, 0.3],
      ['Low case', width / 2 + 0.25, 0.02, -depth / 2, 0.3],
      [isExample ? 'Example' : 'Today', S.nowX, height * 1.2, 0, 0.34],
    ];
    for (const [text, x, y, z, size] of titles) {
      const lab = makeLabel(text, { size, color: text === 'Today' || text === 'Example' ? '#ffd9bd' : '#9d94bd', weight: 700, anchorX: text.endsWith('case') ? 0 : 0.5 });
      lab.position.set(x, y, z);
      root.add(lab);
    }
  }

  _buildSkyline(report) {
    const root = this._dynamicGroup('skyline');
    const v = report.video;
    const target = {
      id: v.id, url: v.url, title: v.title, age_days: v.age_days, views: v.views, likes: v.likes, comments: v.comments,
      performance_index: report.signals.performance_index,
    };
    const sky = buildSkyline(report.peers, target);

    const floorW = sky.extentX + 2;
    const floorD = sky.extentZ + 2;
    const floor = new THREE.Mesh(new THREE.PlaneGeometry(floorW, floorD), new THREE.MeshBasicMaterial({ color: 0x181229, transparent: true, opacity: 0.9 }));
    floor.rotation.x = -Math.PI / 2;
    floor.position.y = -0.01;
    root.add(floor);
    const grid = new THREE.GridHelper(Math.max(floorW, floorD), Math.round(Math.max(floorW, floorD) / sky.spacing), C.grid1, C.grid2);
    grid.material.transparent = true;
    grid.material.opacity = 0.5;
    root.add(grid);

    const boxGeo = new THREE.BoxGeometry(0.66, 1, 0.66);
    this.bars = [];
    for (const t of sky.towers) {
      const color = new THREE.Color().setHSL(t.hue / 360, t.isTarget ? 0.95 : 0.7, t.isTarget ? 0.62 : 0.5);
      const mat = new THREE.MeshStandardMaterial({ color, roughness: 0.55, metalness: 0.1, emissive: color, emissiveIntensity: t.isTarget ? 0.55 : 0.12 });
      const bar = new THREE.Mesh(boxGeo, mat);
      bar.scale.y = t.height;
      bar.position.set(t.x, t.height / 2, t.z);
      bar.userData.item = t;
      bar.userData.baseEmissive = mat.emissiveIntensity;
      root.add(bar);
      this.bars.push(bar);
      if (t.isTarget) {
        const beam = new THREE.Mesh(new THREE.CylinderGeometry(0.3, 0.3, 7, 24, 1, true), new THREE.MeshBasicMaterial({ color: 0xff9a55, transparent: true, opacity: 0.22, side: THREE.DoubleSide, depthWrite: false, blending: THREE.AdditiveBlending }));
        beam.position.set(t.x, t.height + 3.5, t.z);
        const ring = new THREE.Mesh(new THREE.RingGeometry(0.52, 0.64, 48), new THREE.MeshBasicMaterial({ color: C.ember, transparent: true, opacity: 0.8, side: THREE.DoubleSide }));
        ring.rotation.x = -Math.PI / 2;
        ring.position.set(t.x, 0.02, t.z);
        const tag = makeLabel('This Short', { size: 0.38, color: '#ffd9bd', weight: 700 });
        tag.position.set(t.x, t.height + 0.7, t.z);
        root.add(beam, ring, tag);
        if (!reducedMotion()) this.animators.skyline.push((time) => ring.scale.setScalar(1 + 0.25 * Math.sin(time * 2.4)));
      }
    }
    const front = makeLabel('Newest', { size: 0.34, color: '#9d94bd', weight: 700 });
    front.position.set(0, 0.05, floorD / 2 + 0.4);
    const back = makeLabel('Oldest', { size: 0.34, color: '#9d94bd', weight: 700 });
    back.position.set(0, 0.05, -floorD / 2 - 0.4);
    root.add(front, back);
  }

  _onMove(e) {
    const r = this.canvas.getBoundingClientRect();
    this.pointer.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
    this.pointerInside = true;
    this.lastClient = [e.clientX - r.left, e.clientY - r.top];
  }

  _setHit(bar) {
    if (this.hit === bar) return;
    if (this.hit) this.hit.material.emissiveIntensity = this.hit.userData.baseEmissive;
    this.hit = bar;
    if (bar) bar.material.emissiveIntensity = 0.9;
    this.canvas.classList.toggle('is-pickable', !!bar);
    if (!bar) this.hooks.onHover?.(null);
  }

  _raycast() {
    if (!this.pointerInside || !this.bars) return;
    this.raycaster.setFromCamera(this.pointer, this.camera);
    const [first] = this.raycaster.intersectObjects(this.bars, false);
    this._setHit(first ? first.object : null);
    if (first) this.hooks.onHover?.(first.object.userData.item, this.lastClient[0], this.lastClient[1]);
  }

  _frame(ms) {
    this._raf = requestAnimationFrame(this._frame);
    if (!this.visible || document.hidden) return;
    const dt = Math.min(this.clock.getDelta(), 0.1);
    this.controls.update();
    for (const fn of this.animators[this.active]) fn(ms / 1000, dt);
    this.stars[this.active].rotation.y += dt * 0.015;
    if (this.active === 'skyline') this._raycast();
    this.renderer.render(this.scenes[this.active], this.camera);
  }
}
