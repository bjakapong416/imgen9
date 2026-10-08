// Playtest page wiring: character pickers, per-actor settings, comparison table, export.

import { actionDuration, DIRECTIONS, eventFrame, loadAnimSet } from "../anim/animset.js";
import { t } from "../i18n.js";
import { activePage, showPage } from "../tabs.js";
import { Actor, MotionLab } from "./lab.js";

const $ = (id) => document.getElementById(id);
const lab = new MotionLab($("labStage"));
window.motionLab = lab; // handy for poking at the scene from the dev console
const fmt = (v, d = 2) => (v == null || !Number.isFinite(v) ? "–" : String(Math.round(v * 10 ** d) / 10 ** d));

function toast(text, isError = false) {
  const el = $("toast");
  el.textContent = text;
  el.className = `toast${isError ? " error" : ""}`;
  el.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { el.hidden = true; }, isError ? 5000 : 2200);
}

// ───────────────────────── pickers ─────────────────────────

function listItem(label, sub, onAdd) {
  const li = document.createElement("li");
  li.innerHTML = `<span></span><small></small>`;
  li.firstChild.textContent = label;
  li.lastChild.innerHTML = `<span class="add">＋</span> ${sub}`;
  li.addEventListener("click", onAdd);
  return li;
}

async function refreshRenders() {
  const res = await fetch("/api/characters").then((r) => r.json());
  $("labRenders").replaceChildren(...res.items.map((c) =>
    listItem(c.name, c.error ? "error" : `${c.animations.length} anims${c.sprite_id ? " · 2D" : ""}`,
      () => (c.error ? toast(c.error, true) : c.sprite_id ? addCharacter("sprite", c.sprite_id, c) : addCharacter("render", c.folder)))));
  $("labRendersHint").innerHTML = res.items.length
    ? `Folder: <code>${res.root}</code>`
    : `Nothing rendered yet. Run <code>blender -b -P blender/render_8dir.py -- --name orc --files orc.fbx …</code>`;
}

let spriteTimer = 0;
async function refreshSprites() {
  const q = $("labSpriteSearch").value;
  const res = await fetch(`/api/sprites/files?q=${encodeURIComponent(q)}&limit=60`).then((r) => r.json());
  if (!res.available) {
    $("labSpriteList").replaceChildren();
    return;
  }
  $("labSpriteList").replaceChildren(...res.items.map((it) => listItem(it.name, it.folder.split("/").pop(), () => addCharacter("sprite", it.id))));
}
$("labSpriteSearch").addEventListener("input", () => { clearTimeout(spriteTimer); spriteTimer = setTimeout(refreshRo, 150); });
$("labRefresh").addEventListener("click", refreshRenders);

/** renders/<folder> that holds this character's lab_config.json: Blender renders and our own 2D sprites. */
function labFolder(set) {
  if (set.source === "render") return set.id;
  return String(set.id || "").startsWith("own_") ? set.name : null;
}

function savedConfig(source, id) {
  try { return JSON.parse(localStorage.getItem(`lab:${source}:${id}`) || "{}"); } catch { return {}; }
}

async function fetchJson(url) {
  return fetch(url).then(async (r) => {
    if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
    return r.json();
  });
}

/** Weapons a 2D character can switch between in the lab: each is a full sprite built by the tool. */
function weaponChoices(info) {
  if (!info?.weapons?.length) return null;
  const list = info.weapons.map((w) => ({ id: w.id, label: w.name, sprite_id: w.sprite_id }));
  if (info.body_sprite_id) list.push({ id: "", label: t("— Empty hands —"), sprite_id: info.body_sprite_id });
  return list;
}

async function addCharacter(source, id, info = null) {
  try {
    const url = source === "render" ? `/api/characters/${encodeURIComponent(id)}` : `/api/sprites/animset?id=${id}`;
    const data = await fetchJson(url);
    const set = await loadAnimSet(data);
    let saved = source === "render" ? { ...savedConfig(source, id), ...(data.meta?.lab || {}) } : savedConfig(source, id);
    const folder = labFolder(set);
    if (source === "sprite" && folder) {  // our 2D sprite: settings saved next to it in renders/<slug>/
      const lab = await fetch(`/api/characters/${encodeURIComponent(folder)}/lab`).then((r) => (r.ok ? r.json() : {}));
      saved = { ...saved, ...lab };
    }
    const actor = new Actor(set, saved);
    actor.key = `${source}:${id}`;
    actor.weapons = weaponChoices(info);
    actor.weaponId = info?.weapon ?? "";
    lab.add(actor);
    $("labEmpty").hidden = true;
    renderActorList();
    renderTable();
  } catch (err) {
    toast(err.message, true);
  }
}

// ───────────────────────── scene list + selection ─────────────────────────

function renderActorList() {
  $("labActors").replaceChildren(...lab.actors.map((a) => {
    const li = document.createElement("li");
    li.classList.toggle("active", a === lab.selected);
    li.innerHTML = `<span class="dot"></span><span class="grow"></span><small></small><button class="rm" title="${t("Remove from the scene (Delete)")}">✕</button>`;
    li.children[0].style.background = a.color;
    li.children[1].textContent = a.name;
    li.children[2].textContent = a.source;
    li.addEventListener("click", () => lab.select(a));
    li.querySelector(".rm").addEventListener("click", (e) => { e.stopPropagation(); removeActor(a); });
    return li;
  }));
}

$("labStage").addEventListener("select", () => {
  renderActorList();
  renderActorCard();
  renderTable();
});

function fillSelect(sel, values, current) {
  sel.replaceChildren(...values.map((v) => {
    const o = document.createElement("option");
    o.value = o.textContent = v;
    return o;
  }));
  sel.value = current;
}

function renderActorCard() {
  const a = lab.selected;
  $("labActorCard").hidden = !a;
  if (!a) return;
  $("labActorDot").style.background = a.color;
  $("labActorName").textContent = a.name;
  $("labActorSource").textContent = a.source === "sprite" ? "2D sprite" : "render";
  $("labWeaponField").hidden = !a.weapons;
  if (a.weapons) {
    $("labWeapon").replaceChildren(...a.weapons.map((w) => {
      const o = document.createElement("option");
      o.value = w.id;
      o.textContent = w.label;
      return o;
    }));
    $("labWeapon").value = a.weaponId;
  }
  for (const role of ["idle", "walk", "attack"]) {
    fillSelect($(`labRole${role[0].toUpperCase()}${role.slice(1)}`), a.set.action_types, a.cfg.roles[role]);
  }
  $("labPpm").value = fmt(a.cfg.ppm, 1);
  $("labMoveSpeed").value = fmt(a.cfg.moveSpeed);
  $("labNatural").value = a.cfg.naturalSpeed ?? "";
  $("labSync").checked = a.cfg.syncAnim;
  $("labHitFrame").value = a.cfg.hitFrame ?? "";
  $("labRange").value = fmt(a.cfg.attackRange);
  const folder = labFolder(a.set);
  $("labSave").disabled = !folder;
  $("labSave").title = folder ? `Save to renders/${folder}/lab_config.json` : "Settings are remembered in this browser";
  renderActorStats();
}

// Swap the weapon like the game would: same body, same settings, another prebuilt sprite.
$("labWeapon").addEventListener("change", async (e) => {
  const a = lab.selected;
  const w = a?.weapons?.find((x) => x.id === e.target.value);
  if (!w) return;
  try {
    const set = await loadAnimSet(await fetchJson(`/api/sprites/animset?id=${w.sprite_id}`));
    set.name = a.set.name;            // keeps lab settings saving to renders/<slug>/
    a.set = set;
    a.weaponId = w.id;
    toast(t("Weapon: {name}", { name: w.label }));
  } catch (err) {
    toast(err.message, true);
    e.target.value = a.weaponId;
  }
});

function renderActorStats() {
  const a = lab.selected;
  if (!a) return;
  const slide = a.slide();
  const bar = $("labSlideBar");
  if (slide == null) {
    bar.style.width = "0";
    $("labSlideText").textContent = "Stride speed unknown: enter it to check for foot sliding.";
  } else {
    const pct = Math.abs(slide) * 100;
    bar.style.width = `${Math.min(100, pct * 2)}%`;
    bar.style.background = pct < 5 ? "var(--hitbox)" : pct < 15 ? "var(--warn)" : "var(--pivot)";
    const rate = a.walkRate();
    $("labSlideText").textContent = pct < 0.5
      ? `Feet stick to the ground${rate !== 1 ? ` (walk anim plays at ${fmt(rate)}×)` : ""}.`
      : `Feet ${slide > 0 ? "slide backward (body faster than the steps)" : "run ahead (steps faster than the body)"} by ${fmt(pct, 0)}%.`;
  }
  const atk = a.anim("attack", "S");
  if (atk) {
    const hf = a.hitFrame();
    const hitMs = hf * atk.delay_ms;
    const dur = actionDuration(atk);
    $("labHitText").textContent = `Hit at frame ${hf}/${atk.frames.length - 1} (${a.hitSource()}) → ${Math.round(hitMs)} ms of ${Math.round(dur)} ms (${Math.round((hitMs / dur) * 100)}%). Windup ${Math.round(hitMs)} ms · recovery ${Math.round(dur - hitMs)} ms.`;
  }
}

function persist(a) {
  const data = labConfig(a);
  try { localStorage.setItem(`lab:${a.key}`, JSON.stringify(data)); } catch { /* storage blocked */ }
}

function labConfig(a) {
  return {
    roles: a.cfg.roles,
    ppm: a.cfg.ppm,
    move_speed: a.cfg.moveSpeed,
    natural_speed: a.cfg.naturalSpeed,
    sync_anim: a.cfg.syncAnim,
    hit_frame: a.cfg.hitFrame,
    attack_range: a.cfg.attackRange,
  };
}

function bindCfg(id, apply, event = "input") {
  $(id).addEventListener(event, (e) => {
    const a = lab.selected;
    if (!a) return;
    apply(a, e.target);
    persist(a);
    renderActorStats();
    renderTable();
  });
}
const num = (el) => { const v = parseFloat(el.value); return Number.isFinite(v) ? v : null; };

for (const role of ["idle", "walk", "attack"]) {
  bindCfg(`labRole${role[0].toUpperCase()}${role.slice(1)}`, (a, el) => { a.cfg.roles[role] = el.value; a.t = 0; }, "change");
}
bindCfg("labPpm", (a, el) => { const v = num(el); if (v && v >= 5) a.cfg.ppm = v; });
bindCfg("labMoveSpeed", (a, el) => { const v = num(el); if (v && v > 0) a.cfg.moveSpeed = v; });
bindCfg("labNatural", (a, el) => { const v = num(el); a.cfg.naturalSpeed = v && v > 0 ? v : null; });
bindCfg("labSync", (a, el) => { a.cfg.syncAnim = el.checked; }, "change");
bindCfg("labHitFrame", (a, el) => { const v = num(el); a.cfg.hitFrame = v == null ? null : Math.max(0, Math.round(v)); });
bindCfg("labRange", (a, el) => { const v = num(el); if (v && v > 0) a.cfg.attackRange = v; });

$("labUseNatural").addEventListener("click", () => {
  const a = lab.selected;
  if (!a?.cfg.naturalSpeed) return toast("Stride speed unknown for this character.", true);
  a.cfg.moveSpeed = a.cfg.naturalSpeed;
  persist(a);
  renderActorCard();
  renderTable();
});

function removeActor(a) {
  if (!a) return;
  lab.remove(a);
  $("labEmpty").hidden = lab.actors.length > 0;
  renderActorList();
  renderActorCard();
  renderTable();
}

$("labRemove").addEventListener("click", () => removeActor(lab.selected));

$("labSave").addEventListener("click", async () => {
  const a = lab.selected;
  const folder = a && labFolder(a.set);
  if (!folder) return toast("This sprite has no project folder to save into", true);
  const res = await fetch(`/api/characters/${encodeURIComponent(folder)}/lab`, {
    method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify(labConfig(a)),
  });
  toast(res.ok ? `Saved renders/${folder}/lab_config.json` : "Save failed", !res.ok);
});

// Engine-facing data: timing per action + the movement/attack numbers tuned here.
$("labExport").addEventListener("click", () => {
  const a = lab.selected;
  if (!a) return;
  const meta = a.set.meta || {};
  const animations = {};
  for (const type of a.set.action_types) {
    const act = a.set.getAction(type, "S");
    if (!act) continue;
    const isAttack = type === a.cfg.roles.attack;
    const hit = isAttack ? a.hitFrame() : null;
    animations[type] = {
      frames: act.frames.length,
      delay_ms: act.delay_ms,
      duration_ms: Math.round(actionDuration(act)),
      loop: a.isLoop(type === a.cfg.roles.walk ? "walk" : type === a.cfg.roles.idle ? "idle" : "attack"),
      events: Object.fromEntries(act.frames.map((f, i) => [i, f.event]).filter(([, e]) => e)),
      ...(isAttack ? { hit_frame: hit, hit_time_ms: Math.round(hit * act.delay_ms) } : {}),
    };
  }
  const out = {
    format: "character_anims/1",
    name: a.name,
    source: a.source,
    directions: DIRECTIONS,
    frame: meta.frame ? { width: meta.frame[0], height: meta.frame[1] } : undefined,
    pivot: meta.pivot,
    pixels_per_meter: a.cfg.ppm,
    roles: a.cfg.roles,
    move_speed_m_s: a.cfg.moveSpeed,
    move_speed_px_s: Math.round(a.cfg.moveSpeed * a.cfg.ppm),
    walk_anim_rate: Math.round(a.walkRate() * 1000) / 1000,
    attack_range_m: a.cfg.attackRange,
    animations,
  };
  const blob = new Blob([JSON.stringify(out, null, 2)], { type: "application/json" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = `${a.name}_anims.json`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(link.href), 2000);
});

// ───────────────────────── comparison table ─────────────────────────

function renderTable() {
  const head = `<tr><th>Character</th><th>Walk cycle</th><th>Stride speed</th><th>Game speed</th><th>Stride / cycle</th><th>Slide</th>
    <th>Attack</th><th>Hit at</th><th>Windup %</th><th>Height</th></tr>`;
  const rows = lab.actors.map((a) => {
    const walk = a.anim("walk", "S");
    const atk = a.anim("attack", "S");
    const cycle = walk ? actionDuration(walk) / a.walkRate() : null;
    const slide = a.slide();
    const slideCls = slide == null ? "" : Math.abs(slide) < 0.05 ? "good" : Math.abs(slide) < 0.15 ? "warn" : "bad";
    const hf = atk ? a.hitFrame() : null;
    const hitMs = atk ? hf * atk.delay_ms : null;
    const dur = atk ? actionDuration(atk) : null;
    const heightM = a.heightM(lab.elevation);
    return `<tr class="${a === lab.selected ? "selected" : ""}">
      <td><span class="dot" style="background:${a.color}"></span> ${a.name} <small>${a.source}</small></td>
      <td>${walk ? `${walk.frames.length}×${fmt(walk.delay_ms, 0)}ms = ${fmt(cycle, 0)}ms` : "–"}</td>
      <td>${a.cfg.naturalSpeed ? fmt(a.cfg.naturalSpeed) + " m/s" : "?"}</td>
      <td>${fmt(a.cfg.moveSpeed)} m/s</td>
      <td>${cycle ? fmt((a.cfg.moveSpeed * cycle) / 1000) + " m" : "–"}</td>
      <td class="${slideCls}">${slide == null ? "?" : `${slide > 0 ? "+" : ""}${fmt(slide * 100, 0)}%`}</td>
      <td>${atk ? `${atk.frames.length}×${fmt(atk.delay_ms, 0)}ms = ${fmt(dur, 0)}ms` : "–"}</td>
      <td>${atk ? `f${hf} · ${fmt(hitMs, 0)}ms` : "–"}</td>
      <td>${atk ? fmt((hitMs / dur) * 100, 0) + "%" : "–"}</td>
      <td>${a.heightPx()}px ≈ ${fmt(heightM)} m</td>
    </tr>`;
  });
  $("labTable").innerHTML = lab.actors.length ? head + rows.join("") : "";
}

// ───────────────────────── toolbar ─────────────────────────

fillSelect($("labDir"), DIRECTIONS, "SE");
document.querySelectorAll("#labModeSeg button").forEach((b) => b.addEventListener("click", () => {
  lab.mode = b.dataset.mode;
  document.querySelectorAll("#labModeSeg button").forEach((x) => x.classList.toggle("active", x === b));
  $("labLineupCtl").hidden = lab.mode === "playground";
  $("labDir").hidden = lab.mode === "compass";   // 8 Dirs shows every direction
  if (lab.mode === "lineup") { lab.cam = { x: 0, y: -0.6 }; lab.restartAll(); }
  if (lab.mode === "compass") lab.restartAll();
}));
$("labRole").addEventListener("change", (e) => { lab.lineup.role = e.target.value; lab.restartAll(); });
$("labDir").addEventListener("change", (e) => { lab.lineup.dir = e.target.value; });
$("labRestart").addEventListener("click", () => lab.restartAll());
$("labSpeed").addEventListener("change", (e) => { lab.speedMul = parseFloat(e.target.value); });
for (const [id, key] of [["labGrid", "grid"], ["labRanges", "ranges"], ["labTreadmill", "treadmill"], ["labLabels", "labels"], ["labFollow", "follow"]]) {
  $(id).addEventListener("change", (e) => { lab.opts[key] = e.target.checked; });
}

// ───────────────────────── keyboard ─────────────────────────

const MOVE_KEYS = new Set(["KeyW", "KeyA", "KeyS", "KeyD", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"]);
window.addEventListener("keydown", (e) => {
  if (activePage() !== "lab" || /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName)) return;
  if (MOVE_KEYS.has(e.code)) { e.preventDefault(); lab.setKey(e.code, true); }
  else if (e.code === "Space") { e.preventDefault(); if (!e.repeat) lab.attack(); }
  else if (e.code === "Delete" || e.code === "Backspace") { e.preventDefault(); if (!e.repeat) removeActor(lab.selected); }
  else if (e.code === "Tab" && lab.actors.length) {
    e.preventDefault();
    const i = lab.actors.indexOf(lab.selected);
    lab.select(lab.actors[(i + 1) % lab.actors.length]);
  }
});
window.addEventListener("keyup", (e) => { if (MOVE_KEYS.has(e.code)) lab.setKey(e.code, false); });
window.addEventListener("blur", () => { for (const k of MOVE_KEYS) lab.setKey(k, false); });

lab.onHitListeners.push((actor) => { if (actor === lab.selected) renderActorStats(); });

// Start / stop the render loop with the tab; load lists the first time it's shown.
let loaded = false;
function onPage() {
  lab.active = activePage() === "lab";
  if (lab.active && !loaded) {
    loaded = true;
    refreshRenders();
    refreshSprites();
  }
}
window.addEventListener("pagechange", onPage);
onPage();

// Other tabs (Pipeline) ask to show a character here.
window.addEventListener("lab:add", async (e) => {
  showPage("lab");
  const { source, id } = e.detail;
  const existing = lab.actors.find((a) => a.key === `${source}:${id}`);
  if (existing) lab.select(existing);
  else await addCharacter(source, id);
});
