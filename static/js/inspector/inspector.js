// Frame-by-frame view (Sprite Inspector): opened from the Pipeline result card for one character.
// Play actions per direction, inspect timing / events / layers, download frames, strips and GIFs.

import { actionDuration, DIRECTIONS, drawFrame, eventFrame, frameIndexAt, loadAnimSet, setBounds } from "../anim/animset.js";
import { t } from "../i18n.js";
import { drawIsoGrid, drawPivotMarker } from "../iso.js";
import { activePage, showPage } from "../tabs.js";

const $ = (id) => document.getElementById(id);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const COMPASS = [["NW", "N", "NE"], ["W", null, "E"], ["SW", "S", "SE"]];
const DIR_INDEX = Object.fromEntries(DIRECTIONS.map((d, i) => [d, i]));

const state = {
  set: null,
  type: "idle",
  dir: "S",
  view: "single",
  playing: true,
  speed: 1,
  t: 0,
  zoom: 2,
  manualFrame: null, // set while scrubbing / stepping
  bounds: null,
  loadToken: 0,
};

const canvas = $("inspStage");
const ctx = canvas.getContext("2d");
let cssW = 0, cssH = 0, dpr = 1;

// ───────────────────────── file list ─────────────────────────

let searchTimer = 0;
let activeId = null;

async function refreshList() {
  const q = $("inspSearch").value;
  const only = state.project ? `&project=${encodeURIComponent(state.project)}` : "";
  const res = await fetch(`/api/sprites/files?q=${encodeURIComponent(q)}&limit=500${only}`).then((r) => r.json());
  if (!res.total && !q) {
    $("inspCount").textContent = t("No sprites yet");
    $("inspRootHint").textContent = t("Build a character in the Pipeline tab: its sprite shows up here.");
    return;
  }
  $("inspCount").textContent = `${res.total.toLocaleString()} sprites${res.total > res.items.length ? ` · showing ${res.items.length}` : ""}`;
  const list = $("inspList");
  list.replaceChildren(...res.items.map((it) => {
    const li = document.createElement("li");
    li.dataset.id = it.id;
    li.classList.toggle("active", it.id === activeId);
    li.innerHTML = `<span></span><small></small>`;
    li.firstChild.textContent = it.name;
    li.lastChild.textContent = it.folder.split("/").pop();
    li.title = `${it.folder}/${it.name}`;
    li.addEventListener("click", () => loadSprite(it.id));
    return li;
  }));
}

$("inspSearch").addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(refreshList, 150);
});

// ───────────────────────── loading ─────────────────────────

async function loadSprite(id) {
  const token = ++state.loadToken;
  activeId = id;
  document.querySelectorAll("#inspList li").forEach((li) => li.classList.toggle("active", li.dataset.id === id));
  try {
    const data = await fetch(`/api/sprites/animset?id=${id}`).then(async (r) => {
      if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
      return r.json();
    });
    const set = await loadAnimSet(data);
    if (token !== state.loadToken) return; // a newer click won
    state.set = set;
    state.bounds = setBounds(set);
    if (!set.hasType(state.type)) state.type = set.action_types[0];
    state.t = 0;
    state.manualFrame = null;
    fitZoom();
    $("inspEmpty").hidden = true;
    buildActionButtons();
    refreshPanels();
  } catch (err) {
    console.error(err);
    $("inspCount").textContent = `Error: ${err.message}`;
  }
}

function fitZoom() {
  const b = state.bounds;
  if (!b || !cssW) return;
  const z = Math.min((cssH * 0.7) / (b.y1 - b.y0), (cssW * 0.6) / (b.x1 - b.x0));
  // Whole / half steps keep pixel art crisp.
  state.zoom = Math.max(0.5, Math.min(8, Math.floor(z * 2) / 2));
  $("inspZoomLabel").textContent = `${Math.round(state.zoom * 100)}%`;
}

// ───────────────────────── panels ─────────────────────────

function currentAction(dir = state.dir) {
  return state.set?.getAction(state.type, dir) || null;
}

function buildActionButtons() {
  const seg = $("inspActionSeg");
  seg.replaceChildren(...state.set.action_types.map((type, i) => {
    const b = document.createElement("button");
    b.textContent = type;
    b.title = `${type} (${i + 1})`;
    b.classList.toggle("active", type === state.type);
    b.addEventListener("click", () => setType(type));
    return b;
  }));
}

function setType(type) {
  state.type = type;
  state.t = 0;
  state.manualFrame = null;
  document.querySelectorAll("#inspActionSeg button").forEach((b) => b.classList.toggle("active", b.textContent === type));
  refreshPanels();
}

function setDir(dir) {
  state.dir = dir;
  state.manualFrame = null;
  refreshPanels();
}

function refreshPanels() {
  buildCompass();
  buildTimeline();
  renderActionInfo();
  renderSpriteInfo();
  renderDownloads();
}

function buildCompass() {
  const el = $("inspCompass");
  el.replaceChildren(...COMPASS.flat().map((dir) => {
    const b = document.createElement("button");
    if (!dir) { b.className = "center"; return b; }
    const a = currentAction(dir);
    b.innerHTML = `<span>${dir}</span><small>${a ? `#${DIR_INDEX[dir]} · ${a.drawing}${a.mirrored ? " ↔" : ""}` : "–"}</small>`;
    b.classList.toggle("active", dir === state.dir);
    b.addEventListener("click", () => setDir(dir));
    return b;
  }));
}

let timelineCells = [];
function buildTimeline() {
  const a = currentAction();
  const el = $("inspTimeline");
  timelineCells = (a?.frames || []).map((f, i) => {
    const cell = document.createElement("div");
    cell.className = "tl-frame";
    cell.innerHTML = `<b>${i}</b><span>${Math.round(a.delay_ms)}ms</span>`;
    if (f.event) {
      const ev = document.createElement("span");
      const isAtk = f.event.toLowerCase() === "atk";
      ev.className = `tl-ev ${isAtk ? "atk" : "snd"}`;
      ev.textContent = isAtk ? "atk" : "♪";
      ev.title = f.event;
      cell.appendChild(ev);
    }
    cell.addEventListener("click", () => { state.playing = false; state.manualFrame = i; updatePlayButton(); renderDownloads(); });
    return cell;
  });
  el.replaceChildren(...timelineCells);
}

function info(el, rows) {
  el.replaceChildren(...rows.flatMap(([k, v]) => {
    const dt = document.createElement("dt");
    const dd = document.createElement("dd");
    dt.textContent = k;
    dd.textContent = v;
    return [dt, dd];
  }));
}

function renderActionInfo() {
  const a = currentAction();
  if (!a) return info($("inspActionInfo"), [["–", "no data"]]);
  const dur = actionDuration(a);
  const atk = eventFrame(a, "atk");
  const events = a.frames.map((f, i) => (f.event ? `f${i} ${f.event}` : null)).filter(Boolean);
  const rows = [
    ["Action #", `${a.index} = ${state.set.action_types.indexOf(a.type)}×8 + ${DIR_INDEX[a.dir]}`],
    ["Frames", a.frames.length],
    ["Delay", `${a.delay_ms} ms  (interval ${a.interval} × 25)`],
    ["Duration", `${Math.round(dur)} ms`],
    ["Drawing", `${a.drawing}${a.mirrored ? " (flipped)" : ""}`],
  ];
  if (atk >= 0) rows.push(["Hit (atk)", `frame ${atk} → ${Math.round(atk * a.delay_ms)} ms (${Math.round((atk / a.frames.length) * 100)}%)`]);
  rows.push(["Events", events.length ? events.join(", ") : "none"]);
  info($("inspActionInfo"), rows);
}

function renderDownloads() {
  const el = $("inspDownloads");
  const own = state.set?.id?.startsWith("own_");
  el.hidden = !own;
  if (!own) return;
  const a = currentAction();
  const frame = a ? (state.manualFrame ?? frameIndexAt(a, state.t, $("inspLoop").checked)) : 0;
  const base = `/api/sprites/download?id=${state.set.id}`;
  el.innerHTML = `
    <h2>${t("Download images")}</h2>
    <div class="dl-grid">
      <a class="btn small" href="${base}&kind=frame&action=${a?.index ?? 0}&frame=${frame}">${t("⬇ This frame (PNG)")}</a>
      <a class="btn small" href="${base}&kind=gif&action=${a?.index ?? 0}">${t("⬇ This action, this direction (GIF)")}</a>
      <a class="btn small" href="${base}&kind=strip&action=${a?.index ?? 0}">${t("⬇ This action, this direction (PNG strip)")}</a>
      <a class="btn small primary" href="${base}&kind=web">${t("⬇ For a web game (atlas + json)")}</a>
      <a class="btn small" href="${base}&kind=zip">${t("⬇ Everything (zip)")}</a>
    </div>
    <p class="hint">${t("{type} · {dir} · frame {frame} — every image is the same size, with the feet at the same point (see info.json in the zip)", { type: esc(state.type), dir: esc(state.dir), frame })}</p>`;
}

function renderSpriteInfo() {
  const s = state.set;
  const b = state.bounds;
  info($("inspSpriteInfo"), [
    ["Name", s.name],
    ["Folder", s.meta.folder],
    ["ACT version", s.meta.act_version],
    ["Actions", `${s.meta.action_count} = ${s.action_types.length} types × 8 dirs`],
    ["Images", `${s.images.length} in ${s.atlases.length} atlas page(s)`],
    ["Size vs origin", `${Math.round(b.x1 - b.x0)}w · ${Math.round(-b.y0)} up · ${Math.round(b.y1)} down`],
  ]);
}

// ───────────────────────── controls ─────────────────────────

function updatePlayButton() {
  $("inspPlay").textContent = state.playing ? "❚❚" : "▶";
}

$("inspPlay").addEventListener("click", () => {
  state.playing = !state.playing;
  if (state.playing) state.manualFrame = null;
  updatePlayButton();
});
$("inspSpeed").addEventListener("change", (e) => { state.speed = parseFloat(e.target.value); });
document.querySelectorAll("#inspViewSeg button").forEach((b) => b.addEventListener("click", () => {
  state.view = b.dataset.view;
  document.querySelectorAll("#inspViewSeg button").forEach((x) => x.classList.toggle("active", x === b));
}));
const setZoom = (z) => {
  state.zoom = Math.max(0.5, Math.min(12, z));
  $("inspZoomLabel").textContent = `${Math.round(state.zoom * 100)}%`;
};
$("inspZoomIn").addEventListener("click", () => setZoom(state.zoom + 0.5));
$("inspZoomOut").addEventListener("click", () => setZoom(state.zoom - 0.5));
canvas.addEventListener("wheel", (e) => {
  e.preventDefault();
  setZoom(state.zoom * Math.exp(-e.deltaY * 0.0015));
}, { passive: false });

function stepFrame(delta) {
  const a = currentAction();
  if (!a) return;
  const n = a.frames.length;
  const cur = state.manualFrame ?? frameIndexAt(a, state.t, $("inspLoop").checked);
  state.playing = false;
  state.manualFrame = (((cur + delta) % n) + n) % n;
  updatePlayButton();
  renderDownloads();
}

window.addEventListener("keydown", (e) => {
  if (activePage() !== "inspector" || /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName) || !state.set) return;
  const k = e.key;
  if (k === " ") { e.preventDefault(); $("inspPlay").click(); }
  else if (k === "ArrowRight" || k === ".") { e.preventDefault(); stepFrame(1); }
  else if (k === "ArrowLeft" || k === ",") { e.preventDefault(); stepFrame(-1); }
  else if (/^[1-9]$/.test(k) && state.set.action_types[+k - 1]) setType(state.set.action_types[+k - 1]);
  else if (k === "q" || k === "e") {
    const i = DIRECTIONS.indexOf(state.dir);
    setDir(DIRECTIONS[(i + (k === "e" ? 1 : 7)) % 8]);
  }
});

// ───────────────────────── render loop ─────────────────────────

function resize() {
  const r = canvas.parentElement.getBoundingClientRect();
  dpr = window.devicePixelRatio || 1;
  if (r.width === cssW && r.height === cssH) return;
  cssW = r.width;
  cssH = r.height;
  canvas.width = Math.round(cssW * dpr);
  canvas.height = Math.round(cssH * dpr);
}

function drawOne(dir, cx, cy, zoom, opts) {
  const a = currentAction(dir);
  if (!a) return null;
  const loop = $("inspLoop").checked;
  const idx = state.manualFrame != null ? Math.min(state.manualFrame, a.frames.length - 1) : frameIndexAt(a, state.t, loop);
  const b = state.bounds;
  // Put the origin so the sprite's overall bounds are centred on (cx, cy).
  const ox = Math.round(cx - ((b.x0 + b.x1) / 2) * zoom);
  const oy = Math.round(cy - ((b.y0 + b.y1) / 2) * zoom);

  if (opts.grid) {
    ctx.save();
    ctx.beginPath();
    ctx.ellipse(ox, oy, 64 * zoom, 32 * zoom, 0, 0, Math.PI * 2);
    ctx.clip();
    drawIsoGrid(ctx, { ox, oy, tileW: 32 * zoom, tileH: 16 * zoom, lineWidth: 1, bounds: { x0: ox - 70 * zoom, y0: oy - 40 * zoom, x1: ox + 70 * zoom, y1: oy + 40 * zoom }, color: "rgba(120,140,180,0.35)" });
    ctx.restore();
  }

  const frame = a.frames[idx];
  drawFrame(ctx, state.set, frame, ox, oy, { scale: zoom, layerBoxes: opts.layers, anchors: opts.anchors, smoothing: false });

  const isAtk = frame?.event && frame.event.toLowerCase() === "atk";
  if (isAtk) {
    ctx.save();
    ctx.strokeStyle = "#ff3b4f";
    ctx.lineWidth = 3;
    ctx.beginPath();
    ctx.ellipse(ox, oy, 26 * zoom, 13 * zoom, 0, 0, Math.PI * 2);
    ctx.stroke();
    ctx.fillStyle = "#ff3b4f";
    ctx.font = "bold 13px ui-monospace, monospace";
    ctx.fillText("ATK!", ox + 28 * zoom, oy - 4);
    ctx.restore();
  }
  if (opts.origin) drawPivotMarker(ctx, ox, oy, { size: 10 });
  return { a, idx };
}

let lastHighlighted = -1;
let last = performance.now();
function tick(now) {
  requestAnimationFrame(tick);
  const dt = Math.min(100, now - last);
  last = now;
  if (activePage() !== "inspector") return;
  if (!state.listLoaded) {
    state.listLoaded = true;
    refreshList();
  }
  resize();
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  if (!state.set) return;
  if (state.playing) state.t += dt * state.speed;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

  const opts = {
    origin: $("inspShowOrigin").checked,
    layers: $("inspShowLayers").checked,
    anchors: $("inspShowAnchors").checked,
    grid: $("inspShowGrid").checked,
  };

  if (state.view === "all") {
    const cw = cssW / 3, ch = cssH / 3;
    const b = state.bounds;
    const z = Math.max(0.5, Math.min(state.zoom, Math.floor(Math.min((ch * 0.75) / (b.y1 - b.y0), (cw * 0.8) / (b.x1 - b.x0)) * 2) / 2));
    ctx.font = "11px ui-monospace, monospace";
    COMPASS.forEach((row, r) => row.forEach((dir, c) => {
      if (!dir) return;
      const res = drawOne(dir, cw * (c + 0.5), ch * (r + 0.5), z, { ...opts, layers: false });
      const a = res?.a;
      ctx.fillStyle = dir === state.dir ? "#4f8cff" : "rgba(230,233,239,0.7)";
      ctx.fillText(`${dir} #${DIR_INDEX[dir]}  ${a ? a.drawing + (a.mirrored ? " ↔" : "") : ""}`, cw * c + 8, ch * r + 16);
    }));
  } else {
    const res = drawOne(state.dir, cssW / 2, cssH / 2 + 10, state.zoom, opts);
    if (res) {
      ctx.fillStyle = "rgba(230,233,239,0.8)";
      ctx.font = "12px ui-monospace, monospace";
      const tms = Math.round(res.idx * res.a.delay_ms);
      ctx.fillText(`${state.type} · ${state.dir}  frame ${res.idx}/${res.a.frames.length - 1}  t=${tms}ms`, 12, cssH - 14);
    }
  }

  // Timeline highlight (single direction's frame index).
  const a = currentAction();
  if (a) {
    const idx = state.manualFrame ?? frameIndexAt(a, state.t, $("inspLoop").checked);
    if (idx !== lastHighlighted || !timelineCells[idx]?.classList.contains("current")) {
      timelineCells.forEach((c, i) => c.classList.toggle("current", i === idx));
      lastHighlighted = idx;
    }
  }
}

requestAnimationFrame(tick);

// Other tabs (Pipeline) can open one of our exported sprites here.
window.addEventListener("inspector:open", async (e) => {
  showPage("inspector");
  state.listLoaded = true;
  state.project = e.detail.name || null;   // only this character + its layers / weapons / colour variants
  $("inspSearch").value = "";
  await refreshList();
  await loadSprite(e.detail.id);
});

$("inspBack").addEventListener("click", () => showPage("pipeline"));
