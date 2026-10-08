// Weapon layer card (2D route): weapons drawn on their own and laid over every frame at the hand,
// as in classic 2D online games, so the game can swap them. The hand editor corrects where the weapon sits
// in each frame; the starting point comes from the pose guide the AI drew from.

import { t } from "../i18n.js";

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
// English labels, translated with t() where shown.
const ACTIONS = { idle: "Idle", walk: "Walk", attack: "Attack", cast: "Magic attack", damage: "Hurt", die: "Die" };
const VIEWS = { front: "Front 3/4", side: "Side", back: "Back 3/4" };

// Editor state survives board re-renders.
const edit = { action: "attack", view: "front", data: null, strip: null, weapon: null, weaponUrl: "", sel: 0, pending: {} };

function loadImage(url) {
  return new Promise((ok, fail) => {
    const im = new Image();
    im.onload = () => ok(im);
    im.onerror = () => fail(new Error(t("Could not load image: {url}", { url })));
    im.src = url;
  });
}

/**
 * deps: { api, toast, busy, update(project) } — update stores the returned project and re-renders.
 */
export function renderWeapons(card, p, deps) {
  const w = p.weapons;
  if (!w) return;
  if (!w.types) {   // a server started before weapon types: the card needs the new API
    card("span-12", t("Weapon"), "").insertAdjacentHTML("beforeend",
      `<p class="hint">${t("The server is an old version: restart the server and refresh the page to use weapon types")}</p>`);
    return;
  }
  const active = w.items.find((i) => i.id === w.active);
  const el = card("span-12", t("Weapons (separate layer · swappable in game)"),
    t("The character is drawn empty-handed; the weapon is a separate image laid over the hand in every frame · Weapons of the same type swap freely"));
  el.insertAdjacentHTML("beforeend", `
    <div class="weapon-bar">
      <label class="check"><input type="checkbox" data-w-layer ${w.layer ? "checked" : ""} /> ${t("Weapon as a separate layer")}</label>
      ${w.layer ? `<label class="check" title="${t("Idle, walk, hurt, die: empty hands, no weapon · The weapon shows only when attacking (physical and magic)")}"><input type="checkbox" data-w-attack-only ${w.attack_only ? "checked" : ""} /> ${t("Weapon only in attacks")}</label>` : ""}
      <span class="hint">${w.layer
        ? t("Every pose prompt asks for <b>empty hands</b> (gripping as if holding a weapon): redraw the poses if the old images have a weapon in hand")
        : t("Off: the weapon is drawn as part of the poses and can't be swapped in game")}</span>
    </div>
    <div class="weapon-desc">
      <label>${t("Character's weapon type")}</label>
      <select data-w-type>
        <option value="" ${w.type ? "" : "selected"}>${t("— No weapon —")}</option>
        ${Object.entries(w.types).map(([k, ty]) => `<option value="${k}" ${k === w.type ? "selected" : ""}>${esc(ty.th)} · ${t("{style} attack", { style: esc(ty.style_th) })}</option>`).join("")}
      </select>
      <span class="hint">${t("Sets the attack the prompts and the 🦴 pose guide ask for. Weapons with the same attack style swap without redrawing the character")}</span>
    </div>
    <div class="weapon-desc">
      <span class="hint">${t("🧪 Try a sample weapon:")}</span>
      ${Object.entries(w.types).map(([k, ty]) => `<button class="btn small" data-w-sample="${k}" title="${t("{style} attack", { style: esc(ty.style_th) })}">${esc(ty.th)}</button>`).join("")}
    </div>
    <div class="weapon-desc">
      <label>${t("Held weapon (English, used in prompts)")}</label>
      <input data-w-desc value="${esc(w.desc_en)}" placeholder="e.g. a wooden staff whose top curls into a hook holding a blue crystal" />
      <button class="btn small" data-w-prompt title="${t("Prompt for an AI to draw the weapon alone, upright, handle down")}">${t("📋 Weapon prompt")}</button>
    </div>
    <div class="weapon-list">
      ${w.items.map((it) => {
        const ty = w.types[it.type] || w.types.staff;
        const clash = w.type && ty.style !== w.types[w.type].style
          ? `<small class="warnb" title="${t("The character is drawn with a {char} attack; this weapon uses a {weapon} attack: the attacks won't match until you change the character's weapon type and redraw the attack", { char: esc(w.types[w.type].style_th), weapon: esc(ty.style_th) })}">${t("⚠ Attack mismatch")}</small>` : "";
        return `
        <div class="weapon-item ${it.id === w.active ? "active" : ""}" data-w-pick="${it.id}" title="${t("Use this weapon")}">
          <img src="${esc(it.url)}" alt="" />
          <input data-w-name="${it.id}" value="${esc(it.name)}" />
          <select data-w-itype="${it.id}">${Object.entries(w.types).map(([k, x]) =>
            `<option value="${k}" ${k === (it.type || "staff") ? "selected" : ""}>${esc(x.th)}</option>`).join("")}</select>${clash}
          <label>${t("Length × body")} <input type="number" step="0.05" min="0.1" max="3" data-w-size="${it.id}" value="${it.size}" /></label>
          <label>${t("Grip")} <input type="number" step="0.02" min="0" max="1" data-w-grip="${it.id}" value="${it.grip[1]}" title="${t("0 = top end, 1 = bottom end")}" /></label>
          <button class="rm" data-w-rm="${it.id}" title="${t("Remove")}">✕</button>
        </div>`;
      }).join("")}
      <label class="weapon-drop" data-w-drop><input type="file" accept="image/*" hidden />${t("＋ Drop a weapon image")}<br /><small>${t("(drawn upright, handle down)")}</small></label>
    </div>
    ${w.extras.length ? `<div class="weapon-extras"><span class="hint">${t("Items split from the turnaround sheet:")}</span>
      ${w.extras.map((x) => `<button class="btn small" data-w-extra="${esc(x.name)}"><img src="${esc(x.url)}" alt="" /> ${t("Use as weapon")}</button>`).join("")}</div>` : ""}
    ${active ? `
      <div class="weapon-editor">
        <div class="weapon-editor-bar">
          <b>${t("Weapon grip")}</b>
          <div class="seg small">${Object.entries(ACTIONS).filter(([a]) => (a !== "cast" || p.sheets2d_magic) && (!w.attack_only || a === "attack" || a === "cast")).map(([a, label]) =>
            `<button data-w-action="${a}" class="${a === edit.action ? "active" : ""}">${t(label)}</button>`).join("")}</div>
          <div class="seg small">${Object.entries(VIEWS).filter(([v]) => v !== "side" || p.sheets2d_side).map(([v, label]) =>
            `<button data-w-view="${v}" class="${v === edit.view ? "active" : ""}">${t(label)}</button>`).join("")}</div>
          <span class="hint">${t("Click a frame, then drag to move the hand · Mouse wheel or slider = rotate")}</span>
        </div>
        <canvas data-w-canvas height="300"></canvas>
        <div class="weapon-editor-bar">
          <span data-w-frame class="hint"></span>
          <label>${t("Rotate")} <input type="range" min="-180" max="180" step="1" data-w-angle /></label>
          <label class="check"><input type="checkbox" data-w-behind /> ${t("Behind the body")}</label>
          <button class="btn small" data-w-reset>${t("↺ From pose guide")}</button>
          <button class="btn small primary" data-w-save>${t("💾 Save grip")}</button>
          <span class="hint">${t("Edit only 2 views; the other 6 directions come from flipping · {n} frames edited", { n: w.overrides })}</span>
        </div>
      </div>` : `<p class="hint">${t("No weapon yet: drop a weapon image or use an item from the turnaround sheet")}</p>`}`);

  const put = async (patch, msg) => {
    try {
      deps.busy(true, t("Rebuilding sprite…"));
      deps.update(await deps.api(`/api/projects/${p.slug}/weapons`, {
        method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify(patch),
      }));
      if (msg) deps.toast(msg);
    } catch (err) {
      deps.toast(err.message, true);
    } finally {
      deps.busy(false);
    }
  };

  el.querySelector("[data-w-layer]").addEventListener("change", (e) => put({ layer: e.target.checked },
    e.target.checked ? t("Weapon split off · pose prompts now ask for empty hands") : t("Weapon merged with the character")));
  el.querySelector("[data-w-desc]").addEventListener("change", (e) => put({ desc_en: e.target.value }));
  el.querySelector("[data-w-attack-only]")?.addEventListener("change", (e) => put({ attack_only: e.target.checked },
    e.target.checked ? t("Weapon shows only in attacks · empty hands in other actions") : t("Weapon in hand in every action")));
  el.querySelector("[data-w-type]").addEventListener("change", (e) => {
    if (!e.target.value) return put({ type: "" }, t("Character holds no weapon"));
    const ty = w.types[e.target.value];
    put({ type: e.target.value }, t("Weapon type: {type} · prompts and pose guide now use a {style} attack (redraw the attack)", { type: ty.th, style: ty.style_th }));
  });
  el.querySelectorAll("[data-w-sample]").forEach((b) => b.addEventListener("click", async () => {
    try {
      deps.busy(true, t("Adding sample weapon…"));
      deps.update(await deps.api(`/api/projects/${p.slug}/weapons/sample?type=${b.dataset.wSample}`, { method: "POST" }));
      const name = w.types[b.dataset.wSample].th;
      deps.toast(w.layer ? t("Trying {name}", { name })
        : t("Trying {name} · turn on \"Weapon as a separate layer\" to lay it over the character", { name }));
    } catch (err) {
      deps.toast(err.message, true);
    } finally {
      deps.busy(false);
    }
  }));
  el.querySelectorAll("[data-w-itype]").forEach((s) => s.addEventListener("change", () => put({ items: { [s.dataset.wItype]: { type: s.value } } })));
  el.querySelector("[data-w-prompt]").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(w.prompt);
      deps.toast(t("Weapon prompt copied · attach the character image so the line art matches"));
    } catch {
      deps.toast(t("Could not copy"), true);
    }
  });
  el.querySelectorAll("[data-w-pick]").forEach((d) => d.addEventListener("click", (e) => {
    if (e.target.closest("input, button") || d.dataset.wPick === w.active) return;
    put({ active: d.dataset.wPick }, t("Weapon changed"));
  }));
  el.querySelectorAll("[data-w-name]").forEach((i) => i.addEventListener("change", () => put({ items: { [i.dataset.wName]: { name: i.value } } })));
  el.querySelectorAll("[data-w-size]").forEach((i) => i.addEventListener("change", () => put({ items: { [i.dataset.wSize]: { size: Number(i.value) } } })));
  el.querySelectorAll("[data-w-grip]").forEach((i) => i.addEventListener("change", () => {
    const it = w.items.find((x) => x.id === i.dataset.wGrip);
    put({ items: { [it.id]: { grip: [it.grip[0], Number(i.value)] } } });
  }));
  el.querySelectorAll("[data-w-rm]").forEach((b) => b.addEventListener("click", async () => {
    try {
      deps.update(await deps.api(`/api/projects/${p.slug}/weapons/${b.dataset.wRm}`, { method: "DELETE" }));
    } catch (err) {
      deps.toast(err.message, true);
    }
  }));
  el.querySelectorAll("[data-w-extra]").forEach((b) => b.addEventListener("click", async () => {
    try {
      deps.busy(true, t("Cutting the weapon out of the turnaround sheet…"));
      deps.update(await deps.api(`/api/projects/${p.slug}/weapons/from-extra?extra=${encodeURIComponent(b.dataset.wExtra)}`, { method: "POST" }));
      deps.toast(t("Weapon added"));
    } catch (err) {
      deps.toast(err.message, true);
    } finally {
      deps.busy(false);
    }
  }));

  // Weapon images: the only image drop zone after step 1 besides the action sheets (agreed with the user).
  const drop = el.querySelector("[data-w-drop]");
  const upload = async (files) => {
    for (const f of [...files].filter((x) => x.type.startsWith("image/"))) {
      try {
        deps.busy(true, t("Removing the background of {name} and straightening the weapon…", { name: f.name }));
        const form = new FormData();
        form.append("file", f);
        form.append("name", f.name.replace(/\.[^.]+$/, ""));
        form.append("type", w.type || "");
        deps.update(await deps.api(`/api/projects/${p.slug}/weapons`, { method: "POST", body: form }));
        deps.toast(t("Weapon {name} added", { name: f.name }));
      } catch (err) {
        deps.toast(err.message, true);
      } finally {
        deps.busy(false);
      }
    }
  };
  drop.querySelector("input").addEventListener("change", (e) => upload(e.target.files));
  drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("drag"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("drag"));
  drop.addEventListener("drop", (e) => { e.preventDefault(); e.stopPropagation(); drop.classList.remove("drag"); upload(e.dataTransfer.files); });

  if (w.attack_only && edit.action !== "cast") edit.action = "attack";   // the actions that carry the weapon
  if (edit.action === "cast" && !p.sheets2d_magic) edit.action = "attack";
  if (active) bindEditor(el, p, active, deps, put);
}

// ───────────────────────── hand editor ─────────────────────────

function effective(i) {
  const a = edit.data.anchors[i];
  const o = edit.pending[edit.data.keys[i]] ?? a.override ?? {};
  return {
    x: a.auto.x + (o.dx || 0), y: a.auto.y + (o.dy || 0), angle: a.auto.angle + (o.da || 0),
    behind: "behind" in o ? o.behind : a.auto.behind, o,
  };
}

function setOverride(i, change) {
  const key = edit.data.keys[i];
  const cur = { ...(edit.pending[key] ?? edit.data.anchors[i].override ?? {}) };
  edit.pending[key] = { ...cur, ...change };
}

async function bindEditor(el, p, item, deps, put) {
  const canvas = el.querySelector("[data-w-canvas]");
  const ctx = canvas.getContext("2d");
  const angle = el.querySelector("[data-w-angle]");
  const behind = el.querySelector("[data-w-behind]");
  const label = el.querySelector("[data-w-frame]");
  let layout = null;

  const load = async () => {
    try {
      edit.data = await deps.api(`/api/projects/${p.slug}/weapon-frames?action=${edit.action}&view=${edit.view}`);
      [edit.strip, edit.weapon] = await Promise.all([loadImage(edit.data.url), edit.weaponUrl === item.url && edit.weapon ? edit.weapon : loadImage(item.url)]);
      edit.weaponUrl = item.url;
      edit.sel = Math.min(edit.sel, edit.data.boxes.length - 1);
    } catch (err) {
      edit.data = null;
      label.textContent = err.message;
    }
    draw();
  };

  const draw = () => {
    const dpr = window.devicePixelRatio || 1;
    const cssW = canvas.parentElement.clientWidth;
    const cssH = 300;
    canvas.style.width = `${cssW}px`;
    canvas.style.height = `${cssH}px`;
    canvas.width = Math.round(cssW * dpr);
    canvas.height = Math.round(cssH * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = "#0c0f14";
    ctx.fillRect(0, 0, cssW, cssH);
    const d = edit.data;
    if (!d || !edit.strip) return;
    const n = d.boxes.length;
    const cellW = cssW / n;
    const maxH = d.height * 1.9;                       // room for a raised weapon above the head
    const scale = Math.min((cssH - 24) / maxH, cellW / (d.height * 1.4));
    const base = cssH - 16;
    layout = { cellW, scale, base, n };
    ctx.imageSmoothingEnabled = scale < 1;
    for (let i = 0; i < n; i++) {
      const [x0, y0, x1, y1] = d.boxes[i];
      const [fx, fy] = d.feet[i];
      const cx = cellW * i + cellW / 2;
      const a = effective(i);
      const hx = cx + a.x * d.height * scale;
      const hy = base + a.y * d.height * scale;
      const drawWeapon = () => {
        const wh = item.size * d.height * scale;
        const ww = (edit.weapon.width / edit.weapon.height) * wh * (item.squash ?? 1);
        ctx.save();
        ctx.translate(hx, hy);
        ctx.rotate((a.angle * Math.PI) / 180);
        ctx.drawImage(edit.weapon, -item.grip[0] * ww, -item.grip[1] * wh, ww, wh);
        ctx.restore();
      };
      if (i === edit.sel) {
        ctx.fillStyle = "rgba(79,140,255,0.10)";
        ctx.fillRect(cellW * i + 2, 2, cellW - 4, cssH - 4);
      }
      if (a.behind) drawWeapon();
      ctx.drawImage(edit.strip, x0, y0, x1 - x0, y1 - y0,
        cx - (fx - x0) * scale, base - (fy - y0) * scale, (x1 - x0) * scale, (y1 - y0) * scale);
      if (!a.behind) drawWeapon();
      ctx.fillStyle = i === edit.sel ? "#4f8cff" : "rgba(255,255,255,0.5)";
      ctx.beginPath();
      ctx.arc(hx, hy, i === edit.sel ? 5 : 3, 0, Math.PI * 2);
      ctx.fill();
      ctx.fillStyle = Object.keys(a.o).length ? "#f5d04a" : "rgba(230,233,239,0.6)";
      ctx.font = "11px ui-monospace, monospace";
      ctx.textAlign = "center";
      ctx.fillText(`f${i}${Object.keys(a.o).length ? " ✎" : ""}`, cx, cssH - 3);
    }
    const a = effective(edit.sel);
    angle.value = Math.round(a.angle);
    behind.checked = a.behind;
    label.textContent = t("{action} · {view} · frame {n} · rotation {deg}°",
      { action: t(ACTIONS[edit.action]), view: t(VIEWS[edit.view]), n: edit.sel, deg: Math.round(a.angle) });
  };

  let drag = null;
  canvas.addEventListener("pointerdown", (e) => {
    if (!layout) return;
    const r = canvas.getBoundingClientRect();
    const i = Math.min(layout.n - 1, Math.floor((e.clientX - r.left) / layout.cellW));
    if (i !== edit.sel) { edit.sel = i; draw(); return; }
    drag = { x: e.clientX, y: e.clientY, o: { ...effective(i).o } };
    canvas.setPointerCapture(e.pointerId);
  });
  canvas.addEventListener("pointermove", (e) => {
    if (!drag) return;
    const k = edit.data.height * layout.scale;
    setOverride(edit.sel, { dx: (drag.o.dx || 0) + (e.clientX - drag.x) / k, dy: (drag.o.dy || 0) + (e.clientY - drag.y) / k });
    draw();
  });
  canvas.addEventListener("pointerup", () => { drag = null; });
  canvas.addEventListener("wheel", (e) => {
    if (!layout) return;
    e.preventDefault();
    const o = effective(edit.sel).o;
    setOverride(edit.sel, { da: (o.da || 0) + (e.deltaY > 0 ? 5 : -5) });
    draw();
  }, { passive: false });
  angle.addEventListener("input", () => {
    const auto = edit.data.anchors[edit.sel].auto.angle;
    setOverride(edit.sel, { da: Number(angle.value) - auto });
    draw();
  });
  behind.addEventListener("change", () => { setOverride(edit.sel, { behind: behind.checked }); draw(); });
  el.querySelector("[data-w-reset]").addEventListener("click", () => {
    edit.pending[edit.data.keys[edit.sel]] = {};
    draw();
  });
  el.querySelector("[data-w-save]").addEventListener("click", async () => {
    const overrides = Object.fromEntries(Object.entries(edit.pending).map(([k, o]) => [k, Object.keys(o).length ? o : null]));
    if (!Object.keys(overrides).length) return deps.toast(t("No frame edited yet"));
    edit.pending = {};
    await put({ overrides }, t("Grip saved for {n} frames", { n: Object.keys(overrides).length }));
  });
  el.querySelectorAll("[data-w-action]").forEach((b) => b.addEventListener("click", () => {
    edit.action = b.dataset.wAction;
    el.querySelectorAll("[data-w-action]").forEach((x) => x.classList.toggle("active", x === b));
    load();
  }));
  el.querySelectorAll("[data-w-view]").forEach((b) => b.addEventListener("click", () => {
    edit.view = b.dataset.wView;
    el.querySelectorAll("[data-w-view]").forEach((x) => x.classList.toggle("active", x === b));
    load();
  }));
  await load();
}
