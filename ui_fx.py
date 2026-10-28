"""Visual effects for the dashboard: ambient background, live HUD clock and the 3D server hall.

Both are Streamlit Custom Components v2. They are decorative and fail soft: if WebGL or the
three.js CDN is unavailable the page still works and shows a flat fallback.
"""
import streamlit as st

THREE_URL = "https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js"

# ---------------------------------------------------------------- circuit wiring
# Shared by the ambient layer and the scroll story: faint circuit traces on a grid with slow
# pulses of current running along them. makeWires(canvas) -> {resize(w, h), draw(t)}.
WIRES_JS = r"""
function makeWires(canvas, opt = {}) {
  const ctx = canvas.getContext("2d");
  const cell = opt.cell || 34, alpha = opt.alpha ?? 1;
  const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const DIRS = [[1, 0], [1, 1], [0, 1], [-1, 1], [-1, 0], [-1, -1], [0, -1], [1, -1]];
  let w = 0, h = 0, traces = [], base = null, last = 0;
  const rnd = (a, b) => a + Math.random() * (b - a);

  const build = () => {
    traces = [];
    const cols = Math.ceil(w / cell) + 1, rows = Math.ceil(h / cell) + 1, used = new Set();
    const want = Math.round((cols * rows) / 16 * (opt.density || 1));
    for (let k = 0; k < want * 4 && traces.length < want; k++) {
      let x = Math.floor(Math.random() * cols), y = Math.floor(Math.random() * rows);
      if (used.has(x + "," + y)) continue;
      let d = 2 * Math.floor(Math.random() * 4);                 // start orthogonal
      const pts = [[x, y]], seen = [x + "," + y];
      const len = Math.floor(rnd(6, 26));
      for (let i = 0; i < len; i++) {
        if (i > 1 && Math.random() < 0.2) d = (d + (Math.random() < 0.5 ? 1 : 7)) % 8;
        const nx = x + DIRS[d][0], ny = y + DIRS[d][1], key = nx + "," + ny;
        if (nx < 0 || ny < 0 || nx >= cols || ny >= rows || used.has(key) || seen.includes(key)) break;
        x = nx; y = ny; seen.push(key);
        const prev = pts[pts.length - 1], pp = pts[pts.length - 2];
        if (pp && (prev[0] - pp[0]) === DIRS[d][0] && (prev[1] - pp[1]) === DIRS[d][1]) pts[pts.length - 1] = [x, y];
        else pts.push([x, y]);
      }
      if (seen.length < 5) continue;
      seen.forEach((k2) => used.add(k2));
      const px = pts.map(([a, b]) => [a * cell, b * cell]);
      const segs = []; let total = 0;
      for (let i = 1; i < px.length; i++) {
        const l = Math.hypot(px[i][0] - px[i - 1][0], px[i][1] - px[i - 1][1]);
        segs.push(l); total += l;
      }
      traces.push({ px, segs, total, speed: rnd(18, 42), tail: rnd(50, 110),
                    s: Math.random() < 0.5 ? rnd(0, total) : -rnd(0, total * 2) });
    }
    base = document.createElement("canvas");
    base.width = canvas.width; base.height = canvas.height;
    const b = base.getContext("2d"), k = canvas.width / Math.max(w, 1);
    b.scale(k, k);
    b.lineWidth = 1.1; b.lineJoin = "round";
    b.strokeStyle = `rgba(255,255,255,${0.055 * alpha})`;
    b.fillStyle = `rgba(255,255,255,${0.09 * alpha})`;
    for (const t of traces) {
      b.beginPath(); t.px.forEach(([a, c], i) => (i ? b.lineTo(a, c) : b.moveTo(a, c))); b.stroke();
      for (const [a, c] of [t.px[0], t.px[t.px.length - 1]]) {
        b.beginPath(); b.arc(a, c, 2.4, 0, Math.PI * 2); b.stroke();
        b.beginPath(); b.arc(a, c, 0.9, 0, Math.PI * 2); b.fill();
      }
    }
  };

  const at = (t, s) => {
    s = Math.max(0, Math.min(t.total, s));
    for (let i = 0; i < t.segs.length; i++) {
      if (s <= t.segs[i] || i === t.segs.length - 1) {
        const f = t.segs[i] ? s / t.segs[i] : 0, a = t.px[i], c = t.px[i + 1];
        return [a[0] + (c[0] - a[0]) * f, a[1] + (c[1] - a[1]) * f];
      }
      s -= t.segs[i];
    }
  };

  return {
    resize(nw, nh, dpr) {
      w = nw; h = nh;
      canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      build();
    },
    draw(now, clear = true) {
      const dt = Math.min(0.1, last ? (now - last) / 1000 : 0); last = now;
      if (clear) ctx.clearRect(0, 0, w, h);
      if (!base) return;
      ctx.save(); ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.drawImage(base, 0, 0); ctx.restore();
      if (still) return;
      ctx.lineCap = "round";
      for (const t of traces) {
        t.s += t.speed * dt;
        if (t.s - t.tail > t.total) { t.s = -rnd(0, t.total * 2.5); continue; }
        if (t.s <= 0) continue;
        const n = 14, step = t.tail / n;
        let prev = at(t, t.s - t.tail);
        for (let i = 1; i <= n; i++) {
          const cur = at(t, t.s - t.tail + i * step), q = i / n;
          ctx.strokeStyle = `rgba(215,232,255,${0.5 * q * q * alpha})`;
          ctx.lineWidth = 0.8 + q * 1.1;
          ctx.beginPath(); ctx.moveTo(prev[0], prev[1]); ctx.lineTo(cur[0], cur[1]); ctx.stroke();
          prev = cur;
        }
        if (t.s < t.total) {
          const [hx, hy] = prev, g = ctx.createRadialGradient(hx, hy, 0, hx, hy, 9);
          g.addColorStop(0, `rgba(240,248,255,${0.85 * alpha})`);
          g.addColorStop(1, "rgba(200,225,255,0)");
          ctx.fillStyle = g; ctx.beginPath(); ctx.arc(hx, hy, 9, 0, Math.PI * 2); ctx.fill();
        }
      }
    },
  };
}
"""

# ---------------------------------------------------------------- ambient layer
# A fixed canvas behind the app (perspective floor grid with circuit wiring) plus the live IST
# clock for any element with class "hud-clock". Set up once per page, never per rerun.
_AMBIENT_JS = WIRES_JS + """
export default function () {
  if (window.__dcAmbient) return;
  window.__dcAmbient = true;

  const tick = () => {
    const t = new Date().toLocaleTimeString("en-GB", { timeZone: "Asia/Kolkata", hour12: false }) + " IST";
    document.querySelectorAll(".hud-clock").forEach((el) => { el.textContent = t; });
  };
  tick();
  setInterval(tick, 1000);

  const canvas = document.createElement("canvas");
  canvas.id = "dc-ambient";
  canvas.setAttribute("aria-hidden", "true");
  document.body.prepend(canvas);
  const ctx = canvas.getContext("2d");
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  let w = 0, h = 0, dpr = 1;
  const resize = () => {
    dpr = Math.min(window.devicePixelRatio || 1, 1.5);
    w = window.innerWidth; h = window.innerHeight;
    canvas.width = w * dpr; canvas.height = h * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    wires.resize(w, h, dpr);
  };
  const wires = makeWires(canvas, { density: 0.8, alpha: 0.9 });
  resize();
  window.addEventListener("resize", resize);
  let last = 0, offset = 0;

  const draw = (t) => {
    requestAnimationFrame(draw);
    if (document.hidden || t - last < 33) return;      // ~30 fps, paused in background tabs
    last = t;
    wires.draw(t);                                   // clears, then traces + current
    const horizon = h * 0.52, vx = w * 0.5;
    const proj = (x, z) => {                             // floor point -> screen
      const s = 1 / (z + 0.08);
      return [vx + x * s * w * 0.07, horizon + s * h * 0.035];
    };
    if (!reduce) offset = (offset + 0.004) % 1;
    // depth lines
    ctx.lineWidth = 1;
    for (let i = 0; i < 26; i++) {
      const z = ((i / 26) + 1 - offset) % 1;
      const y = proj(0, z * 3.2)[1];
      const a = Math.max(0, 0.11 - z * 0.1);
      ctx.strokeStyle = `rgba(230,230,230,${a})`;
      ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke();
    }
    // lateral lines converging to the vanishing point
    for (let i = -18; i <= 18; i++) {
      const [x1, y1] = proj(i * 1.2, 0.0);
      const [x2, y2] = proj(i * 1.2, 3.2);
      const g = ctx.createLinearGradient(x1, y1, x2, y2);
      g.addColorStop(0, "rgba(230,230,230,0.10)");
      g.addColorStop(1, "rgba(230,230,230,0)");
      ctx.strokeStyle = g;
      ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();
    }
  };
  requestAnimationFrame(draw);
}
"""

_ambient = st.components.v2.component("dc_ambient", html="<span></span>", js=_AMBIENT_JS)


def ambient():
    """Mount the ambient background + clock (renders nothing visible in the layout)."""
    with st.container(key="fx_ambient"):
        _ambient(key="dc_ambient")


# ---------------------------------------------------------------- 3D server hall
_HALL_HTML = """
<div class="hall" role="img" aria-label="3D view of the server hall">
  <div class="stage"></div>
  <div class="labels"></div>
  <div class="fallback">3D view unavailable — showing status only</div>
</div>
"""

_HALL_CSS = """
.hall { position: relative; width: 100%; height: 340px; overflow: hidden; border-radius: 10px;
  background: radial-gradient(80% 90% at 50% 100%, rgba(230,230,230,0.10), rgba(5,5,5,0) 70%); }
.stage, .stage canvas { position: absolute; inset: 0; width: 100% !important; height: 100% !important; }
.labels { position: absolute; inset: 0; pointer-events: none; }
.tag { position: absolute; transform: translate(-50%, -100%); font: 600 10.5px/1.25 'JetBrains Mono',
  ui-monospace, monospace; letter-spacing: .08em; color: #f2f2f2; text-align: center;
  white-space: nowrap; padding: 3px 7px; border: 1px solid rgba(230,230,230,.35);
  background: rgba(10,10,10,.78); border-radius: 3px; text-transform: uppercase; }
.tag b { display: block; font-size: 13px; letter-spacing: .02em; }
.tag.critical { border-color: #f43f5e; color: #ffd5dc; box-shadow: 0 0 14px rgba(244,63,94,.45); }
.tag.warning { border-color: #f59e0b; color: #fde7c2; box-shadow: 0 0 12px rgba(245,158,11,.35); }
.tag.plan { border-color: #3b82f6; }
.fallback { display: none; position: absolute; inset: 0; align-items: center; justify-content: center;
  font: 500 12px 'JetBrains Mono', monospace; color: #7a7a7a; letter-spacing: .08em; }
.hall.failed .fallback { display: flex; }
"""

_HALL_JS = """
const STATE = new WeakMap();
const COLORS = { CRITICAL: 0xf43f5e, WARNING: 0xf59e0b, PLAN: 0x3b82f6, HOLD: 0x64748b,
                 OK: 0x10b981, NONE: 0xe5e5e5 };

async function setup(root, THREE, setTriggerValue) {
  const stage = root.querySelector(".stage");
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  stage.appendChild(renderer.domElement);

  const scene = new THREE.Scene();
  scene.fog = new THREE.Fog(0x050505, 9, 22);
  const camera = new THREE.PerspectiveCamera(34, 2, 0.1, 60);

  scene.add(new THREE.HemisphereLight(0xdddddd, 0x02040a, 0.9));
  const key = new THREE.DirectionalLight(0xffffff, 1.4); key.position.set(4, 8, 6); scene.add(key);
  const rim = new THREE.PointLight(0xe5e5e5, 18, 14); rim.position.set(0, 3.2, -2.5); scene.add(rim);

  // floor: glowing grid + soft reflection plane
  const grid = new THREE.GridHelper(30, 60, 0xe5e5e5, 0x2a2a2a);
  grid.material.transparent = true; grid.material.opacity = 0.35; scene.add(grid);
  const floor = new THREE.Mesh(new THREE.PlaneGeometry(30, 30),
    new THREE.MeshStandardMaterial({ color: 0x0a0a0a, metalness: 0.6, roughness: 0.45 }));
  floor.rotation.x = -Math.PI / 2; floor.position.y = -0.002; scene.add(floor);

  const group = new THREE.Group(); scene.add(group);
  const airflow = new THREE.Group(); scene.add(airflow);

  const raycaster = new THREE.Raycaster(), pointer = new THREE.Vector2();
  const st = { renderer, scene, camera, group, airflow, racks: [], raf: 0, THREE, raycaster,
               pointer, hover: null, setTriggerValue, root, visible: true, t0: performance.now() };

  const resize = () => {
    const r = stage.getBoundingClientRect();
    if (!r.width || !r.height) return;
    renderer.setSize(r.width, r.height, false);
    camera.aspect = r.width / r.height; camera.updateProjectionMatrix();
  };
  st.ro = new ResizeObserver(resize); st.ro.observe(stage); resize();
  st.io = new IntersectionObserver(([e]) => { st.visible = e.isIntersecting; });
  st.io.observe(stage);

  renderer.domElement.onpointermove = (ev) => {
    const r = renderer.domElement.getBoundingClientRect();
    pointer.x = ((ev.clientX - r.left) / r.width) * 2 - 1;
    pointer.y = -((ev.clientY - r.top) / r.height) * 2 + 1;
    raycaster.setFromCamera(pointer, camera);
    const hit = raycaster.intersectObjects(st.racks.map((k) => k.body))[0];
    st.hover = hit ? hit.object.userData.name : null;
    renderer.domElement.style.cursor = st.hover && st.live ? "pointer" : "default";
  };
  renderer.domElement.onpointerleave = () => { st.hover = null; };
  renderer.domElement.onclick = () => { if (st.hover && st.live) st.setTriggerValue("rack", st.hover); };

  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  let last = 0;
  const loop = (t) => {
    st.raf = requestAnimationFrame(loop);
    if (!st.visible || document.hidden || t - last < 25) return;
    last = t;
    const s = (t - st.t0) / 1000;
    const orbit = reduce ? 0 : Math.sin(s * 0.12) * 0.35;
    const n = Math.max(st.racks.length, 1);
    const span = n * 1.35;
    camera.position.set(Math.sin(orbit) * (span + 3.5), 3.1, Math.cos(orbit) * (span + 3.5));
    camera.lookAt(0, 0.9, 0);
    for (const k of st.racks) {
      // LEDs flicker; the fill column breathes, faster when the rack is in trouble
      const leds = k.leds, c = new THREE.Color();
      for (let i = 0; i < leds.count; i++) {
        const on = reduce ? 1 : (Math.sin(s * k.rate[i] + k.phase[i]) > -0.2 ? 1 : 0.15);
        c.setHex(k.ledHex[i]).multiplyScalar(on);
        leds.setColorAt(i, c);
      }
      leds.instanceColor.needsUpdate = true;
      const pulse = k.urgent && !reduce ? 0.55 + 0.45 * Math.sin(s * 4) : 0.8;
      k.fill.material.opacity = 0.35 + 0.35 * pulse;
      k.halo.material.opacity = (k.urgent ? 0.35 : 0.12) * pulse;
      const target = st.hover === k.name ? 1.04 : 1;
      k.obj.scale.lerp(new THREE.Vector3(target, target, target), 0.15);
    }
    for (const p of st.airflow.children) {                // hot-aisle air rising
      const pos = p.geometry.attributes.position;
      for (let i = 0; i < pos.count; i++) {
        let y = pos.getY(i) + (reduce ? 0 : 0.012);
        if (y > 2.6) y = 0.1;
        pos.setY(i, y);
      }
      pos.needsUpdate = true;
    }
    renderer.render(scene, camera);
    // project label anchors into the overlay
    const r = renderer.domElement.getBoundingClientRect();
    for (const k of st.racks) {
      if (!k.tag) continue;
      const v = k.anchor.clone().applyMatrix4(k.obj.matrixWorld).project(camera);
      k.tag.style.left = `${(v.x * 0.5 + 0.5) * r.width}px`;
      k.tag.style.top = `${(-v.y * 0.5 + 0.5) * r.height}px`;
    }
  };
  st.raf = requestAnimationFrame(loop);
  return st;
}

function build(st, racks, live) {
  const { THREE, group, airflow, root } = st;
  const labels = root.querySelector(".labels");
  const disposeTree = (obj) => obj.traverse((o) => {
    if (o.geometry) o.geometry.dispose();
    if (o.material) o.material.dispose();
  });
  [...group.children].forEach((o) => { disposeTree(o); group.remove(o); });
  [...airflow.children].forEach((o) => { disposeTree(o); airflow.remove(o); });
  labels.innerHTML = "";
  st.racks = []; st.live = live;

  const n = racks.length, gap = 1.35;
  const cabinet = new THREE.BoxGeometry(0.9, 2.2, 1.0);
  racks.forEach((rk, i) => {
    const obj = new THREE.Group();
    obj.position.set((i - (n - 1) / 2) * gap, 0, 0);
    const color = COLORS[rk.status] ?? COLORS.NONE;

    const body = new THREE.Mesh(cabinet, new THREE.MeshStandardMaterial({
      color: 0x111111, metalness: 0.75, roughness: 0.35 }));
    body.position.y = 1.1; body.userData.name = rk.name; obj.add(body);
    const edges = new THREE.LineSegments(new THREE.EdgesGeometry(cabinet),
      new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0.65 }));
    edges.position.y = 1.1; obj.add(edges);

    // utilisation column behind the glass door
    const used = Math.max(0.03, Math.min(rk.used ?? 0.35, 1.2));
    const fill = new THREE.Mesh(new THREE.BoxGeometry(0.12, 2.0 * used, 0.02),
      new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.6 }));
    fill.position.set(0.33, 0.1 + used, 0.511); obj.add(fill);
    const glass = new THREE.Mesh(new THREE.PlaneGeometry(0.82, 2.1),
      new THREE.MeshBasicMaterial({ color: 0xe5e5e5, transparent: true, opacity: 0.05 }));
    glass.position.set(0, 1.1, 0.505); obj.add(glass);

    // server LEDs (instanced)
    const rows = 18, cols = 4, count = rows * cols;
    const leds = new THREE.InstancedMesh(new THREE.PlaneGeometry(0.035, 0.035),
      new THREE.MeshBasicMaterial({ color: 0xffffff }), count);
    const m = new THREE.Matrix4(), c = new THREE.Color();
    const ledHex = [], rate = [], phase = [];
    for (let r = 0; r < rows; r++) for (let q = 0; q < cols; q++) {
      const idx = r * cols + q;
      m.setPosition(-0.32 + q * 0.07, 0.2 + r * 0.105, 0.512);
      leds.setMatrixAt(idx, m);
      const hot = live && Math.random() < 0.08 && rk.status !== "OK";
      ledHex.push(hot ? color : (Math.random() < 0.8 ? 0x34d399 : 0xe5e5e5));
      rate.push(1 + Math.random() * 6); phase.push(Math.random() * 6.28);
      leds.setColorAt(idx, c.setHex(ledHex[idx]));
    }
    obj.add(leds);

    // floor halo under the rack
    const halo = new THREE.Mesh(new THREE.CircleGeometry(0.85, 40),
      new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.2,
                                    blending: THREE.AdditiveBlending, depthWrite: false }));
    halo.rotation.x = -Math.PI / 2; halo.position.y = 0.01; obj.add(halo);

    group.add(obj);
    const anchor = new THREE.Vector3(0, 2.45, 0);
    let tag = null;
    if (live) {
      tag = document.createElement("div");
      tag.className = `tag ${rk.status.toLowerCase()}`;
      tag.innerHTML = `${rk.name}<b>${Math.round(100 * rk.used)}%</b>${rk.resource}`;
      labels.appendChild(tag);
    }
    st.racks.push({ obj, body, fill, halo, leds, ledHex, rate, phase, tag, anchor, name: rk.name,
                    urgent: rk.status === "CRITICAL" || rk.status === "WARNING" });
  });

  // airflow particles in the hot aisle behind the row
  const pts = 160, arr = new Float32Array(pts * 3);
  for (let i = 0; i < pts; i++) {
    arr[i * 3] = (Math.random() - 0.5) * n * gap;
    arr[i * 3 + 1] = Math.random() * 2.6;
    arr[i * 3 + 2] = -0.75 - Math.random() * 0.5;
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.BufferAttribute(arr, 3));
  airflow.add(new THREE.Points(g, new THREE.PointsMaterial({
    color: 0xfb923c, size: 0.035, transparent: true, opacity: 0.55,
    blending: THREE.AdditiveBlending, depthWrite: false })));
}

export default function (component) {
  const { data, parentElement, setTriggerValue } = component;
  const root = parentElement.querySelector(".hall");
  if (!root) return;
  const racks = data?.racks ?? [];
  const live = !!data?.live;
  const sig = JSON.stringify(data);

  let entry = STATE.get(parentElement);
  if (!entry) {
    entry = { sig: null, st: null, ready: null };
    STATE.set(parentElement, entry);
    entry.ready = import(data.three_url)
      .then((THREE) => setup(root, THREE, setTriggerValue))
      .then((st) => { entry.st = st; return st; })
      .catch((err) => { console.warn("server hall disabled:", err); root.classList.add("failed"); });
  }
  entry.ready.then((st) => {
    if (!st) return;
    st.setTriggerValue = setTriggerValue;
    if (entry.sig !== sig) { entry.sig = sig; build(st, racks, live); }
  });

  return () => {
    const st = entry.st;
    if (st) {
      cancelAnimationFrame(st.raf);
      st.ro.disconnect(); st.io.disconnect();
      st.scene.traverse((o) => { if (o.geometry) o.geometry.dispose();
                                 if (o.material) o.material.dispose(); });
      st.renderer.dispose();
      st.renderer.domElement.remove();
    }
    STATE.delete(parentElement);
  };
}
"""

_hall = st.components.v2.component("dc_server_hall", html=_HALL_HTML, css=_HALL_CSS, js=_HALL_JS)


def server_hall(racks, live, on_rack=None):
    """3D row of racks. racks: [{name, status, used (0-1), resource}]; live=False draws
    anonymous decorative racks (used for tenant views - no capacity data is sent)."""
    return _hall(key="dc_hall", data={"racks": racks, "live": live, "three_url": THREE_URL},
                 on_rack_change=on_rack or (lambda: None))
