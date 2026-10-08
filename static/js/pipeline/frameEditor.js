// Per-frame editor for one AI-drawn strip: reorder, delete, duplicate, flip, nudge, hold (pause)
// and replace single frames without redrawing the row. Edits are a list kept apart from the drawing
// (core/sprite2d.apply_edits), so "Reset" always gets the drawing back.

import { t } from "../i18n.js";

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const NUDGE = 2;        // drawing pixels per click
const MAX_HOLD = 4;

const st = { slug: null, action: null, view: null, label: "", data: null, strip: null, imgs: {}, edits: [], dirty: false, sel: 0, timer: 0 };

function loadImage(url) {
  return new Promise((ok, fail) => {
    const im = new Image();
    im.onload = () => ok(im);
    im.onerror = () => fail(new Error(t("Could not load image: {url}", { url })));
    im.src = url;
  });
}

/** Source rectangle + feet of an edit entry (a frame of the drawing or a replacement image). */
function sourceOf(e) {
  if (e.file) {
    const im = st.imgs[e.file];
    if (!im) return null;
    return { img: im, box: [0, 0, im.width, im.height], feet: [im.width / 2, im.height] };
  }
  const b = st.data.boxes[e.src];
  if (!b) return null;
  return { img: st.strip, box: b, feet: st.data.feet[e.src] };
}

/** Draw one edited frame with its feet at (fx, fy) on ctx, scaled by k. */
function drawFrame(ctx, e, fx, fy, k) {
  const s = sourceOf(e);
  if (!s) return;
  const [x0, y0, x1, y1] = s.box;
  const w = x1 - x0, h = y1 - y0;
  ctx.save();
  ctx.translate(fx + (e.dx || 0) * k, fy + (e.dy || 0) * k);
  if (e.flip) ctx.scale(-1, 1);
  const ox = e.flip ? (x0 + w - s.feet[0]) : (s.feet[0] - x0);
  ctx.drawImage(s.img, x0, y0, w, h, -ox * k, -(s.feet[1] - y0) * k, w * k, h * k);
  ctx.restore();
}

export async function openFrameEditor(host, p, action, view, deps) {
  clearInterval(st.timer);
  const row = p.sheets2d?.[action];
  Object.assign(st, { slug: p.slug, action, view, sel: 0, dirty: false, imgs: {},
    label: `${row?.th || action} · ${row?.views?.[view]?.th || view}` });
  host.innerHTML = `<p class="hint">${t("Loading…")}</p>`;
  try {
    st.data = await deps.api(`/api/projects/${encodeURIComponent(p.slug)}/strip?action=${action}&view=${view}`);
    st.strip = await loadImage(st.data.url);
    for (const [name, url] of Object.entries(st.data.files || {})) st.imgs[name] = await loadImage(url);
  } catch (err) {
    host.innerHTML = `<p class="hint">${esc(err.message)}</p>`;
    return;
  }
  st.edits = st.data.edits.map((e) => ({ ...e }));
  render(host, deps);
}

function render(host, deps) {
  const d = st.data;
  const odd = new Set(d.odd_frames || []);
  host.innerHTML = `
    <div class="fe-head">
      <b>${t("Edit frames")}: ${esc(st.label)}</b>
      ${d.issues?.length ? `<span class="warnb">⚠ ${esc(d.issues.join(" · "))}</span>` : `<span class="okb">✓</span>`}
      <span class="hint">${t("Changes apply on top of the AI drawing; Reset brings the drawing back. Red outline = a frame the check found odd.")}</span>
    </div>
    <div class="fe-body">
      <canvas class="fe-preview" width="220" height="240"></canvas>
      <div class="fe-strip">
        ${st.edits.map((e, i) => `
          <div class="fe-frame ${i === st.sel ? "sel" : ""} ${e.src != null && odd.has(e.src) ? "odd" : ""}" data-i="${i}">
            <canvas width="96" height="120"></canvas>
            <div class="fe-num">${i + 1}${e.file ? " ★" : ""}${e.hold > 1 ? ` ×${e.hold}` : ""}</div>
            <div class="fe-tools">
              <button data-op="left" title="${t("Move earlier")}">◀</button><button data-op="right" title="${t("Move later")}">▶</button>
              <button data-op="flip" title="${t("Flip left-right")}">⇋</button><button data-op="dup" title="${t("Duplicate")}">⧉</button>
              <button data-op="del" title="${t("Delete")}">🗑</button>
            </div>
            <div class="fe-tools">
              <button data-op="hold-" title="${t("Shorter pause")}">−</button><span title="${t("Show this frame this many times (a pause)")}">⏸${e.hold}</span><button data-op="hold+" title="${t("Longer pause")}">+</button>
            </div>
            <div class="fe-tools">
              <button data-op="up">↑</button><button data-op="down">↓</button><button data-op="l">←</button><button data-op="r">→</button>
            </div>
          </div>`).join("")}
        <label class="fe-add dropmini"><input type="file" accept="image/*" hidden />＋ ${t("Drop an image to add a frame (then move it into place)")}</label>
      </div>
    </div>
    <div class="btn-row">
      <button class="btn small primary" data-fe="save" ${st.dirty ? "" : "disabled"}>💾 ${t("Save frames")}</button>
      <button class="btn small" data-fe="reset" ${d.edited || st.dirty ? "" : "disabled"}>↺ ${t("Reset to the drawing")}</button>
      <button class="btn small" data-fe="close">✕ ${t("Close")}</button>
      <span class="hint">${t("{n} frames × {ms} ms · pauses repeat a frame", { n: st.edits.reduce((a, e) => a + e.hold, 0), ms: d.frame_ms })}</span>
    </div>`;

  // Thumbnails: each frame fitted, feet at the bottom centre.
  host.querySelectorAll(".fe-frame").forEach((tile) => {
    const i = Number(tile.dataset.i);
    const c = tile.querySelector("canvas");
    const ctx = c.getContext("2d");
    const k = Math.min(110 / (d.height * 1.25), 1);
    drawFrame(ctx, st.edits[i], c.width / 2, c.height - 6, k);
    tile.addEventListener("click", (ev) => {
      const op = ev.target.closest("[data-op]")?.dataset.op;
      if (op) edit(i, op);
      else st.sel = i;
      render(host, deps);
    });
  });

  // Preview loop with pauses.
  const pc = host.querySelector(".fe-preview");
  const pctx = pc.getContext("2d");
  const seq = st.edits.flatMap((e) => Array(e.hold).fill(e));
  let n = 0;
  clearInterval(st.timer);
  const tick = () => {
    if (!pc.isConnected) { clearInterval(st.timer); return; }
    pctx.clearRect(0, 0, pc.width, pc.height);
    pctx.fillStyle = "rgba(0,0,0,.3)";
    pctx.beginPath(); pctx.ellipse(pc.width / 2, pc.height - 20, 34, 9, 0, 0, Math.PI * 2); pctx.fill();
    if (seq.length) drawFrame(pctx, seq[n % seq.length], pc.width / 2, pc.height - 20, Math.min(200 / (d.height * 1.25), 1));
    n++;
  };
  tick();
  st.timer = setInterval(tick, d.frame_ms);

  const add = host.querySelector(".fe-add");
  const upload = async (files) => {
    const f = [...files].find((x) => x.type.startsWith("image/"));
    if (!f) return;
    deps.busy(true, t("Removing the background…"));
    try {
      const form = new FormData();
      form.append("file", f);
      const r = await deps.api(`/api/projects/${encodeURIComponent(st.slug)}/strip/frame?action=${st.action}&view=${st.view}`, { method: "POST", body: form });
      st.imgs[r.file] = await loadImage(r.url);
      st.edits.splice(st.sel + 1, 0, { file: r.file, flip: false, dx: 0, dy: 0, hold: 1 });
      st.sel += 1;
      st.dirty = true;
    } catch (err) {
      deps.toast(err.message, true);
    } finally {
      deps.busy(false);
    }
    render(host, deps);
  };
  add.querySelector("input").addEventListener("change", (e) => upload(e.target.files));
  add.addEventListener("dragover", (e) => { e.preventDefault(); add.classList.add("drag"); });
  add.addEventListener("dragleave", () => add.classList.remove("drag"));
  add.addEventListener("drop", (e) => { e.preventDefault(); e.stopPropagation(); add.classList.remove("drag"); upload(e.dataTransfer.files); });

  host.querySelector('[data-fe="close"]').addEventListener("click", () => { clearInterval(st.timer); host.innerHTML = ""; deps.onClose?.(); });
  host.querySelector('[data-fe="reset"]').addEventListener("click", () => save(host, deps, null));
  host.querySelector('[data-fe="save"]').addEventListener("click", () => save(host, deps, st.edits));
}

function edit(i, op) {
  const e = st.edits;
  const swap = (a, b) => { if (b >= 0 && b < e.length) { [e[a], e[b]] = [e[b], e[a]]; st.sel = b; } };
  if (op === "left") swap(i, i - 1);
  if (op === "right") swap(i, i + 1);
  if (op === "flip") e[i].flip = !e[i].flip;
  if (op === "dup") { e.splice(i + 1, 0, { ...e[i] }); st.sel = i + 1; }
  if (op === "del" && e.length > 1) { e.splice(i, 1); st.sel = Math.min(i, e.length - 1); }
  if (op === "hold-") e[i].hold = Math.max(1, e[i].hold - 1);
  if (op === "hold+") e[i].hold = Math.min(MAX_HOLD, e[i].hold + 1);
  if (op === "up") e[i].dy = (e[i].dy || 0) - NUDGE;
  if (op === "down") e[i].dy = (e[i].dy || 0) + NUDGE;
  if (op === "l") e[i].dx = (e[i].dx || 0) - NUDGE;
  if (op === "r") e[i].dx = (e[i].dx || 0) + NUDGE;
  if (!["left", "right"].includes(op)) st.sel = Math.min(st.sel, e.length - 1);
  st.dirty = true;
}

async function save(host, deps, edits) {
  deps.busy(true, t("Rebuilding sprite…"));
  try {
    deps.update(await deps.api(`/api/projects/${encodeURIComponent(st.slug)}/strip?action=${st.action}&view=${st.view}`, {
      method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify({ edits }),
    }), { keepEditor: { action: st.action, view: st.view } });
    deps.toast(edits ? t("Frames saved · sprite rebuilt") : t("Back to the AI drawing"));
  } catch (err) {
    deps.toast(err.message, true);
  } finally {
    deps.busy(false);
  }
}
