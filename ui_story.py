"""Scroll-driven opening for the operator overview (Custom Component v2).

Three acts, all driven by how far the page is scrolled through a tall pinned stage:
  1. Campus: aerial view of the data-centre campus in black and white (halls with rooftop
     condensers, generator yards, substation), fans turning; the camera dives onto one hall.
  2. Hall: the camera drops through the roof and visits each monitored rack in turn, showing that
     rack's combined resource chart (hover any rack to inspect it instead).
  3. Tenants: the camera rises to a top view of the rack row; each rack shows a treemap of how
     much of its capacity every tenant uses.
Charts and treemaps draw themselves the first time they appear. The overlays work without
WebGL; only the 3D scene needs the three.js CDN.
"""
import hashlib

import streamlit as st

from ui_fx import WIRES_JS

THREE_URL = "https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js"

_HTML = """
<div class="story">
  <div class="stage">
    <div class="gl" aria-hidden="true"></div>
    <canvas class="wires" aria-hidden="true"></canvas>
    <div class="shade"></div>

    <section class="ov ov-hero">
      <p class="sys"><span class="led"></span>AO-02 <i>//</i> capacity early warning <i>//</i> read-only</p>
      <p class="brand"><b>Fore<span>Sight</span></b></p>
      <h1>Seeing trouble before<br>the capacity runs out</h1>
      <p class="lede"></p>
      <dl class="hud"></dl>
      <div class="cue"><span>Scroll to enter the hall</span><b></b></div>
    </section>

    <section class="ov ov-rack">
      <ol class="steps"></ol>
      <div class="card">
        <div class="card-h"><span class="rk"></span><span class="chip"></span></div>
        <p class="eta"></p>
        <svg class="chart" viewBox="0 0 560 280" role="img"></svg>
        <div class="legend"></div>
        <p class="hint">% of capacity · solid = actual (<span class="smooth-win">7-day</span> median) · dashed = P50 forecast ·
          hover a rack to inspect it</p>
      </div>
    </section>

    <section class="ov ov-ten">
      <div class="ten-h">
        <p class="sys">Tenant consumption <i>//</i> share of each rack's capacity</p>
        <div class="seg" role="group" aria-label="Workload"></div>
      </div>
      <div class="maps"><div class="mi"></div></div>
      <div class="tlegend"></div>
    </section>

    <div class="rail"><i></i></div>
  </div>
</div>
"""

_CSS = """
:host { --w: #f2f2f2; --g1: #a3a3a3; --g2: #6b6b6b; --g3: #2a2a2a; --ink: #050505;
  --crit: #f43f5e; --warn: #f59e0b; --plan: #3b82f6; --hold: #8a8a8a; --ok: #10b981;
  --mono: 'JetBrains Mono', ui-monospace, monospace; --head: 'Space Grotesk', Inter, sans-serif; }
.story { position: relative; height: 640vh; margin: 0 0 1.5rem; }
.stage { position: absolute; top: 0; left: 0; right: 0; height: 100vh; overflow: hidden;
  background: var(--ink); border: 1px solid #1d1d1d; border-radius: 6px; will-change: transform; }
.gl, .gl canvas { position: absolute; inset: 0; width: 100% !important; height: 100% !important; }
.wires { position: absolute; inset: 0; width: 100%; height: 100%; pointer-events: none;
  mix-blend-mode: screen; }
.shade { position: absolute; inset: 0; pointer-events: none;
  background: radial-gradient(120% 90% at 50% 40%, transparent 45%, rgba(0,0,0,.75) 100%); }
.ov { position: absolute; inset: 0; pointer-events: none; opacity: 0; color: var(--w);
  transition: opacity .25s linear; }
.sys { font: 600 11px var(--mono); letter-spacing: .18em; text-transform: uppercase; color: var(--g1);
  display: flex; align-items: center; gap: .55rem; margin: 0; }
.sys i { color: var(--g2); font-style: normal; }
.led { width: 7px; height: 7px; border-radius: 50%; background: var(--crit);
  box-shadow: 0 0 10px var(--crit); animation: blink 1s steps(1) infinite; }
.led.ok { background: var(--ok); box-shadow: 0 0 10px var(--ok); }
@keyframes blink { 50% { opacity: .2; } }

/* act 1 */
.ov-hero { padding: 3.2rem 3rem 7rem; display: flex; flex-direction: column; justify-content: flex-end;
  background: linear-gradient(0deg, rgba(5,5,5,.92) 0%, rgba(5,5,5,.35) 45%, transparent 70%); }
.brand b { font-weight: inherit; }
.brand { display: flex; align-items: center; gap: .9rem; margin: 1.1rem 0 0;
  font: 600 clamp(1.25rem, 1rem + .8vw, 1.7rem)/1 var(--head); letter-spacing: -.02em; color: #fff; }
.brand span { color: var(--g1); }
.brand::after { content: ""; width: 4.5rem; height: 1px; background: linear-gradient(90deg, #6b6b6b, transparent); }
.ov-hero h1 { font: 600 clamp(2rem, 1.2rem + 3vw, 3.6rem)/1.02 var(--head); letter-spacing: -.035em;
  margin: .45rem 0 .6rem; color: #fff; }
.lede { font: 400 15px/1.5 Inter, sans-serif; color: #bdbdbd; max-width: 38rem; margin: 0 0 1.2rem; }
.hud { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 1px; margin: 0;
  max-width: 62rem; background: #2c2c2c; border: 1px solid #2c2c2c; border-radius: 4px; overflow: hidden; }
.hud div { background: rgba(8,8,8,.9); padding: .6rem .75rem; }
.hud dt { font: 600 10px var(--mono); letter-spacing: .16em; text-transform: uppercase; color: var(--g2); }
.hud dd { font: 600 16px var(--mono); margin: .2rem 0 0; color: #fff; white-space: nowrap; }
.hud dd.crit { color: var(--crit); text-shadow: 0 0 14px rgba(244,63,94,.6); }
.hud dd.warn { color: var(--warn); }
.hud dd.plan { color: var(--plan); }
.cue { position: absolute; right: 3rem; bottom: 7rem; display: flex; flex-direction: column;
  align-items: center; gap: .6rem; font: 600 10px var(--mono); letter-spacing: .2em;
  text-transform: uppercase; color: var(--g1); }
.cue b { width: 1px; height: 46px; background: linear-gradient(var(--w), transparent);
  animation: drip 1.8s ease-in-out infinite; transform-origin: top; }
@keyframes drip { 0% { transform: scaleY(0); } 50% { transform: scaleY(1); } 100% { transform: scaleY(1); opacity: 0; } }

/* act 2 */
.steps { position: absolute; left: 2rem; bottom: 1.6rem; list-style: none;
  margin: 0; padding: 0; display: flex; flex-wrap: wrap; gap: .7rem 1.4rem; max-width: 46%; }
.steps li { font: 600 11px var(--mono); letter-spacing: .14em; text-transform: uppercase;
  color: var(--g2); display: flex; align-items: center; gap: .6rem; transition: color .3s; }
.steps li::before { content: ""; width: 18px; height: 1px; background: currentColor; transition: width .3s; }
.steps li.on { color: #fff; }
.steps li.more { color: var(--g2); letter-spacing: .08em; }
.steps li.more::before { display: none; }
.steps li.on::before { width: 34px; }
.steps li .d { width: 7px; height: 7px; border-radius: 50%; }
.card { position: absolute; right: 2rem; top: 50%; transform: translateY(-50%);
  width: min(600px, 54%); pointer-events: auto; padding: 1rem 1.1rem .8rem;
  background: rgba(10,10,10,.82); border: 1px solid #2e2e2e; border-radius: 5px;
  backdrop-filter: blur(6px); box-shadow: 0 30px 80px -30px #000; }
.card::before, .card::after { content: ""; position: absolute; width: 14px; height: 14px;
  border: solid #fff; opacity: .7; }
.card::before { top: -1px; left: -1px; border-width: 2px 0 0 2px; }
.card::after { bottom: -1px; right: -1px; border-width: 0 2px 2px 0; }
.card-h { display: flex; align-items: center; gap: .7rem; }
.rk { font: 600 22px var(--head); letter-spacing: -.02em; text-transform: uppercase; }
.chip { font: 700 10px var(--mono); letter-spacing: .12em; padding: .2rem .45rem; border-radius: 2px;
  border: 1px solid currentColor; }
.eta { font: 400 13px/1.45 Inter, sans-serif; color: #c9c9c9; margin: .35rem 0 .4rem; min-height: 2.9em; }
.chart { width: 100%; height: auto; display: block; overflow: visible; }
.chart .grid { stroke: #232323; stroke-width: 1; }
.chart .yt, .chart .xt { font: 500 10px var(--mono); fill: #7a7a7a; }
.chart .yt { text-anchor: end; }
.chart .xt { text-anchor: middle; }
.chart .axis { transform: translateY(22px); opacity: 0;
  transition: transform .8s cubic-bezier(.2,.7,.2,1), opacity .8s; }
.chart .axis.in { transform: none; opacity: 1; }
.chart .cap { stroke: #9a9a9a; stroke-dasharray: 2 4; }
.chart .today { stroke: #6b6b6b; stroke-dasharray: 2 3; }
.chart .lbl { font: 600 9.5px var(--mono); letter-spacing: .08em; }
.legend { display: flex; flex-wrap: wrap; gap: .3rem 1rem; margin-top: .35rem;
  font: 500 11px var(--mono); color: #bdbdbd; }
.legend span { display: inline-flex; align-items: center; gap: .4rem; }
.legend i { width: 14px; height: 2px; display: inline-block; }
.hint { font: 500 10.5px var(--mono); color: var(--g2); margin: .5rem 0 0; letter-spacing: .02em; }

/* act 3 */
.ten-h { position: absolute; top: 5.2rem; left: 2rem; right: 2rem; display: flex;
  align-items: center; justify-content: space-between; gap: 1rem; }
.seg { display: flex; gap: 0; pointer-events: auto; border: 1px solid #3a3a3a; border-radius: 3px; overflow: hidden; }
.seg button { font: 600 11px var(--mono); letter-spacing: .12em; text-transform: uppercase;
  color: #bdbdbd; background: rgba(10,10,10,.85); border: 0; padding: .5rem .85rem; cursor: pointer; }
.seg button + button { border-left: 1px solid #3a3a3a; }
.seg button.on { background: #f2f2f2; color: #050505; }
.maps { position: absolute; inset: 0; }
.mi { position: absolute; inset: 0; will-change: transform; }
/* many racks: a grid of readable cards between header and legend, scrolled by the page */
.maps.grid { top: 104px; bottom: 64px; overflow: hidden;
  -webkit-mask-image: linear-gradient(transparent, #000 18px, #000 calc(100% - 18px), transparent);
  mask-image: linear-gradient(transparent, #000 18px, #000 calc(100% - 18px), transparent); }
.map { position: absolute; transform: translate(-50%, -50%); }
.map .tm { position: relative; width: 100%; aspect-ratio: 1 / 1.15; border: 1px solid #5a5a5a;
  background: rgba(8,8,8,.45); }
.in .tile:not(.free) { opacity: .9; }
.tile { position: absolute; box-sizing: border-box; border: 1px solid rgba(5,5,5,.9); overflow: hidden;
  transform: scale(.2); opacity: 0; transform-origin: top left;
  transition: transform .7s cubic-bezier(.2,.8,.2,1), opacity .5s; }
.in .tile { transform: none; opacity: 1; }
.tile span { position: absolute; left: 6px; top: 5px; font: 600 10.5px/1.25 var(--mono); color: #050505; }
.tile span b { display: block; font-size: 15px; }
.tile.free { background: repeating-linear-gradient(135deg, rgba(255,255,255,.07) 0 6px, transparent 6px 12px); }
.tile.free span { color: #9a9a9a; }
.map h4 { font: 600 12px var(--mono); letter-spacing: .14em; text-transform: uppercase; margin: 0 0 .4rem;
  display: flex; justify-content: space-between; color: #fff; }
.map h4 em { font-style: normal; color: var(--g1); }
.bar { height: 6px; margin-top: .45rem; background: #222; position: relative; }
.bar i { position: absolute; inset: 0 auto 0 0; background: #f2f2f2; width: 0;
  transition: width 1s cubic-bezier(.2,.7,.2,1); }
.bar i.hot { background: var(--warn); }
.bar i.crit { background: var(--crit); }
.bar u { position: absolute; top: -3px; bottom: -3px; width: 2px; background: var(--warn); }
.tlegend { position: absolute; left: 2rem; right: 2rem; bottom: 1.5rem; display: flex; flex-wrap: wrap;
  gap: .4rem 1.2rem; font: 500 11px var(--mono); color: #c9c9c9; }
.tlegend span { display: inline-flex; gap: .45rem; align-items: center; }
.tlegend i { width: 10px; height: 10px; display: inline-block; }
.tlegend .note { margin-left: auto; color: var(--g2); }

.rail { position: absolute; right: 10px; top: 12%; bottom: 12%; width: 2px; background: #1e1e1e; }
.rail i { position: absolute; left: 0; right: 0; top: 0; height: 0; background: #f2f2f2; }

@media (prefers-reduced-motion: reduce) {
  .led, .cue b { animation: none; }
  .tile, .chart .axis, .bar i { transition: none; }
}
@media (max-width: 900px) {
  .hud { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  .card { left: 1rem; right: 1rem; width: auto; top: auto; bottom: 1rem; transform: none; }
  .steps { display: none; }
}
"""

_JS = WIRES_JS + r"""
const STATE = new WeakMap();
const SEV = { CRITICAL: "#f43f5e", WARNING: "#f59e0b", PLAN: "#3b82f6", HOLD: "#8a8a8a", OK: "#10b981" };
const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
const smooth = (a, b, v) => { const t = clamp((v - a) / (b - a), 0, 1); return t * t * (3 - 2 * t); };
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const REDUCE = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

// ------------------------------------------------------------ timeline
// p in [0,1] = how far the pinned stage has been scrolled.
const ACT = { roofEnd: 0.16, hallStart: 0.18, hallEnd: 0.74, tenStart: 0.82 };
const STORY_VH = 640;                 // must match .story height in the CSS
const TOUR_MAX = 8;                   // racks the camera visits; hover reaches every rack
const COLS_MAX = 12, GAP_X = 1.35, ROW_GAP = 7;
const RANK = { CRITICAL: 0, WARNING: 1, PLAN: 2, HOLD: 3, OK: 4 };

// Racks in rows of at most COLS_MAX, row 0 at the front (+z); every rack faces +z.
function rackLayout(n) {
  const cols = Math.max(1, Math.min(n, COLS_MAX)), rows = Math.ceil(n / cols);
  const xs = [], zs = [];
  for (let i = 0; i < n; i++) {
    xs.push(((i % cols) - (cols - 1) / 2) * GAP_X);
    zs.push(((rows - 1) / 2 - Math.floor(i / cols)) * ROW_GAP);
  }
  return { xs, zs, cols, rows, spanX: (cols - 1) * GAP_X + 1, spanZ: (rows - 1) * ROW_GAP + 1 };
}

// The racks worth a guided stop: most urgent first, then visited in floor order.
function tourOf(racks) {
  return racks.map((rk, i) => i)
    .sort((a, b) => (RANK[racks[a].status] ?? 5) - (RANK[racks[b].status] ?? 5) || a - b)
    .slice(0, TOUR_MAX).sort((a, b) => a - b);
}

function rackAt(p, n) {
  const seg = (ACT.hallEnd - ACT.hallStart) / n;
  return clamp(Math.floor((p - ACT.hallStart) / seg), 0, n - 1);
}

function keyframes(L, tour, topY) {
  const n = tour.length, seg = (ACT.hallEnd - ACT.hallStart) / n, k = [];
  const X = (i) => L.xs[tour[i]], Z = (i) => L.zs[tour[i]];
  k.push({ p: 0.00, pos: [-22, 46, 52], look: [-5, 0, -9] });       // aerial view of the campus
  k.push({ p: 0.09, pos: [0, 14, 3.4], look: [0, 0, 0] });          // over the monitored hall
  k.push({ p: ACT.hallStart, pos: [X(0) - 3.5, 3.8, Z(0) + 6.5], look: [X(0), 1, Z(0)] });
  tour.forEach((_, i) => {
    const x = X(i), z = Z(i), side = i % 2 ? -1 : 1;
    if (i && z !== Z(i - 1))             // next row: lift over the racks before dropping in
      k.push({ p: ACT.hallStart + seg * (i + 0.12), pos: [(X(i - 1) + x) / 2 + 1.5, 4.6, Z(i - 1) + 5.2],
               look: [x + 1.5, 1, z] });
    // swing in from alternating sides; aim right of the rack so it sits beside the chart card
    k.push({ p: ACT.hallStart + seg * (i + 0.40), pos: [x + 1.7 + side * 1.0, 2.5, z + 5.6], look: [x + 1.5, 1.1, z] });
    k.push({ p: ACT.hallStart + seg * (i + 0.90), pos: [x + 1.5 + side * 0.4, 2.0, z + 5.0], look: [x + 1.5, 1.15, z] });
  });
  k.push({ p: 0.79, pos: [0, 6.5 + L.spanZ * 0.3, 6.5 + L.spanZ / 2], look: [0, 0.6, 0] });
  k.push({ p: ACT.tenStart + 0.02, pos: [0, topY * 1.05, 0.3], look: [0, 0, 0] });
  k.push({ p: 1.00, pos: [0, topY, 0.28], look: [0, 0, 0] });
  return k;
}

function sampleCam(k, p) {
  let i = 0;
  while (i < k.length - 2 && p > k[i + 1].p) i++;
  const a = k[i], b = k[i + 1];
  const t = smooth(a.p, b.p, p);
  const mix = (u, v) => u.map((x, j) => x + (v[j] - x) * t);
  return { pos: mix(a.pos, b.pos), look: mix(a.look, b.look) };
}

// ------------------------------------------------------------ charts
function chartSVG(rk, ticks, uid) {
  const W = 560, H = 280, L = 40, R = 12, T = 18, B = 26, x0 = -180, x1 = 175;
  const ymax = Math.max(120, Math.ceil(rk.ymax / 20) * 20);
  const X = (d) => L + ((d - x0) / (x1 - x0)) * (W - L - R);
  const Y = (v) => T + (1 - v / ymax) * (H - T - B);
  let axis = "";
  for (let v = 0; v <= ymax; v += 20)
    axis += `<line class="grid" x1="${L}" x2="${W - R}" y1="${Y(v)}" y2="${Y(v)}"/>` +
            `<text class="yt" x="${L - 7}" y="${Y(v) + 3}">${v}</text>`;
  for (const [d, lab] of ticks) axis += `<text class="xt" x="${X(d)}" y="${H - 6}">${esc(lab)}</text>`;
  axis += `<line class="cap" x1="${L}" x2="${W - R}" y1="${Y(100)}" y2="${Y(100)}"/>` +
          `<text class="yt" x="${W - R}" y="${Y(100) - 5}" style="text-anchor:end">capacity</text>` +
          `<line class="today" x1="${X(0)}" x2="${X(0)}" y1="${T}" y2="${H - B}"/>` +
          `<text class="xt" x="${X(0)}" y="${T - 6}">TODAY</text>`;
  const path = (pts) => pts.map(([d, v], i) => `${i ? "L" : "M"}${X(d).toFixed(1)},${Y(v).toFixed(1)}`).join("");
  let lines = "", marks = "";
  for (const s of rk.series) {
    const w = s.alert ? 2.4 : 1.6;
    lines += `<path d="${path(s.actual)}" fill="none" stroke="${s.color}" stroke-width="${w}"/>`;
    if (s.forecast.length)
      lines += `<path d="${path(s.forecast)}" fill="none" stroke="${s.color}" stroke-width="${w}" stroke-dasharray="5 4"/>`;
    for (const m of s.marks) {
      const c = m.kind === "full" ? "#f43f5e" : "#facc15";
      marks += `<line x1="${X(m.day)}" x2="${X(m.day)}" y1="${Y(0)}" y2="${Y(m.level)}" stroke="${c}" stroke-width="2"/>` +
               `<circle cx="${X(m.day)}" cy="${Y(m.level)}" r="4" fill="${c}" stroke="#050505" stroke-width="2"/>` +
               `<text class="lbl" x="${X(m.day) - 6}" y="${Y(m.level) - 8}" fill="${c}" text-anchor="end">${esc(m.text)}</text>`;
    }
  }
  return `<defs><clipPath id="rv${uid}"><rect class="rv" x="0" y="0" width="0" height="${H}"/></clipPath></defs>` +
         `<g class="axis">${axis}</g><g clip-path="url(#rv${uid})">${lines}${marks}</g>`;
}

function showRack(st, i) {
  if (st.shown === i) return;
  st.shown = i;
  const rk = st.data.racks[i], root = st.root, uid = ++st.uid;
  const col = SEV[rk.status] || "#f2f2f2";
  root.querySelector(".rk").textContent = rk.name;
  const chip = root.querySelector(".chip");
  chip.textContent = rk.status; chip.style.color = col;
  root.querySelector(".eta").textContent = rk.headline;
  const svg = root.querySelector(".chart");
  svg.setAttribute("aria-label", `${rk.name} resources as % of capacity, actual and forecast`);
  svg.innerHTML = chartSVG(rk, st.data.ticks, uid);
  root.querySelector(".legend").innerHTML = rk.series.map((s) =>
    `<span><i style="background:${s.color}"></i>${esc(s.name)}${s.alert ? " ▲" : ""}</span>`).join("");
  root.querySelectorAll(".steps li").forEach((li) => li.classList.toggle("on", +li.dataset.i === i));
  const rect = svg.querySelector(".rv"), axis = svg.querySelector(".axis");
  const first = !st.drawn.has(rk.name);
  st.drawn.add(rk.name);
  if (!first || REDUCE) { rect.setAttribute("width", 560); axis.classList.add("in"); return; }
  requestAnimationFrame(() => axis.classList.add("in"));
  const t0 = performance.now(), dur = 1500;
  const step = (t) => {
    if (st.shown !== i) return;
    const k = clamp((t - t0 - 250) / dur, 0, 1), e = 1 - Math.pow(1 - k, 3);
    rect.setAttribute("width", (560 * e).toFixed(1));
    if (k < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

// ------------------------------------------------------------ treemaps
function squarify(items, x, y, w, h) {
  const out = [], total = items.reduce((a, b) => a + b.v, 0);
  if (!total) return out;
  let rest = items.map((it) => ({ ...it, a: (it.v * w * h) / total }));
  const worst = (row, side) => {
    const s = row.reduce((a, b) => a + b.a, 0);
    const mx = Math.max(...row.map((r) => r.a)), mn = Math.min(...row.map((r) => r.a));
    return Math.max((side * side * mx) / (s * s), (s * s) / (side * side * mn));
  };
  while (rest.length) {
    const side = Math.min(w, h);
    let row = [rest[0]], i = 1;
    while (i < rest.length && worst([...row, rest[i]], side) <= worst(row, side)) row.push(rest[i++]);
    const s = row.reduce((a, b) => a + b.a, 0);
    if (w >= h) {
      const cw = s / h; let cy = y;
      for (const r of row) { const ch = r.a / cw; out.push({ ...r, x, y: cy, w: cw, h: ch }); cy += ch; }
      x += cw; w -= cw;
    } else {
      const rh = s / w; let cx = x;
      for (const r of row) { const rw = r.a / rh; out.push({ ...r, x: cx, y, w: rw, h: rh }); cx += rw; }
      y += rh; h -= rh;
    }
    rest = rest.slice(i);
  }
  return out;
}

function drawMaps(st, animate) {
  const d = st.data, wl = st.workload, maps = st.root.querySelector(".mi");
  maps.innerHTML = d.racks.map((rk, i) => {
    const t = (d.tenants[rk.name] || {})[wl];
    if (!t) return `<div class="map" data-i="${i}"><h4>${esc(rk.name)}<em>n/a</em></h4></div>`;
    const items = t.shares.filter((s) => s.pct > 0.05).map((s) => ({ v: s.pct, name: s.tenant, pct: s.pct }));
    const free = Math.max(0, 100 - t.used);
    if (free > 0.05) items.push({ v: free, name: "free", pct: free, free: true });
    items.sort((a, b) => b.v - a.v);
    const tiles = squarify(items, 0, 0, 100, 100).map((tl, j) => {
      const big = tl.w > 22 && tl.h > 16;
      const bg = tl.free ? "" : `background:${d.tenant_colors[tl.name] || "#d4d4d4"};`;
      return `<div class="tile${tl.free ? " free" : ""}" style="left:${tl.x}%;top:${tl.y}%;width:${tl.w}%;` +
             `height:${tl.h}%;${bg}transition-delay:${(j * 0.07).toFixed(2)}s" title="${esc(tl.name)}: ${tl.pct.toFixed(1)}%">` +
             (big ? `<span>${esc(tl.name)}<b>${tl.pct.toFixed(0)}%</b></span>` : "") + `</div>`;
    }).join("");
    const cls = t.used >= t.safe ? "crit" : t.used >= t.safe - 10 ? "hot" : "";
    return `<div class="map" data-i="${i}"><h4>${esc(rk.name)}<em>${t.used.toFixed(0)}% used</em></h4>` +
           `<div class="tm">${tiles}</div><div class="bar"><i class="${cls}" data-w="${Math.min(t.used, 100)}"></i>` +
           `<u style="left:${t.safe}%"></u></div></div>`;
  }).join("");
  const reveal = () => maps.querySelectorAll(".map").forEach((m) => {
    m.classList.add("in");
    const bar = m.querySelector(".bar i"); if (bar) bar.style.width = bar.dataset.w + "%";
  });
  if (animate && !REDUCE) requestAnimationFrame(() => requestAnimationFrame(reveal)); else reveal();
  st.root.querySelectorAll(".seg button").forEach((b) => b.classList.toggle("on", b.dataset.wl === wl));
  layoutMaps(st);
}

// Few racks in one row: hang each card over its rack in the top view. Otherwise (or without
// WebGL): a grid of readable cards; rows that do not fit scroll with the page at the end.
function layoutMaps(st) {
  const maps = st.root.querySelector(".maps"), cards = maps.querySelectorAll(".map");
  const W = st.stage.clientWidth, H = st.stage.clientHeight, n = st.data.racks.length;
  if (!W || !H) return;
  const pts = st.project ? st.project() : null;
  const spacing = pts && pts.length > 1 ? Math.abs(pts[1][0] - pts[0][0]) : W * 0.3;
  if (pts && spacing * 0.9 >= 150) {
    const size = clamp(spacing * 0.9, 150, 260);
    maps.classList.remove("grid");
    cards.forEach((m) => {
      const pt = pts[+m.dataset.i]; if (!pt) return;
      m.style.left = pt[0] + "px"; m.style.top = pt[1] + "px"; m.style.width = size + "px";
    });
    st.mapsScroll = 0;
  } else {
    maps.classList.add("grid");
    const pad = 32, gap = 18, w = W - pad * 2, view = H - 104 - 64;
    const cols = Math.max(1, Math.min(n, Math.floor((w + gap) / (190 + gap))));
    const size = Math.min(240, (w - (cols - 1) * gap) / cols);
    const rowH = size * 1.15 + 44 + gap, rows = Math.ceil(n / cols);
    const x0 = pad + (w - (cols * size + (cols - 1) * gap)) / 2;
    const y0 = Math.max(0, (view - rows * rowH + gap) / 2);
    cards.forEach((m, i) => {
      m.style.width = size + "px";
      m.style.left = x0 + (i % cols) * (size + gap) + size / 2 + "px";
      m.style.top = y0 + Math.floor(i / cols) * rowH + (rowH - gap) / 2 + "px";
    });
    st.mapsScroll = Math.max(0, Math.ceil(rows * rowH - gap - view + 12));
  }
  const h = `calc(${STORY_VH}vh + ${st.mapsScroll}px)`;
  if (st.root.style.height !== h) st.root.style.height = h;
}

// ------------------------------------------------------------ overlays (no WebGL needed)
function initOverlay(st) {
  const r = st.root, d = st.data;
  r.querySelector(".lede").textContent = d.lede;
  r.querySelector(".smooth-win").textContent = d.smooth_win || "7-day";
  r.querySelector(".hud").innerHTML = d.hud.map(([k, v, c]) =>
    `<div><dt>${esc(k)}</dt><dd class="${c}"${k === "IST clock" ? ' data-clock="1"' : ""}>${esc(v)}</dd></div>`).join("");
  r.querySelector(".sys .led").classList.toggle("ok", !d.critical);
  st.tour = tourOf(d.racks);
  const more = d.racks.length - st.tour.length;
  r.querySelector(".steps").innerHTML = st.tour.map((i) =>
    `<li data-i="${i}"><span class="d" style="background:${SEV[d.racks[i].status] || "#f2f2f2"}"></span>${esc(d.racks[i].name)}</li>`).join("") +
    (more > 0 ? `<li class="more">+${more} more · hover any rack</li>` : "");
  r.querySelector(".seg").innerHTML = d.workloads.map(([lab, key]) =>
    `<button type="button" data-wl="${key}">${esc(lab)}</button>`).join("");
  r.querySelectorAll(".seg button").forEach((b) => { b.onclick = () => { st.workload = b.dataset.wl; drawMaps(st, true); }; });
  r.querySelector(".tlegend").innerHTML = Object.entries(d.tenant_colors).map(([t, c]) =>
    `<span><i style="background:${c}"></i>${esc(t)}</span>`).join("") +
    `<span><i style="background:repeating-linear-gradient(135deg,#555 0 2px,transparent 2px 4px)"></i>free</span>` +
    `<span class="note">area = % of rack capacity · bar = used vs safe limit</span>`;
  st.workload = st.workload || d.workloads[0][1];
  st.shown = -1;
}

function frame(st, t) {
  const r = st.root, story = r, stage = st.stage;
  const vh = window.innerHeight, rect = story.getBoundingClientRect();
  if (stage.style.height !== vh + "px") stage.style.height = vh + "px";
  const extra = st.mapsScroll || 0;                              // treemap rows below the fold
  const travel = Math.max(rect.height - vh - extra, 1);
  const off = clamp(-rect.top, 0, travel + extra);
  stage.style.transform = `translateY(${off}px)`;
  if (rect.bottom < 0 || rect.top > vh) return false;           // off screen: skip the work
  st.mi.style.transform = `translateY(${-Math.max(0, off - travel).toFixed(1)}px)`;
  const target = Math.min(off, travel) / travel;
  st.p += (target - st.p) * (REDUCE ? 1 : 0.14);
  const p = st.p, focus = st.hover ?? st.tour[rackAt(p, st.tour.length)];
  st.focus = focus;

  const a1 = 1 - smooth(0.05, 0.12, p);
  const a2 = smooth(ACT.hallStart - 0.01, ACT.hallStart + 0.03, p) * (1 - smooth(ACT.hallEnd, ACT.hallEnd + 0.03, p));
  const a3 = smooth(ACT.tenStart - 0.02, ACT.tenStart + 0.02, p);
  st.ov[0].style.opacity = a1; st.ov[1].style.opacity = a2; st.ov[2].style.opacity = a3;
  st.ov[1].style.pointerEvents = a2 > 0.5 ? "" : "none";
  st.ov[2].querySelector(".seg").style.pointerEvents = a3 > 0.5 ? "auto" : "none";
  r.querySelector(".card").style.pointerEvents = a2 > 0.5 ? "auto" : "none";
  r.querySelector(".rail i").style.height = (p * 100).toFixed(1) + "%";

  if (a2 > 0.01) showRack(st, focus);
  if (a3 > 0.3 && !st.mapsDrawn) { st.mapsDrawn = true; drawMaps(st, true); }
  st.hallF = a2; st.tenF = a3;
  if (st.render3D) st.render3D(p, t);
  const ww = stage.clientWidth, wh = stage.clientHeight;
  if (ww !== st.wiresW || wh !== st.wiresH) {
    st.wiresW = ww; st.wiresH = wh; st.wires.resize(ww, wh, Math.min(window.devicePixelRatio || 1, 1.5));
    layoutMaps(st);
  }
  st.wires.draw(t);
  return true;
}

// ------------------------------------------------------------ 3D scene
function init3D(st, THREE) {
  const gl = st.root.querySelector(".gl");
  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
  renderer.setClearColor(0x050505, 1);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  gl.appendChild(renderer.domElement);
  const scene = new THREE.Scene();
  scene.fog = new THREE.FogExp2(0x050505, 0.05);
  const camera = new THREE.PerspectiveCamera(40, 1, 0.1, 260);
  scene.add(new THREE.HemisphereLight(0xffffff, 0x0a0a0a, 0.75));
  const sun = new THREE.DirectionalLight(0xffffff, 1.3); sun.position.set(6, 16, 9); scene.add(sun);
  const gloss = new THREE.PointLight(0xffffff, 10, 9); gloss.position.set(0, 2.8, 2.4); scene.add(gloss);

  const disposables = [];
  const keep = (o) => { disposables.push(o); return o; };

  // floor
  const floor = new THREE.Mesh(keep(new THREE.PlaneGeometry(60, 60)),
    keep(new THREE.MeshStandardMaterial({ color: 0x0b0b0b, roughness: 0.55, metalness: 0.5 })));
  floor.rotation.x = -Math.PI / 2; scene.add(floor);
  const grid = new THREE.GridHelper(60, 100, 0x3a3a3a, 0x181818);
  grid.position.y = 0.002; scene.add(grid); keep(grid.geometry); keep(grid.material);

  // monitored racks: rows of up to COLS_MAX down the middle
  const racks = st.data.racks, n = racks.length, L = rackLayout(n), xs = L.xs, zs = L.zs;
  const cab = keep(new THREE.BoxGeometry(0.9, 2.2, 1.0));
  const cabEdges = keep(new THREE.EdgesGeometry(cab));
  const ledGeo = keep(new THREE.PlaneGeometry(0.035, 0.035));
  const mon = racks.map((rk, i) => {
    const g = new THREE.Group(); g.position.set(xs[i], 0, zs[i]);
    const body = new THREE.Mesh(cab, keep(new THREE.MeshStandardMaterial({ color: 0x141414, metalness: 0.7, roughness: 0.35 })));
    body.position.y = 1.1; body.userData.i = i; g.add(body);
    const edgeMat = keep(new THREE.LineBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.5 }));
    const edges = new THREE.LineSegments(cabEdges, edgeMat); edges.position.y = 1.1; g.add(edges);
    const used = clamp(rk.used, 0.03, 1.15);
    const fillMat = keep(new THREE.MeshBasicMaterial({ color: SEV[rk.status] || 0xffffff, transparent: true, opacity: 0 }));
    const fill = new THREE.Mesh(keep(new THREE.BoxGeometry(0.12, 2.0 * used, 0.02)), fillMat);
    fill.position.set(0.33, 0.1 + used, 0.512); g.add(fill);
    const leds = new THREE.InstancedMesh(ledGeo, keep(new THREE.MeshBasicMaterial({ color: 0xffffff })), 64);
    const m = new THREE.Matrix4(), rate = [], phase = [];
    for (let k = 0; k < 64; k++) {
      m.setPosition(-0.32 + (k % 4) * 0.07, 0.22 + Math.floor(k / 4) * 0.12, 0.512);
      leds.setMatrixAt(k, m); leds.setColorAt(k, new THREE.Color(0xffffff));
      rate.push(1 + Math.random() * 7); phase.push(Math.random() * 6.3);
    }
    g.add(leds);
    const haloMat = keep(new THREE.MeshBasicMaterial({ color: SEV[rk.status] || 0xffffff, transparent: true,
      opacity: 0, blending: THREE.AdditiveBlending, depthWrite: false }));
    const halo = new THREE.Mesh(keep(new THREE.CircleGeometry(0.95, 48)), haloMat);
    halo.rotation.x = -Math.PI / 2; halo.position.y = 0.01; g.add(halo);
    scene.add(g);
    return { g, body, edgeMat, fillMat, haloMat, leds, rate, phase, col: new THREE.Color(SEV[rk.status] || 0xffffff) };
  });

  // decorative rows either side (black and white, no data)
  const decoCols = Math.max(12, Math.ceil(L.spanX / 1.05) + 2), decoCount = 4 * decoCols;
  const zFront = zs[0] + ROW_GAP, zBack = zs[n - 1] - ROW_GAP;
  const deco = new THREE.InstancedMesh(cab, keep(new THREE.MeshStandardMaterial({ color: 0x111111, metalness: 0.6, roughness: 0.5 })), decoCount);
  const decoLed = new THREE.InstancedMesh(keep(new THREE.PlaneGeometry(0.62, 1.7)),
    keep(new THREE.MeshBasicMaterial({ color: 0x2a2a2a })), decoCount);
  {
    const m = new THREE.Matrix4(); let k = 0;
    for (const z of [zBack, zBack - 4.5, zFront, zFront + 4.5]) for (let c = 0; c < decoCols; c++) {
      const x = (c - (decoCols - 1) / 2) * 1.05;
      m.makeRotationY(z > 0 ? Math.PI : 0); m.setPosition(x, 1.1, z); deco.setMatrixAt(k, m);
      const f = new THREE.Matrix4().makeRotationY(z > 0 ? Math.PI : 0);
      f.setPosition(x, 1.1, z + (z > 0 ? -0.505 : 0.505)); decoLed.setMatrixAt(k, f); k++;
    }
  }
  scene.add(deco, decoLed);

  // the campus seen from the air: the monitored hall in the middle, sister halls, generator
  // yards, a substation, roads and parking; it all fades away as the camera drops in
  const roof = new THREE.Group(); scene.add(roof);
  const roofMats = [];                                   // [material, full opacity]
  const rmat = (c, o = {}) => {
    const m = keep(new THREE.MeshStandardMaterial({ color: c, roughness: 0.85, metalness: 0.15, transparent: true, ...o }));
    roofMats.push([m, 1]); return m;
  };
  const lmat = (a) => { const m = keep(new THREE.LineBasicMaterial({ color: 0xffffff, transparent: true, opacity: a })); roofMats.push([m, a]); return m; };
  const tex = (draw, rx, ry) => {
    const cv2 = document.createElement("canvas"); cv2.width = cv2.height = 256; draw(cv2.getContext("2d"));
    const t = keep(new THREE.CanvasTexture(cv2)); t.wrapS = t.wrapT = THREE.RepeatWrapping;
    t.repeat.set(rx, ry); t.colorSpace = THREE.SRGBColorSpace; t.anisotropy = 4; return t;
  };
  const speckle = (g, base, lo, hi, n) => {
    g.fillStyle = base; g.fillRect(0, 0, 256, 256);
    for (let i = 0; i < n; i++) { const v = lo + Math.random() * (hi - lo) | 0;
      g.fillStyle = `rgb(${v},${v},${v})`; g.fillRect(Math.random() * 256, Math.random() * 256, 1.5, 1.5); }
  };
  const M = (x, y, z, ry = 0) => new THREE.Matrix4().makeRotationY(ry).setPosition(x, y, z);
  const inst = (geo, mat, mats, shadow = true) => {
    const im = keep(new THREE.InstancedMesh(geo, mat, Math.max(mats.length, 1)));
    mats.forEach((m, i) => im.setMatrixAt(i, m)); im.count = mats.length;
    im.castShadow = shadow; im.receiveShadow = true; roof.add(im); return im;
  };
  const flat = (w, d, x, z, y, mat) => {
    const p = new THREE.Mesh(keep(new THREE.PlaneGeometry(w, d)), mat);
    p.rotation.x = -Math.PI / 2; p.position.set(x, y, z); p.receiveShadow = true; roof.add(p); return p;
  };

  const W = Math.max(26, decoCols * 1.05 + 4), D = Math.max(22, zFront - zBack + 12), HT = 3.3;
  const DX = W + 10, DZ = D + 14;
  const sites = [[0, 0], [-DX, 0], [DX, 0], [-DX, -DZ], [0, -DZ], [DX, -DZ], [-2 * DX, -DZ], [2 * DX, 0]];

  // ground: gravel, concrete aprons, asphalt
  flat(600, 600, 0, 0, 0.004, rmat(0xffffff, { map: tex((g) => speckle(g, "#101010", 8, 38, 9000), 120, 120) }));
  const apron = rmat(0xffffff, { map: tex((g) => { speckle(g, "#1c1c1c", 22, 40, 3000);
    g.strokeStyle = "#151515"; g.lineWidth = 2; for (let i = 0; i <= 256; i += 64) { g.strokeRect(i, -2, 0, 260); g.strokeRect(-2, i, 260, 0); } }, 10, 8) });
  sites.forEach(([x, z]) => flat(W + 6, D + 7, x, z, 0.006, apron));
  const asphalt = rmat(0x0a0a0a, { roughness: 0.95 });
  const zAve = D / 2 + 7, zBackRd = -DZ - D / 2 - 6;
  flat(6 * DX, 2, 0, zAve, 0.008, asphalt);
  flat(6 * DX, 2, 0, zBackRd, 0.008, asphalt);
  for (const x of [-2.5 * DX, -1.5 * DX, -0.5 * DX, 0.5 * DX, 1.5 * DX, 2.5 * DX]) flat(2, zAve - zBackRd, x, (zAve + zBackRd) / 2, 0.009, asphalt);
  flat(2, 60, -0.5 * DX, zAve + 30, 0.009, asphalt);                       // site entrance
  const dash = keep(new THREE.PlaneGeometry(0.7, 0.07)).rotateX(-Math.PI / 2), paint = rmat(0x8c8c8c, { roughness: 1 });
  {
    const ms = [];
    for (let x = -3 * DX; x < 3 * DX; x += 1.6) ms.push(M(x, 0.012, zAve), M(x, 0.012, zBackRd));
    inst(dash, paint, ms, false);
  }

  // halls: panelled walls, membrane roof and a white outline
  const wallG = keep(new THREE.BoxGeometry(W, HT, D)), roofG = keep(new THREE.BoxGeometry(W, 0.2, D));
  const wall = rmat(0x161616, { map: tex((g) => { g.fillStyle = "#3a3a3a"; g.fillRect(0, 0, 256, 256);
    g.fillStyle = "#2e2e2e"; for (let i = 0; i < 256; i += 16) g.fillRect(i, 0, 2, 256); }, 8, 1) });
  const membrane = rmat(0xffffff, { map: tex((g) => { speckle(g, "#4a4a4a", 62, 84, 2500);
    g.fillStyle = "#3e3e3e"; for (let i = 0; i < 256; i += 32) g.fillRect(0, i, 256, 1.5); }, W / 6, D / 6) });
  const outline = lmat(0.4), wallEdges = keep(new THREE.EdgesGeometry(wallG));
  sites.forEach(([x, z]) => {
    const w = new THREE.Mesh(wallG, wall); w.position.set(x, HT / 2, z); w.castShadow = w.receiveShadow = true; roof.add(w);
    const r = new THREE.Mesh(roofG, membrane); r.position.set(x, HT, z); r.castShadow = r.receiveShadow = true; roof.add(r);
    const e = new THREE.LineSegments(wallEdges, outline); e.position.set(x, HT / 2 + 0.05, z); roof.add(e);
  });

  // rooftop: rows of air-cooled condensers along both long edges, a central mechanical spine, vents
  const unitG = keep(new THREE.BoxGeometry(0.86, 0.32, 0.56)), spineG = keep(new THREE.BoxGeometry(W * 0.72, 0.55, 1.4));
  const ventG = keep(new THREE.CylinderGeometry(0.16, 0.16, 0.35, 12));
  const wellG = keep(new THREE.CircleGeometry(0.19, 20)).rotateX(-Math.PI / 2);
  const ringG = keep(new THREE.RingGeometry(0.18, 0.215, 24)).rotateX(-Math.PI / 2);
  const bladeG = keep(new THREE.BoxGeometry(0.34, 0.01, 0.055));
  const units = [], spines = [], vents = [], wells = [], fanAt = [], emitters = [];
  const top = HT + 0.1;
  sites.forEach(([sx, sz], k) => {
    let u = 0;
    for (const s of [-1, 1]) for (const o of [1.1, 1.8]) for (let x = -W / 2 + 1; x <= W / 2 - 1; x += 0.95, u++) {
      const z = sz + s * (D / 2 - o);
      units.push(M(sx + x, top + 0.16, z));
      for (const f of [-0.21, 0.21]) { wells.push(M(sx + x + f, top + 0.325, z)); fanAt.push([sx + x + f, top + 0.33, z, Math.random() * 6.3]); }
      if (!k && o === 1.1 && u % 5 === 0) emitters.push([x, z]);
    }
    spines.push(M(sx, top + 0.27, sz));
    for (let i = 0; i < 10; i++) vents.push(M(sx + (Math.random() - 0.5) * W * 0.8, top + 0.17, sz + (Math.random() < 0.5 ? -1 : 1) * (1.6 + Math.random() * (D / 2 - 4.5))));
  });
  const metal = rmat(0x3c3c3c, { metalness: 0.5, roughness: 0.5 });
  inst(unitG, metal, units); inst(spineG, rmat(0x262626), spines); inst(ventG, metal, vents);
  inst(wellG, rmat(0x050505, { roughness: 1 }), wells, false);
  inst(ringG, rmat(0x9a9a9a), wells, false);
  const bladeMat = rmat(0xc8c8c8);
  const bladesA = inst(bladeG, bladeMat, fanAt.map(() => new THREE.Matrix4()), false);
  const bladesB = inst(bladeG, bladeMat, fanAt.map(() => new THREE.Matrix4()), false);
  const spinFans = (a) => {
    const m = new THREE.Matrix4();
    fanAt.forEach(([x, y, z, ph], i) => {
      bladesA.setMatrixAt(i, m.makeRotationY(a + ph).setPosition(x, y, z));
      bladesB.setMatrixAt(i, m.makeRotationY(a + ph + Math.PI / 2).setPosition(x, y, z));
    });
    bladesA.instanceMatrix.needsUpdate = bladesB.instanceMatrix.needsUpdate = true;
  };
  spinFans(0);

  // backup generators lined up along each hall, with exhaust stacks
  const genG = keep(new THREE.BoxGeometry(1.25, 0.75, 0.62)), stackG = keep(new THREE.CylinderGeometry(0.06, 0.06, 0.5, 8));
  const gens = [], stacks = [];
  sites.forEach(([sx, sz]) => {
    for (const s of [-1, 1]) for (let x = -W / 2 + 1; x <= W / 2 - 1; x += 1.75) {
      const z = sz + s * (D / 2 + 1.7);
      gens.push(M(sx + x, 0.38, z)); stacks.push(M(sx + x + 0.4, 1.0, z));
    }
  });
  inst(genG, rmat(0x2a2a2a, { metalness: 0.4 }), gens); inst(stackG, metal, stacks);

  // substation with transformers, gantries and a transmission line heading off site
  const subX = 1.5 * DX + 1, subZ = zAve + 12;
  flat(22, 14, subX, subZ, 0.01, apron);
  const tfG = keep(new THREE.BoxGeometry(1.4, 1.2, 1.0)), tfs = [];
  for (let i = 0; i < 5; i++) for (let j = 0; j < 2; j++) tfs.push(M(subX - 8 + i * 4, 0.6, subZ - 2.5 + j * 5));
  inst(tfG, rmat(0x333333, { metalness: 0.4 }), tfs);
  {
    const v = [], L2 = (a, b) => v.push(...a, ...b);
    for (let i = 0; i < 6; i++) { const x = subX - 10 + i * 4;
      for (const z of [subZ - 5.5, subZ + 5.5]) L2([x, 0, z], [x, 3.2, z]);
      L2([x, 3.2, subZ - 5.5], [x, 3.2, subZ + 5.5]); }
    for (const z of [subZ - 5.5, subZ, subZ + 5.5]) L2([subX - 10, 3.0, z], [subX + 10, 3.0, z]);
    for (let k = 0; k < 7; k++) {                                      // pylons out to the east
      const x = subX + 16 + k * 14, z = subZ + k * 1.5;
      L2([x - 0.8, 0, z], [x, 5.5, z]); L2([x + 0.8, 0, z], [x, 5.5, z]); L2([x - 1.6, 4.6, z], [x + 1.6, 4.6, z]);
      if (k) for (const d of [-1.5, 0, 1.5]) L2([x - 14 + d, 4.6, z - 1.5], [x + d, 4.6, z]);
      else for (const d of [-1.5, 0, 1.5]) L2([subX + 10, 3.0, subZ + d], [x + d, 4.6, z]);
    }
    const g = keep(new THREE.BufferGeometry()); g.setAttribute("position", new THREE.Float32BufferAttribute(v, 3));
    roof.add(new THREE.LineSegments(g, lmat(0.35)));
  }

  // staff parking by the entrance
  const lotX = -1.5 * DX + 2, lotZ = zAve + 9;
  flat(26, 10, lotX, lotZ, 0.01, asphalt);
  const stripeG = keep(new THREE.PlaneGeometry(0.05, 2.0)).rotateX(-Math.PI / 2), stripes = [], cars = [];
  for (const r of [-2.2, 2.2]) for (let i = 0; i < 22; i++) {
    const x = lotX - 12 + i * 1.15; stripes.push(M(x, 0.013, lotZ + r));
    if (Math.random() < 0.6) cars.push(M(x + 0.57, 0.22, lotZ + r));
  }
  inst(stripeG, paint, stripes, false);
  const carG = keep(new THREE.BoxGeometry(0.75, 0.42, 1.65));
  const carMesh = inst(carG, rmat(0xffffff, { metalness: 0.6, roughness: 0.35 }), cars);
  cars.forEach((_, i) => carMesh.setColorAt(i, new THREE.Color().setScalar(0.08 + Math.random() * 0.6)));

  // sun shadows for the campus only
  renderer.shadowMap.enabled = true; renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  sun.castShadow = true; sun.position.set(-30, 56, 34); sun.target.position.set(0, 0, -8); scene.add(sun.target);
  Object.assign(sun.shadow.camera, { left: -80, right: 80, top: 60, bottom: -60, near: 1, far: 160 });
  sun.shadow.mapSize.set(2048, 2048); sun.shadow.bias = -0.0006; sun.shadow.camera.updateProjectionMatrix();
  // steam: soft additive sprites whose brightness fades with age
  const sprite = (() => {
    const c = document.createElement("canvas"); c.width = c.height = 64;
    const g = c.getContext("2d"), rg = g.createRadialGradient(32, 32, 0, 32, 32, 32);
    rg.addColorStop(0, "rgba(255,255,255,1)"); rg.addColorStop(1, "rgba(255,255,255,0)");
    g.fillStyle = rg; g.fillRect(0, 0, 64, 64); return keep(new THREE.CanvasTexture(c));
  })();
  const P = 420, pos = new Float32Array(P * 3), colr = new Float32Array(P * 3), life = new Float32Array(P), src = new Uint16Array(P);
  const respawn = (i, age) => {
    src[i] = Math.floor(Math.random() * emitters.length);
    const [ex, ez] = emitters[src[i]];
    pos[i * 3] = ex + (Math.random() - 0.5) * 1.0; pos[i * 3 + 1] = 3.9; pos[i * 3 + 2] = ez + (Math.random() - 0.5) * 1.0;
    life[i] = age;
  };
  for (let i = 0; i < P; i++) { respawn(i, Math.random()); pos[i * 3 + 1] = 3.9 + life[i] * 4; }
  const sg = keep(new THREE.BufferGeometry());
  sg.setAttribute("position", new THREE.BufferAttribute(pos, 3));
  sg.setAttribute("color", new THREE.BufferAttribute(colr, 3));
  const smat = keep(new THREE.PointsMaterial({ size: 2.8, map: sprite, vertexColors: true, transparent: true,
    blending: THREE.AdditiveBlending, depthWrite: false }));
  const steam = new THREE.Points(sg, smat); scene.add(steam);

  // interaction
  const ray = new THREE.Raycaster(), ptr = new THREE.Vector2(), mouse = { x: 0, y: 0 };
  const cv = renderer.domElement;
  st.root.querySelector(".stage").onpointermove = (ev) => {
    const b = cv.getBoundingClientRect();
    mouse.x = ((ev.clientX - b.left) / b.width) * 2 - 1; mouse.y = -((ev.clientY - b.top) / b.height) * 2 + 1;
    if (st.hallF < 0.5) { st.hover = null; return; }
    ptr.set(mouse.x, mouse.y); ray.setFromCamera(ptr, camera);
    const hit = ray.intersectObjects(mon.map((m) => m.body))[0];
    st.hover = hit ? hit.object.userData.i : null;
    cv.style.cursor = hit ? "crosshair" : "default";
  };
  st.root.querySelector(".stage").onpointerleave = () => { st.hover = null; };

  const resize = () => {
    const b = gl.getBoundingClientRect(); if (!b.width || !b.height) return;
    renderer.setSize(b.width, b.height, false); camera.aspect = b.width / b.height; camera.updateProjectionMatrix();
  };
  st.ro = new ResizeObserver(resize); st.ro.observe(gl); resize();

  // top view height that fits the whole row across the stage width
  // (measured at rack-top height, where the treemap cards are anchored)
  const fitY = () => {
    const tan = Math.tan((camera.fov * Math.PI) / 360);
    return 2.2 + Math.max((L.spanX * 1.2) / 2 / (tan * camera.aspect), ((L.spanZ + 2) * 1.2) / 2 / tan);
  };
  let K = keyframes(L, st.tour, fitY()), kAspect = camera.aspect;
  const look = new THREE.Vector3(), tmp = new THREE.Vector3(), c = new THREE.Color();
  let last = 0, lastFrame = 0;
  // rack tops on screen, for hanging treemap cards over a single row (null: use the grid)
  st.project = () => {
    const b = cv.getBoundingClientRect(); if (!b.width || L.rows > 1) return null;
    return xs.map((x) => { tmp.set(x, 2.2, 0).project(camera);
      return [(tmp.x * 0.5 + 0.5) * b.width, (-tmp.y * 0.5 + 0.5) * b.height]; });
  };
  st.render3D = (p, t) => {
    if (t - lastFrame < 22) return;                     // ~45 fps cap
    const dt = Math.min((t - (last || t)) / 1000, 0.05); last = t; lastFrame = t;
    if (camera.aspect !== kAspect) { K = keyframes(L, st.tour, fitY()); kAspect = camera.aspect; }
    const cam = sampleCam(K, p);
    const par = REDUCE ? 0 : 0.25 * (1 - st.tenF);
    camera.position.set(cam.pos[0] + mouse.x * par, cam.pos[1] + mouse.y * par * 0.6, cam.pos[2]);
    look.set(cam.look[0], cam.look[1], cam.look[2]); camera.lookAt(look);

    const roofA = 1 - smooth(0.07, ACT.roofEnd, p);
    roof.visible = roofA > 0.01; steam.visible = roofA > 0.01;
    renderer.shadowMap.autoUpdate = roof.visible;
    roofMats.forEach(([m, a]) => { m.opacity = roofA * a; });
    scene.fog.density = 0.05 - 0.038 * roofA;            // clear air over the campus, haze in the hall
    const s = t / 1000;
    if (!REDUCE && roof.visible) spinFans(s * 7);
    if (steam.visible) {
      for (let i = 0; i < P; i++) {
        if (!REDUCE) {
          life[i] += dt * 0.16;
          if (life[i] > 1) respawn(i, 0);
          pos[i * 3 + 1] += dt * 0.65; pos[i * 3] += dt * (0.25 + 0.2 * Math.sin(s * 0.6 + i)); pos[i * 3 + 2] -= dt * 0.12;
        }
        const k = Math.sin(Math.PI * life[i]) * 0.022 * roofA;
        colr[i * 3] = colr[i * 3 + 1] = colr[i * 3 + 2] = k;
      }
      sg.attributes.position.needsUpdate = true; sg.attributes.color.needsUpdate = true;
    }
    const focus = st.focus;
    mon.forEach((m, i) => {
      const on = st.hallF > 0.01 && i === focus;
      const tint = st.hallF * (on ? 1 : 0.25) + st.tenF * 0.15;
      m.edgeMat.color.copy(c.set(0xffffff).lerp(m.col, Math.min(tint * 1.6, 1)));
      m.edgeMat.opacity = 0.45 + 0.5 * (on ? 1 : 0);
      const pulse = REDUCE ? 1 : 0.7 + 0.3 * Math.sin(s * (m.col.r > 0.8 ? 5 : 2));
      m.fillMat.opacity = Math.max(st.hallF, 0.0) * (on ? 0.95 : 0.5) * pulse;
      m.haloMat.opacity = st.hallF * (on ? 0.45 : 0.12) * pulse;
      for (let k = 0; k < 64; k++) {
        const lit = REDUCE || Math.sin(s * m.rate[k] + m.phase[k]) > -0.3;
        m.leds.setColorAt(k, c.setScalar(lit ? 0.95 : 0.12));
      }
      m.leds.instanceColor.needsUpdate = true;
    });
    renderer.render(scene, camera);
    if (st.tenF > 0.01) layoutMaps(st);
  };
  st.dispose3D = () => {
    st.ro && st.ro.disconnect();
    disposables.forEach((d) => d.dispose && d.dispose());
    deco.dispose(); decoLed.dispose(); mon.forEach((m) => m.leds.dispose());
    renderer.dispose(); cv.remove();
  };
}

export default function (component) {
  const { data, parentElement } = component;
  const root = parentElement.querySelector(".story");
  if (!root || !data) return;
  let st = STATE.get(parentElement);
  const sig = JSON.stringify(data);
  if (!st) {
    st = { root, stage: root.querySelector(".stage"), ov: [...root.querySelectorAll(".ov")], p: 0, uid: 0,
           drawn: new Set(), data, sig, hover: null, hallF: 0, tenF: 0, mapsDrawn: false,
           mi: root.querySelector(".mi"), mapsScroll: 0 };
    STATE.set(parentElement, st);
    st.wires = makeWires(root.querySelector(".wires"), { density: 0.9 });
    initOverlay(st);
    const loop = (t) => { st.raf = requestAnimationFrame(loop); frame(st, t); };
    st.raf = requestAnimationFrame(loop);
    st.clock = setInterval(() => {
      const el = root.querySelector("[data-clock]");
      if (el) el.textContent = new Date().toLocaleTimeString("en-GB", { timeZone: "Asia/Kolkata", hour12: false }) + " IST";
    }, 1000);
    import(data.three_url).then((THREE) => init3D(st, THREE))
      .catch((err) => console.warn("AO-02 story: 3D scene disabled", err));
  } else if (st.sig !== sig) {             // data changed (rare): refresh overlays in place
    st.data = data; st.sig = sig; st.shown = -1; initOverlay(st);
    if (st.mapsDrawn) drawMaps(st, false);
  }
  return () => {
    cancelAnimationFrame(st.raf); clearInterval(st.clock);
    st.dispose3D && st.dispose3D();
    STATE.delete(parentElement);
  };
}
"""

_story = st.components.v2.component("ao02_story", html=_HTML, css=_CSS, js=_JS)


def story(payload):
    """Mount the scroll story. payload is built by app.story_payload (operator data only)."""
    # The 3D hall is built once per mount for a fixed rack list: a different rack set (e.g.
    # another data-centre record) needs a fresh mount, so it is part of the key.
    racks = hashlib.sha1("|".join(r["name"] for r in payload["racks"]).encode()).hexdigest()[:10]
    _story(key=f"ao02_story_{racks}", data={**payload, "three_url": THREE_URL})
