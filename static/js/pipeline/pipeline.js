// Pipeline tab: one concept image -> classified profile -> step checklist -> Character Board.
// The board mirrors a character design sheet (movement x8, walk detail, attack x8, extra poses,
// skill VFX, concept vs result) and fills each slot with real rendered frames as they appear.

import { IMAGE_AIS, imageAi, openConnections } from "../connections.js";
import { DIRECTIONS, drawFrame, eventFrame, frameIndexAt, loadAnimSet, setBounds } from "../anim/animset.js";
import { activePage } from "../tabs.js";
import { renderWeapons } from "./weapons.js";
import { openFrameEditor } from "./frameEditor.js";
import { renderVariants } from "./variants.js";
import { renderHats } from "./hats.js";
import { DEFAULT_GROUP, askNewGroup, groupName, groupOptions, renderGroupList, renderGroupPage } from "./groups.js";
import { t } from "../i18n.js";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const BOARD_ORDER = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"];
const DIR_LABEL = {
  N: t("North ↑"), NE: t("Northeast ↗"), E: t("East →"), SE: t("Southeast ↘"),
  S: t("South ↓"), SW: t("Southwest ↙"), W: t("West ←"), NW: t("Northwest ↖"),
};
const ELEMENTS = {
  fire: t("Fire"), water: t("Water"), ice: t("Ice"), earth: t("Earth"), wind: t("Wind"), lightning: t("Lightning"),
  nature: t("Nature"), dark: t("Dark"), holy: t("Holy"), poison: t("Poison"), neutral: t("Neutral"),
};
const CATEGORIES = { player: t("Player"), monster: t("Monster"), pet: t("Special pet"), npc: "NPC", boss: t("Boss") };
const BODIES = {
  humanoid: t("Humanoid (2 legs)"), quadruped: t("Quadruped"), winged_quadruped: t("Quadruped + wings"), bird: t("Bird"),
  serpent: t("Serpent / legless dragon"), blob: t("Blob (round and bouncy)"), insect: t("Insect"), other: t("Not sure"),
};
const WHERE = {
  local: t("This machine"), ai: t("AI assistant (chat)"), blender: t("Blender (automatic)"),
  external: t("External / by hand"), missing: t("Not supported yet"),
};
const ANIM_ALIASES = {
  idle: /idle|stand|breath|wait/i, walk: /walk|move/i, run: /run|sprint|dash/i, attack: /attack|atk|bite|slash|claw|punch|strike/i,
  hurt: /hurt|damage|hit/i, die: /die|death|dead/i, sit: /sit/i, sleep: /sleep|lie|rest/i, happy: /happy|cheer|victory|joy/i, cast: /cast|skill|spell/i,
};

const VIEWS = ["front", "front_3q", "side", "back"];
const VIEW_LABEL = { front: t("Front"), front_3q: t("3/4 front"), side: t("Side"), back: t("Back") };
const REQUIRED_VIEWS = ["front", "side", "back"];
const VIEW_TO_DIRS = { front: ["S"], front_3q: ["SW", "SE"], side: ["W", "E"], back: ["N"] };
const MODEL_EXT = /\.(fbx|glb|gltf|blend|obj)$/i;
const IMAGE_EXT = /\.(png|jpe?g|webp|bmp|gif)$/i;

// Turnaround prompt, adapted per image AI. The layout rules are what the step-1 splitter and
// Hunyuan3D-2mv need: separate figures, side view facing left, same scale, no text.
const LAYOUT_RULES = "Layout rules: ONE single image containing 4 full-body views of the same character in ONE horizontal row, "
  + "in this order: front, front three-quarter, left side (character facing image-left), back. "
  + "Same scale and same height for every view, feet on the same baseline, clear empty space between the views "
  + "(views must not touch or overlap), nothing cropped. No text, no labels, no arrows, no frames, no grid. "
  + "Plain pure white background, even flat lighting, no shadows on the ground. Wide image, at least 2048 px wide.";

const PROMPT_TARGETS = {
  gemini: {
    label: "Gemini",
    url: "https://gemini.google.com/",
    how: t("Attach the character's front image (the ⬇ Download the front view button at step 1), then paste this prompt"),
    build: (d) => "Use the attached image as the exact character design reference: keep the same face, hair, outfit, "
      + "colours and proportions. Create a character turnaround sheet for 3D modeling.\n\n" + d + "\n\n" + LAYOUT_RULES
      + "\n\nReturn only the image.",
  },
  chatgpt: {
    label: "ChatGPT",
    url: "https://chatgpt.com/",
    how: t("Attach the character's front image, then paste this prompt (if you don't get all 4 views, ask again: \"keep all 4 views in one row\")"),
    build: (d) => "I attached a character image. Keep this exact character design (same face, hair, outfit, colours, "
      + "proportions) and draw it as a turnaround reference sheet for 3D modeling, as one wide 3:2 image.\n\n" + d + "\n\n" + LAYOUT_RULES,
  },
  generic: {
    label: t("Other AI"),
    url: null,
    how: t("For other image generators (e.g. Midjourney, Leonardo, Stable Diffusion); if it accepts a reference image, attach the front image too"),
    build: (d) => d + "\n\n" + LAYOUT_RULES,
  },
  claude: {
    label: "Claude",
    url: null,
    how: t("Claude (including Claude Code in this chat) can't make images; it only answers in text. Use the prompt with Gemini or ChatGPT instead.")
      + " " + t("To change the character details in the prompt, say so in this chat and Claude will rewrite the prompt"),
    build: null,
  },
};
let promptTarget = imageAi();   // the image AI picked in 🔌 Connections (or on a prompt box)
window.addEventListener("conn:target", (e) => {
  promptTarget = e.detail.target;
  if (project) renderBoard();
});
const rememberTarget = (k) => { try { localStorage.setItem("pipe:target", k); } catch { /* storage blocked */ } };
// Set when a per-action sprite prompt is copied: the next image dropped at the sheets step fills that
// slot. Without it a dropped image is the all-actions grid.
let pendingSheet = null; // {action, view, label}
let frameEdit = null;    // {action, view} of the strip open in the frame editor

const SHEET_WRAP = {
  gemini: (s) => "Use the attached image as the exact character reference.\n\n" + s + "\n\nReturn only the image.",
  chatgpt: (s) => "I attached a reference image of my character. Keep the exact same character design.\n\n" + s,
  generic: (s) => s,
};

let caps = { claude: false, blender: null };
let project = null;
let groups = [];                 // projects (groups of characters), from /api/groups
let currentGroup = DEFAULT_GROUP; // where a new character goes
let viewGroup = null;            // the project page being shown (instead of a character)
let anim = null; // loaded animset of the rendered character (if any)
let cells = [];  // live sprite canvases on the board
let pollTimer = 0;

// ───────────────────────── api ─────────────────────────

async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  const body = res.headers.get("content-type")?.includes("json") ? await res.json() : null;
  if (!res.ok) throw new Error(body?.detail ? (typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail)) : res.statusText);
  return body;
}

function toast(text, isError = false) {
  const el = $("toast");
  el.textContent = text;
  el.className = `toast${isError ? " error" : ""}`;
  el.hidden = false;
  clearTimeout(toast.t);
  toast.t = setTimeout(() => { el.hidden = true; }, isError ? 6000 : 2500);
}

function busy(on, text) {
  $("pipeBusy").hidden = !on;
  if (text) $("pipeBusyText").textContent = text;
}

// ───────────────────────── projects ─────────────────────────

async function loadCaps() {
  caps = await api("/api/pipeline/capabilities");
  $("pipeCaps").innerHTML = t("One full-body image is enough to start · you can type the character description yourself in the character info card")
    + (caps.blender ? "<br>" + t("✅ Blender found (3D route)") : "")
    + (caps.assistant === false ? `<br>${t("ℹ No AI assistant found (Claude Code, Gemini CLI or Codex): choose “Fill in yourself”")} · <a href="#" data-open-conn>${t("🔌 Connections")}</a>` : "");
  $("pipeCaps").querySelector("[data-open-conn]")?.addEventListener("click", (e) => { e.preventDefault(); openConnections(); });
  // Remember how this person likes to fill in character info.
  try {
    const m = localStorage.getItem("pipe:method");
    if (m && [...$("pipeMethod").options].some((o) => o.value === m)) $("pipeMethod").value = m;
  } catch { /* storage blocked: keep the default */ }
  $("pipeMethod").onchange = (e) => { try { localStorage.setItem("pipe:method", e.target.value); } catch { /* ignore */ } };
}

async function loadList() {
  const [{ items }, g] = await Promise.all([api("/api/projects"), api("/api/groups")]);
  groups = g.items;
  if (!groups.some((x) => x.id === currentGroup)) currentGroup = DEFAULT_GROUP;
  for (const p of items) p.label = `${ELEMENTS[p.element] || p.element} · ${BODIES[p.body_type] || p.body_type}`;
  renderGroupList($("pipeProjects"), groups, items, {
    activeSlug: project?.slug, activeGroup: viewGroup?.id,
    onCharacter: openProject, onGroup: openGroup, onNew: newCharacterIn,
  });
  fillGroupPicker();
  return items;
}

function fillGroupPicker() {
  $("pipeGroup").innerHTML = groupOptions(groups, currentGroup);
  const g = groups.find((x) => x.id === currentGroup);
  $("pipeDropTo").textContent = t("→ a new character in {name}", { name: groupName(g) });
}

async function pickGroup(id) {
  if (id === "__new") id = (await askNewGroup(api, toast)) || currentGroup;
  currentGroup = id;
  await loadList();
}

/** "+" on a project: choose it for the next upload and open the file picker. */
async function newCharacterIn(gid) {
  await pickGroup(gid);
  $("pipeDrop").classList.add("flash");
  setTimeout(() => $("pipeDrop").classList.remove("flash"), 1200);
  $("pipeFile").click();
}

async function openGroup(gid) {
  busy(true, t("Loading…"));
  try {
    viewGroup = await api(`/api/groups/${gid}`);
    project = null;
    currentGroup = gid;
    await render();
    await loadList();
  } catch (err) {
    toast(err.message, true);
  } finally {
    busy(false);
  }
}

async function createFromFile(file) {
  if (!file) return;
  const form = new FormData();
  form.append("file", file);
  form.append("name", $("pipeName").value.trim());
  form.append("method", $("pipeMethod").value);
  form.append("group", currentGroup);
  busy(true, t("Removing background + classifying character…"));
  try {
    project = await api("/api/projects", { method: "POST", body: form });
    $("pipeName").value = "";
    await loadList();
    await render();
  } catch (err) {
    toast(err.message, true);
  } finally {
    busy(false);
  }
}

async function openProject(slug) {
  busy(true, t("Loading…"));
  try {
    project = await api(`/api/projects/${slug}`);
    viewGroup = null;
    currentGroup = project.group?.id || currentGroup;
    await render();
    await loadList();
  } catch (err) {
    toast(err.message, true);
  } finally {
    busy(false);
  }
}

async function patch(body) {
  project = await api(`/api/projects/${project.slug}`, {
    method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify(body),
  });
  renderSteps();
}

// ───────────────────────── render everything ─────────────────────────

async function render() {
  $("pipeEmpty").hidden = !!project || !!viewGroup;
  $("pipeBoard").hidden = !project && !viewGroup;
  if (!project && viewGroup) {
    cells = [];
    renderSteps();
    renderGroupPage($("pipeBoard"), viewGroup, {
      api, toast, busy, onCharacter: openProject, onNew: newCharacterIn,
      reload: async (g) => { if (g) viewGroup = g; else { viewGroup = null; currentGroup = DEFAULT_GROUP; } await loadList(); await render(); },
      labAdd: (list) => list.forEach((c) => window.dispatchEvent(new CustomEvent("lab:add", { detail: { source: "sprite", id: c.sprite_id } }))),
    });
    return;
  }
  if (!project) return;
  anim = null;
  if (project.renders) {
    try {
      const built = project.renders.sprite;
      const src = project.renders.source === "2d" && built ? `/api/sprites/animset?id=${built.sprite_id}` : `/api/characters/${project.slug}`;
      anim = await loadAnimSet(await api(src));
    } catch (err) {
      console.warn("no animset yet", err);
    }
  }
  renderBoard();
  renderSteps();
  schedulePoll();
}

function findType(key) {
  if (!anim) return null;
  const re = ANIM_ALIASES[key] || new RegExp(key, "i");
  return anim.action_types.find((t) => re.test(t)) || null;
}

function hitFrame(type) {
  const a = anim?.getAction(type, "S");
  if (!a) return 0;
  const lab = anim.meta?.lab?.hit_frame;
  if (lab != null) return Math.min(lab, a.frames.length - 1);
  const ev = eventFrame(a, "atk");
  return ev >= 0 ? ev : Math.round((a.frames.length - 1) * 0.59);
}

/** A live sprite canvas. mode: "play" or a fixed frame index. */
function spriteCell(type, dir, mode, label, opts = {}) {
  const wrap = document.createElement("div");
  wrap.className = `cell${opts.big ? " big" : ""}`;
  if (!type || !anim?.getAction(type, dir)) {
    wrap.innerHTML = `<div class="placeholder">${t("None yet")}${label ? `<br>${esc(label)}` : ""}</div>${label ? `<span>${esc(label)}</span>` : ""}`;
    return wrap;
  }
  const c = document.createElement("canvas");
  wrap.appendChild(c);
  if (label) {
    const s = document.createElement("span");
    s.innerHTML = opts.tagClass ? `<span class="tag ${opts.tagClass}">${esc(label)}</span>` : esc(label);
    wrap.appendChild(s);
  }
  cells.push({ canvas: c, type, dir, mode });
  return wrap;
}

/** What Playtest loads for this project: the sprite on the 2D route, the Blender render otherwise. */
function labSource(p) {
  const built = p.renders?.sprite;
  return p.renders?.source === "2d" && built?.sprite_id ? { source: "sprite", id: built.sprite_id } : { source: "render", id: p.slug };
}

// Optional cards the user unfolded or folded: "<slug>|<title>" → open, kept while the page is open.
const foldOpen = new Map();

function renderBoard() {
  cells = [];
  const p = project;
  const prof = p.profile;
  const board = $("pipeBoard");
  board.replaceChildren();

  const is2d = (p.mode || "2d") === "2d";
  const stepOf = (id) => {
    const i = (p.steps || []).findIndex((s) => s.id === id);
    return i < 0 ? null : { ...p.steps[i], n: i + 1 };
  };
  /** role: { kind: "req" | "opt" | "auto", step: step id, open: bool } → a badge saying what the user has
   * to do here. Optional cards start folded (open when already in use); a click on the title unfolds them. */
  const card = (cls, title, sub = "", role = null) => {
    const el = document.createElement("section");
    el.className = `bcard ${cls}`;
    let badge = "";
    if (is2d && role) {
      const s = role.step ? stepOf(role.step) : null;
      if (role.kind === "req") {
        badge = s?.done ? `<span class="role done">✓ ${t("Done")}</span>`
          : `<span class="role req">${t("Required")}</span>`;
      } else if (role.kind === "opt") {
        badge = `<span class="role opt">${t("Optional")}</span>`;
      } else {
        badge = `<span class="role auto">${t("Automatic")}</span>`;
      }
      if (s?.next) el.classList.add("next-card");
    }
    const fold = is2d && role?.kind === "opt";
    const key = `${p.slug}|${title}`;
    if (fold && !(foldOpen.get(key) ?? role.open)) el.classList.add("folded");
    const sn = is2d && role?.step ? stepOf(role.step) : null;
    const num = sn ? `<span class="step-no ${sn.done ? "done" : ""} ${sn.next ? "next" : ""}" title="${esc(t("Step {n}", { n: sn.n }))}">${sn.n}</span>` : "";
    el.innerHTML = `<h3>${num}${badge}${title}${sub ? ` <small>${sub}</small>` : ""}${fold
      ? `<button class="fold-btn" type="button">${el.classList.contains("folded") ? t("▸ Show") : t("▾ Hide")}</button>` : ""}</h3>`;
    if (fold) {
      const h = el.querySelector("h3");
      h.classList.add("foldable");
      h.addEventListener("click", (e) => {
        if (e.target.closest("a, input, select, label") && !e.target.closest(".fold-btn")) return;
        const folded = el.classList.toggle("folded");
        foldOpen.set(key, !folded);
        h.querySelector(".fold-btn").textContent = folded ? t("▸ Show") : t("▾ Hide");
        if (!folded) layoutCells();
      });
    }
    board.appendChild(el);
    return el;
  };
  // Optional cards in use (a weapon layer, a hat, colour variants) start unfolded.
  const inUse = {
    [t("Weapons (separate layer · swappable in game)")]: !!(p.weapons?.layer || p.weapons?.active),
    [t("Weapon")]: !!p.weapons?.active,
    [t("🎩 Headgear (separate layer · swappable in game)")]: !!p.hats2d?.active,
    [t("🎨 Colour variants")]: !!p.variants2d?.items?.length,
  };
  const optional = (cls, title, sub = "") => card(cls, title, sub, { kind: "opt", open: !!inUse[title] });

  // ── 0. what to do next (2D): the one thing to do now, and which cards can be skipped ──
  if (is2d) renderGuide(board, p);

  // ── 1. profile ──
  const byAi = prof._method === "claude" || prof._method === "claude_code";
  const pc = card("span-5", t("Character info"), `<span class="method ${byAi ? "ai" : ""}">${p.ai_pending ? t("Temporary guess · waiting for your AI assistant") : byAi ? t("Classified by AI") : t("Guessed classification")}</span>`, { kind: "req", step: "classify" });
  const conf = (k) => {
    const v = prof._confidence?.[k];
    return v == null ? "" : `<span class="conf ${v < 0.5 ? "low" : "ok"}">${Math.round(v * 100)}%</span>`;
  };
  const opt = (map, cur) => Object.entries(map).map(([k, v]) => `<option value="${k}" ${k === cur ? "selected" : ""}>${v}</option>`).join("");
  pc.insertAdjacentHTML("beforeend", `
    <div class="profile">
      <div class="portrait"><img src="${esc(p.images.cutout)}?v=${Date.now()}" alt="" /></div>
      <div class="pfields">
        <label>${t("Name")}</label><input data-f="name_th" value="${esc(prof.name_th)}" />
        <label>${t("Category")}</label><select data-f="category">${opt(CATEGORIES, prof.category)}</select>
        <label>${t("Breed")}</label><input data-f="breed" value="${esc(prof.breed)}" placeholder="${t("e.g. cattle dog (mixed)")}" />
        <label>${t("Body type")} ${conf("body_type")}</label><select data-f="body_type">${opt(BODIES, prof.body_type)}</select>
        <label title="${t("Proportions used in the turnaround prompt and every pose prompt")}">${t("Character style")}</label><select data-body>${opt(p.sheets2d_body?.options || {}, p.sheets2d_body?.value)}</select>
        <label>${t("Element")} ${conf("element")}</label><select data-f="element">${opt(ELEMENTS, prof.element)}</select>
        <label title="${t("The sprite is drawn this tall in the game; an orc or a giant is taller than a person")}">${t("Height (m)")}</label><input type="number" data-f="height_m" min="0.3" max="10" step="0.1" value="${esc(prof.height_m ?? 1.6)}" />
        <label>${t("Rank")}</label><select data-f="rank">${["S", "A", "B", "C", "D"].map((r) => `<option ${r === prof.rank ? "selected" : ""}>${r}</option>`).join("")}</select>
        <label title="${t("Starts every drawing prompt: describe hair, outfit and colours in detail; leave out weapons and proportions")}">${t("Description (English)")} ${prof.description_en ? "" : `<span class="conf low">${t("Empty")}</span>`}</label>
        <textarea data-f="description_en" rows="3" placeholder="e.g. a young swordswoman with long red hair in a braid, silver plate armour with gold trim, a red cape, brown leather boots">${esc(prof.description_en || "")}</textarea>
        <label>${t("Key features")}</label><textarea data-f="features" placeholder="${t("One per line")}">${esc((prof.features || []).join("\n"))}</textarea>
        <label>${t("Palette")}</label><div class="swatches">${(prof.palette || []).map((c) => `<span style="background:${esc(c)}" title="${esc(c)}"></span>`).join("")}</div>
      </div>
    </div>
    ${prof.notes ? `<p class="hint">${esc(prof.notes)}</p>` : ""}
    ${(p.mode || "2d") === "2d" ? `<div class="mount-form"><span>🐎 ${t("Mounted version:")}</span>
      <input data-mount placeholder="${esc(t("riding … e.g. a large brown riding bird with a saddle"))}" />
      <button class="btn small" data-mount-go title="${esc(t("Creates a new character in this project: the same character riding this mount, drawn like any character"))}">${t("Create")}</button></div>` : ""}
    <div class="char-admin">
      <label>📁 ${t("Project:")} <select data-move>${groupOptions(groups, p.group?.id)}</select></label>
      <label class="btn small" title="${esc(t("Use another image for this character: the drawn poses stay"))}">🔄 ${t("Replace the concept image")}<input type="file" accept="image/*" hidden data-concept /></label>
      <label class="check" title="${esc(t("Guess type, body and colours again from the new image (your name and description are kept)"))}"><input type="checkbox" data-reclassify /> ${t("and guess the info again")}</label>
    </div>`);
  pc.querySelector("[data-body]")?.addEventListener("change", async (e) => {
    pendingSheet = null; // a copied prompt no longer matches the chosen style
    await patch({ sprite2d: { body: e.target.value } });
    renderBoard();
    toast(t("Character style: {style} · turnaround and pose prompts updated", { style: e.target.selectedOptions[0].textContent }));
  });
  pc.querySelector("[data-move]").addEventListener("change", async (e) => {
    let gid = e.target.value;
    if (gid === "__new") gid = await askNewGroup(api, toast);
    if (!gid || gid === p.group?.id) { e.target.value = p.group?.id; return; }
    try {
      project = await api(`/api/projects/${p.slug}/move`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ group: gid }) });
      currentGroup = gid;
      toast(t("Moved to {name}", { name: groupName(groups.find((x) => x.id === gid) || { id: gid }) }));
      await loadList();
      await render();
    } catch (err) {
      toast(err.message, true);
    }
  });
  pc.querySelector("[data-concept]").addEventListener("change", async (e) => {
    const f = e.target.files[0];
    if (!f) return;
    busy(true, t("Removing the background…"));
    try {
      const form = new FormData();
      form.append("file", f);
      form.append("reclassify", pc.querySelector("[data-reclassify]").checked ? "true" : "false");
      project = await api(`/api/projects/${p.slug}/concept`, { method: "POST", body: form });
      toast(t("New concept image saved · the drawn poses are unchanged"));
      await loadList();
      await render();
    } catch (err) {
      toast(err.message, true);
    } finally {
      busy(false);
    }
  });
  pc.querySelector("[data-mount-go]")?.addEventListener("click", async () => {
    const mount = pc.querySelector("[data-mount]").value.trim();
    busy(true, t("Creating the mounted version…"));
    try {
      project = await api(`/api/projects/${project.slug}/mounted`, {
        method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ mount }),
      });
      await loadList();
      await render();
      toast(t("New character: the same character riding the mount · draw its actions in the Poses card"));
    } catch (err) {
      toast(err.message, true);
    } finally {
      busy(false);
    }
  });
  pc.querySelectorAll("[data-f]").forEach((el) => el.addEventListener("change", async () => {
    const f = el.dataset.f;
    const value = f === "features" ? el.value.split("\n").map((s) => s.trim()).filter(Boolean)
      : f === "height_m" ? Math.min(10, Math.max(0.3, Number(el.value) || 1.6)) : el.value;
    await patch({ profile: { [f]: value } });
    if (f === "height_m" && (project.mode || "2d") === "2d") await rebuild2d();
    if (f === "description_en") { pendingSheet = null; renderBoard(); toast(t("Description saved · all prompts updated")); }
  }));

  // ── 2. concept vs result ──
  const cmp = card("span-7", t("Concept vs result"), t("Compare look and colours with the original image"), { kind: "auto" });
  const pair = document.createElement("div");
  pair.className = "compare-pair";
  pair.innerHTML = `<figure><img src="${esc(p.images.cutout)}" alt="" /><figcaption>${t("concept (background removed)")}</figcaption></figure>`;
  const idleType = findType("idle");
  const mc = p.model_check;
  for (const dir of ["S", "SE", "E"]) {
    const fig = document.createElement("figure");
    if (!idleType && mc?.preview_urls?.[dir]) {
      fig.innerHTML = `<img src="${esc(mc.preview_urls[dir])}" alt="" style="width:150px" />`;
      fig.insertAdjacentHTML("beforeend", `<figcaption>${t("3D model (rest pose)")} · ${dir}</figcaption>`);
      pair.appendChild(fig);
      continue;
    }
    if (idleType) {
      const c = document.createElement("canvas");
      c.style.width = "150px";
      fig.appendChild(c);
      cells.push({ canvas: c, type: idleType, dir, mode: "play", fitHeight: true });
    } else {
      fig.innerHTML = `<div class="placeholder" style="width:150px;height:200px">${t("Not rendered yet")}</div>`;
    }
    fig.insertAdjacentHTML("beforeend", `<figcaption>render · ${dir}</figcaption>`);
    pair.appendChild(fig);
  }
  cmp.appendChild(pair);
  if ((p.mode || "2d") !== "2d") cmp.insertAdjacentHTML("beforeend", `<p class="missing-note">${t("⚠ The render is 3D work: line art and shading won't match the 2D concept")}</p>`);

  // ── 2a. views / turnaround ──
  renderViews(card, p);

  // ── 2b. 2D: AI-drawn action strips / 3D: model check ──
  if ((p.mode || "2d") === "2d") {
    renderWeapons(optional, p, { api, toast, busy, update: (np) => { project = np; render(); } });
    renderHats(optional, p, { api, toast, busy, update: (np) => { project = np; render(); } });
    renderSheets(card, p);
  }
  else renderModelCheck(card, p);

  // ── 2c. the goal: the sprite ──
  const built = p.renders?.sprite;
  const resultCard = card("span-12", t("Result + downloads"), t("8-direction sprite · single palette, crisp edges · atk / step events"),
    { kind: "req", step: "export" });
  if (built) {
    resultCard.insertAdjacentHTML("beforeend", `${is2d ? playtestCta() : ""}
      <div class="sprite-summary">
        <div>${t("<b>{types}</b> action types × 8 directions = {actions} actions", { types: built.types, actions: built.actions })}</div>
        <div>${t("<b>{images}</b> images (from {frames} frames, duplicates stored once)", { images: built.images, frames: built.frames })}</div>
        <div>${t("Height <b>{h}px</b> (classic size 82px) · palette <b>{n}</b> colours", { h: built.height_px, n: built.palette_colors })}</div>
        <div>${t("Files: .spr {spr} KB · .act {act} KB", { spr: Math.round(built.bytes.spr / 1024), act: Math.round(built.bytes.act / 1024) })}</div>
      </div>
      <table class="sprite-map"><tr><th>${t("Our action")}</th><th>${t(".act slot")}</th><th>${t("Frames")}</th><th>${t("ms/frame")}</th><th>event</th></tr>
        ${built.action_map.map((a) => `<tr><td>${esc(a.action)}</td><td>${esc(a.slot)}</td><td>${esc(a.frames)}</td><td>${a.delay_ms}</td><td>${esc(Object.entries(a.events).map(([f, e]) => `f${f} ${e}`).join(", ") || "–")}</td></tr>`).join("")}
      </table>
      <div class="btn-row">
        ${is2d ? "" : `<button class="btn small primary" data-res="lab">${t("▶ Try walk/attack in Playtest")}</button>`}
        <a class="btn small" href="/api/sprites/demo/${built.sprite_id}/demo.html" target="_blank" rel="noopener" title="${esc(t("The PixiJS demo from the web-game download, opened right here (needs internet for PixiJS)"))}">${t("▶ Web demo (PixiJS)")}</a>
        <a class="btn small primary" href="/api/sprites/download?id=${built.sprite_id}&kind=web">${t("⬇ For web games (PixiJS / Phaser)")}</a>
        <a class="btn small" href="/api/sprites/download?id=${built.sprite_id}&kind=godot" title="${esc(t("SpriteFrames .tres for an AnimatedSprite2D, with the atlas and a README"))}">${t("⬇ Godot 4")}</a>
        <a class="btn small" href="/api/sprites/download?id=${built.sprite_id}&kind=aseprite" title="${esc(t("Aseprite sprite-sheet JSON (frame tags per action and direction), read by Phaser and many engine importers"))}">${t("⬇ Aseprite JSON")}</a>
        <a class="btn small" href="/api/sprites/download?id=${built.sprite_id}&kind=zip">${t("⬇ All images (zip)")}</a>
        <button class="btn small" data-res="study" title="${esc(t("Play each action frame by frame and download single frames, strips or GIFs"))}">${t("🔍 Frame by frame / GIF")}</button>
      </div>`);
    resultCard.querySelector('[data-res="study"]').addEventListener("click", () => window.dispatchEvent(new CustomEvent("inspector:open", { detail: { id: built.sprite_id, name: p.slug } })));
    resultCard.querySelectorAll('[data-res="lab"], [data-act="playtest"]').forEach((b) => b.addEventListener("click", () =>
      window.dispatchEvent(new CustomEvent("lab:add", { detail: labSource(project) }))));
  } else {
    resultCard.insertAdjacentHTML("beforeend", (p.mode || "2d") === "2d"
      ? `<p class="hint">${t("None yet: drop the AI-drawn pose images in the poses card and the tool assembles the sprite")}</p>`
      : `<p class="hint">${t("None yet: continue from the automatic Render step (needs a rigged, animated model first)")}</p>`);
  }

  if ((p.mode || "2d") === "2d" && built) {
    renderVariants(optional, p, { api, toast, busy, update: (np) => { project = np; render(); } });
  }
  // 2D: the animation previews below only make sense once the sprite exists.
  if (is2d && !built) { layoutCells(); return; }

  // ── 3. movement 8 directions ──
  const walkType = findType("walk");
  const mv = card("span-12", t("Movement animation, 8 directions"), t("Stand / walk / stop"), { kind: "auto" });
  const g = document.createElement("div");
  g.className = "dirgrid";
  for (const dir of BOARD_ORDER) {
    const grp = document.createElement("div");
    grp.className = "dirgroup";
    grp.innerHTML = `<div class="dtitle">${DIR_LABEL[dir]}</div>`;
    const row = document.createElement("div");
    row.className = "cells";
    row.append(spriteCell(idleType, dir, "play", t("Stand")), spriteCell(walkType, dir, "play", t("Walk")), spriteCell(idleType, dir, 0, t("Stop")));
    grp.appendChild(row);
    g.appendChild(grp);
  }
  mv.appendChild(g);

  // ── 4. walk detail ──
  const wd = card("span-6", t("Walk detail"), t("Alternating legs · east (the side view shows the legs best)"), { kind: "auto" });
  const strip = document.createElement("div");
  strip.className = "strip";
  const walkE = walkType && anim.getAction(walkType, "E");
  if (walkE) {
    walkE.frames.forEach((f, i) => {
      const ev = f.event || "";
      const label = ev === "step_l" ? t("Left leg") : ev === "step_r" ? t("Right leg") : ev === "step" ? t("Step") : String(i + 1);
      strip.appendChild(spriteCell(walkType, "E", i, label, { tagClass: ev === "step_l" || ev === "step" ? "l" : ev === "step_r" ? "r" : "" }));
    });
    const rm = anim.meta?.root_motion?.[walkType];
    wd.appendChild(strip);
    const cycle = t("{n} frames × {ms}ms = {total}ms per cycle", { n: walkE.frames.length, ms: Math.round(walkE.delay_ms), total: Math.round(walkE.frames.length * walkE.delay_ms) });
    wd.insertAdjacentHTML("beforeend", `<p class="hint">${cycle}${rm ? ` · ${t("real stride speed {speed} m/s", { speed: rm.speed_m_s })}` : ""} · ${t("Left/right leg tags mark the frames where a foot touches the ground (detected from the bones)")}</p>`);
  } else {
    wd.insertAdjacentHTML("beforeend", `<div class="placeholder" style="width:100%;height:80px">${t("Needs a rendered walk")}</div>`);
  }

  // ── 5. attack 8 directions ──
  const atkType = findType("attack");
  const at = card("span-6", t("Attack animation, 8 directions"), t("Wind-up / hit (damage frame) / recovery"), { kind: "auto" });
  const rows = document.createElement("div");
  rows.className = "atkrows";
  rows.innerHTML = `<div></div><div class="head">${t("Wind-up")}</div><div class="head">${t("Hit")}</div><div class="head">${t("Recovery")}</div><div class="head">${t("Playback")}</div>`;
  const atk = atkType && anim.getAction(atkType, "S");
  const hf = atk ? hitFrame(atkType) : 0;
  for (const dir of BOARD_ORDER) {
    rows.insertAdjacentHTML("beforeend", `<div>${esc(DIR_LABEL[dir])}</div>`);
    rows.append(
      spriteCell(atkType, dir, 0, ""),
      spriteCell(atkType, dir, hf, atk ? `f${hf}` : "", { tagClass: "hit" }),
      spriteCell(atkType, dir, atk ? atk.frames.length - 1 : 0, ""),
      spriteCell(atkType, dir, "play", ""),
    );
  }
  at.appendChild(rows);
  if (atk) {
    const from = anim.meta?.lab?.hit_frame != null ? t("set in Playtest") : t("default 59%, the usual hit point");
    at.insertAdjacentHTML("beforeend", `<p class="hint">${t("Damage at frame {f} = {ms}ms of {total}ms ({from})", { f: hf, ms: Math.round(hf * atk.delay_ms), total: Math.round(atk.frames.length * atk.delay_ms), from })}</p>`);
  }

  if ((p.mode || "2d") === "2d") { layoutCells(); return; }  // 2D: the cards below are 3D-route plans

  // ── 6. extra poses ──
  const extras = (prof.animations || []).filter((a) => !["idle", "walk", "attack"].includes(a.key));
  const ex = card("span-6", t("Extra poses"), t("From the classification plan"));
  const exRow = document.createElement("div");
  exRow.className = "strip";
  for (const a of extras) exRow.appendChild(spriteCell(findType(a.key), "SW", "play", a.label_th, { big: true }));
  ex.appendChild(exRow);

  // ── 7. skill VFX ──
  const sk = card("span-6", t("Skill effects"), t("Skill plan (effects are not part of this version)"));
  const skills = prof.skills || [];
  sk.insertAdjacentHTML("beforeend", skills.length
    ? `<div class="skills">${skills.map((s, i) => `<div class="skill"><button class="rm" data-rm-skill="${i}" title="${t("Delete")}">✕</button><b>${t("Skill {n}: {name}", { n: i + 1, name: esc(s.name_th) })}</b><p>${esc(s.description_th)}</p><p>VFX: ${esc(s.vfx)}</p></div>`).join("")}</div>`
    : `<p class="hint">${t("No skill plan yet: classify with AI to get skills suggested from the element and shape, or add your own below")}</p>`);
  sk.insertAdjacentHTML("beforeend", `<div class="skill-form">
      <input placeholder="${t("Skill name")}" data-sk="name_th" /><input placeholder="${t("What it does")}" data-sk="description_th" />
      <input placeholder="${t("VFX needed")}" data-sk="vfx" /><button class="btn small" data-add-skill>${t("+ Add")}</button></div>`);
  sk.querySelector("[data-add-skill]").addEventListener("click", async () => {
    const vals = Object.fromEntries([...sk.querySelectorAll("[data-sk]")].map((el) => [el.dataset.sk, el.value.trim()]));
    if (!vals.name_th) return toast(t("Enter a skill name first"), true);
    await patch({ profile: { skills: [...skills, vals] } });
    renderBoard();
  });
  sk.querySelectorAll("[data-rm-skill]").forEach((b) => b.addEventListener("click", async () => {
    await patch({ profile: { skills: skills.filter((_, i) => i !== Number(b.dataset.rmSkill)) } });
    renderBoard();
  }));

  layoutCells();
}

/** "Walk it in Playtest before you download": shown at the download step and on the result card. */
function playtestCta() {
  return `<div class="playtest-cta">
    <div class="cta-title">🎮 ${t("Before you download: walk it in Playtest")}</div>
    <ul>
      <li>${t("Walk in all 8 directions (WASD or click the ground): do the legs alternate?")}</li>
      <li>${t("Do the feet slide on the ground? Match the game speed to the stride")}</li>
      <li>${t("Attack a dummy (Space): does the hit land on the right frame?")}</li>
    </ul>
    <button class="btn primary" data-act="playtest">${t("▶ Walk it in Playtest")}</button>
  </div>`;
}

/** Top of the 2D board: the next thing to do, and which cards are required, optional or automatic. */
function renderGuide(board, p) {
  const next = (p.steps || []).find((s) => s.next);
  const HINT = {
    classify: t("Write the English description in Character info (or ask your AI assistant) · every drawing prompt starts with it"),
    views: t("Optional: add a back or side view for better accuracy, or go straight to the AI drawings"),
    sheets: t("Copy the prompt + 🦴 pose guide, let your image AI draw, then drop the picture in the big box of the Poses card"),
    export: t("Download in the Result card, or try the character in Playtest"),
  };
  const sheets = (p.steps || []).find((s) => s.id === "sheets");
  const ready = sheets?.done && p.renders?.sprite;
  const el = document.createElement("section");
  el.className = "bcard span-12 guide-bar";
  el.innerHTML = `
    ${ready ? `<div class="guide-ready">🎮 <b>${t("Every pose is drawn: try the character in Playtest")}</b>
      <span>${t("Walk with WASD or a click, attack with Space, look at all 8 directions")}</span>
      <button class="btn small primary" data-try>${t("▶ Try in Playtest")}</button></div>` : ""}
    <div class="guide-next">${next
      ? `👉 <b>${t("Next: {title}", { title: esc(next.title) })}</b> <span>${esc(HINT[next.id] || next.detail || "")}</span>
         <button class="btn small primary" data-goto>${t("Go there ↓")}</button>`
      : `✅ <b>${t("Every step is done")}</b> <span>${t("Download in the Result card, or try the character in Playtest")}</span>`}</div>
    <div class="guide-legend">
      <span class="role req">${t("Required")}</span> ${t("upload · character info + description · AI drawings")}
      <span class="role opt">${t("Optional")}</span> ${t("reference views · weapon · headgear · colour variants (skip them if you don't need them)")}
      <span class="role auto">${t("Automatic")}</span> ${t("cutting frames, 8 directions and the sprite: the tool does them")}
    </div>`;
  board.appendChild(el);
  el.querySelector("[data-try]")?.addEventListener("click", () =>
    window.dispatchEvent(new CustomEvent("lab:add", { detail: labSource(p) })));
  el.querySelector("[data-goto]")?.addEventListener("click", () =>
    board.querySelector(".next-card")?.scrollIntoView({ behavior: "smooth", block: "start" }));
}

function renderViews(card, p) {
  const views = p.views || {};
  const missing = REQUIRED_VIEWS.filter((v) => !views[v]);
  const el = card("span-12", t("Character views (turnaround)"),
    missing.length ? t("Missing {views} · optional: a back view helps the AI draw the back 3/4 rows more accurately", { views: missing.map((v) => VIEW_LABEL[v]).join(", ") }) : t("Complete ✓"),
    { kind: "opt", step: "views", open: !(p.views || {}).front });
  el.insertAdjacentHTML("beforeend", `
    <div class="views-row four">
      ${VIEWS.map((v) => `
        <div class="view-slot ${views[v] ? "" : "empty"} ${REQUIRED_VIEWS.includes(v) ? "req" : ""}">
          ${views[v] ? `<img src="${esc(views[v])}" alt="" />` : `<label class="view-drop" data-view-drop="${v}"><input type="file" accept="image/*" hidden />${t("No view yet")}<br><b>${VIEW_LABEL[v]}</b>${REQUIRED_VIEWS.includes(v) ? "" : `<br><small>${t("(optional)")}</small>`}<br><small class="drop-here">⬇ ${t("drop or click")}</small></label>`}
          <div class="view-bar">
            <b>${VIEW_LABEL[v]}</b>
            ${views[v] ? `<select data-swap="${v}" title="${t("Wrong view? Swap it")}"><option value="">${t("Swap with…")}</option>${VIEWS.filter((o) => o !== v).map((o) => `<option value="${o}">${VIEW_LABEL[o]}</option>`).join("")}</select>
              <button class="rm flip" data-flip-view="${v}" title="${t("Flip left-right")}${v === "side" ? " " + t("(the side view must face image-left)") : ""}">↔</button>
              <button class="rm" data-rm-view="${v}" title="${t("Delete")}">✕</button>` : ""}
          </div>
        </div>`).join("")}
    </div>
    <label class="dropmini views-drop" data-views-drop><input type="file" accept="image/*" multiple hidden />
      ⬇ ${t("Drop a back or side view, or a turnaround sheet, here: the tool sorts it into views")}</label>
    ${missing.length ? `<div class="prompt-box" data-prompt-box></div>` : ""}`);
  if (missing.length) renderPromptBox(el.querySelector("[data-prompt-box]"), p);
  const bindDrop = (zone, view) => {
    const take = (files) => handleFiles([...files], view, { allowModels: false });
    zone.querySelector("input").addEventListener("change", (e) => take(e.target.files));
    zone.addEventListener("dragover", (e) => { e.preventDefault(); zone.classList.add("drag"); });
    zone.addEventListener("dragleave", () => zone.classList.remove("drag"));
    zone.addEventListener("drop", (e) => { e.preventDefault(); e.stopPropagation(); zone.classList.remove("drag"); take(e.dataTransfer.files); });
  };
  bindDrop(el.querySelector("[data-views-drop]"), null);
  el.querySelectorAll("[data-view-drop]").forEach((z) => bindDrop(z, z.dataset.viewDrop));

  el.querySelectorAll("[data-swap]").forEach((sel) => sel.addEventListener("change", async () => {
    if (!sel.value) return;
    project = await api(`/api/projects/${project.slug}/views-swap?a=${sel.dataset.swap}&b=${sel.value}`, { method: "POST" });
    await render();
  }));
  el.querySelectorAll("[data-flip-view]").forEach((b) => b.addEventListener("click", async () => {
    project = await api(`/api/projects/${project.slug}/views-flip?view=${b.dataset.flipView}`, { method: "POST" });
    await render();
  }));
  if (views.side) el.insertAdjacentHTML("beforeend", `<p class="hint">${t("The side view must face <b>image-left</b> (that's how the 3D model reads it); if it faces right, press ↔")}</p>`);
  el.querySelectorAll("[data-rm-view]").forEach((b) => b.addEventListener("click", async () => {
    project = await api(`/api/projects/${project.slug}/views/${b.dataset.rmView}`, { method: "DELETE" });
    await render();
  }));

}

/** Which body style the prompts use; it is chosen in the character-info card. */
function bodyNote(p) {
  const style = p.sheets2d_body?.options?.[p.sheets2d_body.value] || "";
  return `<span class="hint">${t("Character style: <b>{style}</b> (change it in the character info card)", { style: esc(style) })}</span>`;
}

function renderPromptBox(box, p) {
  const tgt = PROMPT_TARGETS[promptTarget];
  const text = tgt.build ? tgt.build(p.turnaround_prompt || "") : "";
  box.innerHTML = `
    <div class="hint">${t("Views missing: make a turnaround image with an image AI, then drop the result at <b>step 1</b>; the tool splits the views and builds the character")}${p.profile._method === "claude_code" && p.profile.turnaround_prompt ? " · " + t("Character details written by your AI assistant") : ""}</div>
    <div class="sheet-bar">${bodyNote(p)}</div>
    <div class="seg small" role="tablist">${Object.entries(PROMPT_TARGETS).map(([k, v]) =>
      `<button data-target="${k}" class="${k === promptTarget ? "active" : ""}">${v.label}</button>`).join("")}</div>
    <p class="hint">${esc(tgt.how)}</p>
    ${tgt.build ? `<pre>${esc(text)}</pre>
      <div class="btn-row">
        <button class="btn small primary" data-copy-prompt>${t("📋 Copy prompt for {ai}", { ai: esc(tgt.label) })}</button>
        ${tgt.url ? `<a class="btn small" href="${tgt.url}" target="_blank" rel="noopener">${t("Open {ai} ↗", { ai: esc(tgt.label) })}</a>` : ""}
        <a class="btn small" href="${esc(p.images.cutout)}" download="${esc(p.slug)}_front.png">${t("⬇ Front image (attach as reference)")}</a>
      </div>` : ""}`;
  box.querySelectorAll("[data-target]").forEach((b) => b.addEventListener("click", () => {
    promptTarget = b.dataset.target;
    rememberTarget(promptTarget);
    renderPromptBox(box, p);
  }));
  box.querySelector("[data-copy-prompt]")?.addEventListener("click", async () => {
    await navigator.clipboard.writeText(text);
    toast(t("Copied the prompt for {ai}", { ai: tgt.label }));
  });
}

/** Download link for the stick-figure pose guide that matches a prompt (attach it next to the character). */
function guideLink(p, action, view, label) {
  const q = action === "all" ? "" : `?action=${action}&view=${view}`;
  const name = `${p.slug}_pose_${action}${view === "both" ? "" : "_" + view}.png`;
  return `<a class="btn small" href="/api/projects/${encodeURIComponent(p.slug)}/pose-guide${q}" download="${esc(name)}"
    title="${t("Pose guide: attach it with the character image so the AI follows the pose (blue = left, red = right)")}">${label}</a>`;
}

/** The same prompt's character + pose guide as ONE image (attach it instead of two). */
function boardLink(p, action, view, label) {
  const q = action === "all" ? "" : `?action=${action}&view=${view}`;
  const name = `${p.slug}_board_${action}${view === "both" ? "" : "_" + view}.png`;
  return `<a class="btn small" href="/api/projects/${encodeURIComponent(p.slug)}/reference-board${q}" download="${esc(name)}"
    title="${esc(t("Character + pose guide in one image: attach just this one with the prompt"))}">${label}</a>`;
}

/** What's wrong with one AI-drawn strip, in Thai (older reports have no `issues`). */
function checkIssues(c) {
  return c.issues?.length ? c.issues.join(" · ") : `${c.frames}/${c.expected} · ${c.height_spread_pct}%`;
}

/** Drop zone of the sheets step: AI-drawn action images. */
async function uploadSheets(fileList) {
  const images = [...(fileList || [])].filter((f) => f.type.startsWith("image/") || IMAGE_EXT.test(f.name));
  if (!images.length) return toast(t("Only image files can be dropped here"), true);
  for (const img of images) {
    const slot = pendingSheet || { action: "all", view: "both", label: t("All actions") };
    busy(true, t("{label}: removing background, cutting the grid, building the sprite…", { label: slot.label }));
    try {
      const form = new FormData();
      form.append("file", img);
      project = await api(`/api/projects/${project.slug}/sheets/${slot.action}/${slot.view}`, { method: "POST", body: form });
      pendingSheet = null;
      const rows = slot.action === "all" ? Object.values(project.sheets2d || {}) : [project.sheets2d?.[slot.action]];
      const checks = rows.flatMap((row) => (slot.view === "both" ? Object.values(row?.views || {}) : [row?.views?.[slot.view]])
        .filter((c) => c?.check).map((c) => ({ th: `${row.th} ${c.th}`, ...c.check })));
      const bad = checks.filter((c) => !c.ok);
      toast(!checks.length ? t("{label} saved", { label: slot.label })
        : slot.action === "all"
          ? t("Cut {n} rows", { n: checks.length }) + (bad.length ? ` · ⚠ ${bad.map((c) => `${c.th}: ${checkIssues(c)}`).join(", ")} · ${t("you can redraw just those actions")}` : " " + t("✓ All actions complete"))
          : checks.map((c) => `${c.th} ${c.ok ? t("{n} frames ✓", { n: c.frames }) : `⚠ ${checkIssues(c)}`}`).join(" · ") + (bad.length ? " · " + t("should be redrawn") : ""),
        bad.length > 0);
    } catch (err) {
      toast(err.message, true);
    } finally {
      busy(false);
    }
  }
  await render();
}

/** One entry point for every other drop zone: images become views, 3D files become model files. */
async function handleFiles(fileList, targetView = null, { allowImages = true, allowModels = true } = {}) {
  const files = [...(fileList || [])];
  if (!files.length) return;
  const models = files.filter((f) => MODEL_EXT.test(f.name));
  const images = files.filter((f) => !MODEL_EXT.test(f.name) && (f.type.startsWith("image/") || IMAGE_EXT.test(f.name)));
  const other = files.filter((f) => !models.includes(f) && !images.includes(f));
  if (other.length) toast(t("Unknown file type: {names}", { names: other.map((f) => f.name).join(", ") }), true);
  if (images.length && !allowImages) {
    toast(t("Drop character images at step 1 and AI-drawn pose images under the prompt in the AI drawings card"), true);
    images.length = 0;
  }
  if (models.length && !allowModels) {
    toast(t('Drop 3D model files at the "Create 3D model" step'), true);
    models.length = 0;
  }
  if (models.length) await uploadModels(models);
  for (const img of images) {
    busy(true, t("Removing background from {name}…", { name: img.name }));
    try {
      const form = new FormData();
      form.append("file", img);
      if (targetView) {
        project = await api(`/api/projects/${project.slug}/views/${targetView}`, { method: "POST", body: form });
        toast(t("Saved as the {view} view", { view: VIEW_LABEL[targetView] }));
      } else {
        const res = await api(`/api/projects/${project.slug}/views-split`, { method: "POST", body: form });
        let assignment;
        if (res.pieces.length > 1) {
          assignment = Object.fromEntries(res.assignment.map((v, i) => [i, v]));
          toast(t("Split into {n} views: {views} (swap them if wrong)", { n: res.pieces.length, views: res.assignment.map((v) => VIEW_LABEL[v] || v).join(", ") }));
        } else {
          const views = project.views || {};
          const slot = REQUIRED_VIEWS.find((v) => !views[v]) || VIEWS.find((v) => !views[v]) || "front_3q";
          assignment = { 0: slot };
          toast(t('Saved as the {view} view (if wrong, use "Swap with…")', { view: VIEW_LABEL[slot] }));
        }
        project = await api(`/api/projects/${project.slug}/views-assign`, {
          method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(assignment),
        });
      }
    } catch (err) {
      toast(err.message, true);
    } finally {
      busy(false);
    }
  }
  await render();
}

/** Rebuild the 2D sprite after a setting changed (nothing to do before any action is drawn). */
async function rebuild2d() {
  if (!Object.values(project.sheets2d || {}).some((row) => Object.values(row.views || {}).some((c) => c.url))) return;
  busy(true, t("Rebuilding sprite…"));
  try {
    project = await api(`/api/projects/${project.slug}/build2d`, { method: "POST" });
    await render();
  } catch (err) {
    toast(err.message, true);
  } finally {
    busy(false);
  }
}

function renderSheets(card, p) {
  const sheets = p.sheets2d || {};
  const total = Object.values(sheets).reduce((n, r) => n + Object.keys(r.views).length, 0);
  const have = Object.values(sheets).reduce((n, r) => n + Object.values(r.views).filter((c) => c.url).length, 0);
  const el = card("span-12", `${t("Poses (AI-drawn)")} · ${have}/${total}`,
    t("📋 Copy prompt + 🦴 pose guide → let the AI draw → drop the result in the box below · the tool cuts the frames, makes 8 directions and builds the sprite · per-action/per-view buttons are for repairs"),
    { kind: "req", step: "sheets" });
  if (have && have === total && p.renders?.sprite) {
    el.insertAdjacentHTML("beforeend", `<div class="guide-ready">✅ <b>${t("Every pose is drawn: try the character in Playtest")}</b>
      <button class="btn small primary" data-try>${t("▶ Try in Playtest")}</button></div>`);
    el.querySelector("[data-try]").addEventListener("click", () =>
      window.dispatchEvent(new CustomEvent("lab:add", { detail: labSource(p) })));
  }
  const tgt = PROMPT_TARGETS[promptTarget].build ? promptTarget : "gemini";
  el.insertAdjacentHTML("beforeend", `
    <div class="sheet-bar">
      <button class="btn small primary" data-copy-sheet="all|both" title="${t("One image, {n} rows: every action, front 3/4 + back 3/4", { n: Object.keys(sheets).length * 2 })}">${t("📋 All actions (one prompt)")}</button>
      ${guideLink(p, "all", "both", t("🦴 Pose guide"))}${boardLink(p, "all", "both", t("🧩 1 image (character + poses)"))}
      <label class="check" title="${t("Add magic attack (spellcasting) rows, front 3/4 + back 3/4, to the all-actions grid")}"><input type="checkbox" data-magic ${p.sheets2d_magic ? "checked" : ""} /> ${t("Has a magic attack")}</label>
      <label class="check" title="${t("Adds a side-view row per action: west/east then show a true profile instead of the front 3/4 drawing")}"><input type="checkbox" data-side ${p.sheets2d_side ? "checked" : ""} /> ${t("Side view (W/E)")}</label>
      <span class="hint" title="${t("Extra actions get their own rows in the all-actions grid, prompts and pose guides")}">${t("Extra actions:")}</span>
      ${Object.entries(p.sheets2d_extras?.options || {}).map(([k, th]) =>
        `<label class="check"><input type="checkbox" data-extra="${k}" ${(p.sheets2d_extras.chosen || []).includes(k) ? "checked" : ""} /> ${esc(th)}</label>`).join("")}
      <label class="check" title="${t("More pixels keep painted detail sharp; files get bigger. Classic matches old 2D online games.")}">${t("Sprite detail")} <select data-detail>${Object.entries(p.sheets2d_detail?.options || {}).map(([k, label]) =>
        `<option value="${k}" ${Number(k) === p.sheets2d_detail.value ? "selected" : ""}>${esc(label)}</option>`).join("")}</select></label>
      <label class="check" title="${t("Pull every row's colours back to the reference image, so rows drawn at different times don't flicker")}"><input type="checkbox" data-colour-lock ${p.sheets2d_colour_lock ? "checked" : ""} /> ${t("🎨 Lock colours to the reference")}</label>
      <span class="hint">${t("Prompt for")}</span>
      <div class="seg small">${["gemini", "chatgpt", "generic"].map((k) =>
        `<button data-sheet-target="${k}" class="${k === tgt ? "active" : ""}">${PROMPT_TARGETS[k].label}</button>`).join("")}</div>
      ${PROMPT_TARGETS[tgt].url ? `<a class="btn small conn-go ${IMAGE_AIS[tgt]?.cls || ""}" href="${PROMPT_TARGETS[tgt].url}" target="_blank" rel="noopener">${t("Open {ai} ↗", { ai: esc(PROMPT_TARGETS[tgt].label) })}</a>` : ""}
      ${bodyNote(p)}
      <span class="hint">${t('Attach 2 images: the character (turnaround sheet or <a href="{href}" download="{file}">⬇ front image</a>) + 🦴 the pose guide for that prompt', { href: esc(p.images.cutout), file: esc(`${p.slug}_front.png`) })}</span>
    </div>
    <label class="dropmini sheet-drop" data-sheet-drop><input type="file" accept="image/*" multiple hidden />
      ⬇ ${pendingSheet ? t("Drop the <b>{label}</b> image here", { label: esc(pendingSheet.label) }) : t("Drop the AI-drawn image here (all-actions grid)")}</label>
    ${pendingSheet ? `<span class="pending">⏳ ${t("Next image = <b>{label}</b>", { label: esc(pendingSheet.label) })} <button class="rm" data-cancel-pending>✕</button></span>` : ""}
    ${pendingSheet && !pendingSheet.copied ? `<textarea class="pending-text" data-pending-text readonly rows="6">${esc(pendingSheet.text)}</textarea>` : ""}
    <div class="sheet-grid">
      <div class="head"></div>${Object.values(Object.values(sheets)[0]?.views || {}).map((c) => `<div class="head">${esc(c.th)}</div>`).join("")}
      ${Object.entries(sheets).map(([action, row]) => `
        <div class="rowlabel ${pendingSheet && pendingSheet.action === action && pendingSheet.view === "both" ? "pending" : ""}"><b>${esc(row.th)}</b><small>${t("{n} frames", { n: row.frames })}</small>
          <button class="btn small" data-copy-sheet="${action}|both" title="${t("One image, 2 rows: front 3/4 on top, back 3/4 below")}">${t("📋 Both views")}</button>${guideLink(p, action, "both", "🦴")}${boardLink(p, action, "both", "🧩")}</div>
        ${Object.entries(row.views).map(([view, c]) => {
          const chk = c.check;
          const badge = !c.url ? "" : chk && chk.ok === false
            ? `<span class="warnb" title="${t("Got {n}/{want} frames · height varies {pct}%", { n: chk.frames, want: chk.expected, pct: chk.height_spread_pct })}${chk.stride_var != null ? " · " + t("feet open-close {v} (should be ≥ 0.6)", { v: chk.stride_var }) : ""}">⚠ ${esc(checkIssues(chk))}</span>`
            : `<span class="okb">✓ ${t("{n} frames", { n: chk ? chk.frames : "?" })}</span>`;
          const rv = c.review;
          const review = rv ? `<span class="${rv.verdict === "ok" ? "okb" : "warnb"}" title="${esc(rv.notes || "")}">🎨 ${rv.verdict === "ok" ? t("Art review: OK") : rv.verdict === "redraw" ? t("Art review: redraw") : t("Art review: fix")}</span>` : "";
          const turned = c.turned?.length
            ? `<span class="hint" title="${t("The AI drew these frames facing the other way; the tool flipped them to match")}">↔ ${t("Frames {list}", { list: c.turned.join(", ") })}</span>` : "";
          return `<div class="sheet-cell ${c.url ? "has" : ""} ${pendingSheet && pendingSheet.action === action && pendingSheet.view === view ? "pending" : ""}">
            ${c.url ? `<img src="${esc(c.url)}" alt="" />` : `<div class="sheet-empty">${t("None yet")}</div>`}
            <div class="sheet-actions">${badge}${review}${turned}
              <button class="btn small" data-copy-sheet="${action}|${view}">📋 prompt</button>${guideLink(p, action, view, "🦴")}
              ${c.url ? `<button class="btn small" data-flip-sheet="${action}|${view}" title="${t("Flip every frame (same frame order): use when the whole row faces the wrong way")} ${view === "front" ? t("front 3/4 must face lower-left") : t("back 3/4 must face upper-left")}">↔</button>
                <button class="btn small ${c.edited ? "primary" : ""}" data-edit-frames="${action}|${view}" title="${t("Edit frames: reorder, delete, flip, nudge, pause, replace one frame")}">✎${c.edited ? " " + t("edited") : ""}</button>
                ${c.history ? `<button class="btn small" data-undo-sheet="${action}|${view}" title="${t("Bring back the previous drawing of this row")}">↶</button>` : ""}
                <button class="rm" data-rm-sheet="${action}|${view}" title="${t("Delete")}">✕</button>` : ""}
            </div></div>`;
        }).join("")}`).join("")}
    </div>
    <div class="frame-editor" data-frame-editor></div>
    <div class="review-bar">
      <button class="btn small" data-review title="${t("Your AI assistant looks at every drawn row and writes what to fix (copy this into its chat)")}">🎨 ${t("Ask your AI assistant for an art review")}</button>
      ${p.art_review_summary?.date ? `<span class="hint">${t("Last review {date}:", { date: esc(p.art_review_summary.date.slice(0, 16).replace("T", " ")) })} ${esc(p.art_review_summary.summary || "")}</span>` : ""}
    </div>
    <p class="hint">${t("The tool counts the frames and measures the character's height in each frame. If ⚠ shows (wrong frame count or size varies more than 12%), ask the AI to redraw")}</p>`);
  el.querySelectorAll("[data-sheet-target]").forEach((b) => b.addEventListener("click", () => { promptTarget = b.dataset.sheetTarget; rememberTarget(promptTarget); renderBoard(); }));
  el.querySelector("[data-magic]")?.addEventListener("change", async (e) => {
    pendingSheet = null;    // the all-actions prompt now has a different number of rows
    await patch({ sprite2d: { magic: e.target.checked } });
    renderBoard();
    toast(e.target.checked ? t("Magic attack added: the all-actions grid has 12 rows") : t("No magic attack: the all-actions grid has 10 rows"));
  });
  el.querySelector("[data-side]")?.addEventListener("change", async (e) => {
    pendingSheet = null;    // every prompt now has a side row per action
    await patch({ sprite2d: { side: e.target.checked } });
    renderBoard();
    toast(e.target.checked ? t("Side view on: each action has 3 rows (front 3/4, side, back 3/4)") : t("Side view off: west/east use the front 3/4 drawing"));
  });
  el.querySelectorAll("[data-extra]").forEach((box) => box.addEventListener("change", async () => {
    pendingSheet = null;    // the all-actions prompt now has a different number of rows
    const extras = [...el.querySelectorAll("[data-extra]")].filter((x) => x.checked).map((x) => x.dataset.extra);
    await patch({ sprite2d: { extras } });
    renderBoard();
    toast(t("The all-actions grid now has {n} rows", { n: Object.keys(project.sheets2d || {}).length * 2 }));
  }));
  el.querySelector("[data-colour-lock]")?.addEventListener("change", async (e) => {
    await patch({ sprite2d: { colour_lock: e.target.checked } });
    await rebuild2d();
  });
  el.querySelector("[data-detail]")?.addEventListener("change", async (e) => {
    await patch({ sprite2d: { detail: Number(e.target.value) } });
    await rebuild2d();
  });
  const drop = el.querySelector("[data-sheet-drop]");
  drop.querySelector("input").addEventListener("change", (e) => uploadSheets(e.target.files));
  drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("drag"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("drag"));
  drop.addEventListener("drop", (e) => { e.preventDefault(); e.stopPropagation(); drop.classList.remove("drag"); uploadSheets(e.dataTransfer.files); });
  el.querySelector("[data-cancel-pending]")?.addEventListener("click", () => { pendingSheet = null; renderBoard(); renderSteps(); });
  el.querySelectorAll("[data-copy-sheet]").forEach((b) => b.addEventListener("click", async () => {
    const [action, view] = b.dataset.copySheet.split("|");
    const all = action === "all";
    const cell = all ? { prompt: p.sheets2d_prompt_all, th: t("All actions") }
      : view === "both" ? { prompt: sheets[action].prompt_both, th: t("Both views") } : sheets[action].views[view];
    const wrap = SHEET_WRAP[tgt] || SHEET_WRAP.generic;
    const text = wrap(cell.prompt);
    pendingSheet = { action, view, label: all ? cell.th : `${sheets[action].th} · ${cell.th}`, text };
    try {
      await navigator.clipboard.writeText(text);
      pendingSheet.copied = true;
      toast(t('Copied the "{label}" prompt · drop the result in the box under the prompt', { label: pendingSheet.label }));
    } catch {
      toast(t("Couldn't copy automatically: select the text in the box and press Ctrl+C"), true);
    }
    renderBoard();
    renderSteps();
    if (!pendingSheet.copied) document.querySelector("[data-pending-text]")?.select();
  }));
  el.querySelectorAll("[data-flip-sheet]").forEach((b) => b.addEventListener("click", async () => {
    const [action, view] = b.dataset.flipSheet.split("|");
    busy(true, t("Flipping every frame…"));
    try {
      project = await api(`/api/projects/${project.slug}/sheets/${action}/${view}/flip`, { method: "POST" });
    } catch (err) {
      toast(err.message, true);
    } finally {
      busy(false);
    }
    await render();
  }));
  el.querySelector("[data-review]")?.addEventListener("click", async () => {
    const text = t("Review character {slug}", { slug: p.slug });
    try {
      await navigator.clipboard.writeText(text);
      toast(t("Copied: paste it into your AI assistant's chat, then refresh when it's done"));
    } catch {
      toast(text);
    }
  });
  const feHost = el.querySelector("[data-frame-editor]");
  const feDeps = {
    api, toast, busy,
    update: (np, opts) => { project = np; if (opts?.keepEditor) frameEdit = opts.keepEditor; render(); },
    onClose: () => { frameEdit = null; },
  };
  if (frameEdit && sheets[frameEdit.action]?.views[frameEdit.view]?.url) openFrameEditor(feHost, p, frameEdit.action, frameEdit.view, feDeps);
  el.querySelectorAll("[data-edit-frames]").forEach((b) => b.addEventListener("click", () => {
    const [action, view] = b.dataset.editFrames.split("|");
    frameEdit = { action, view };
    openFrameEditor(feHost, p, action, view, feDeps);
    feHost.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }));
  el.querySelectorAll("[data-undo-sheet]").forEach((b) => b.addEventListener("click", async () => {
    const [action, view] = b.dataset.undoSheet.split("|");
    busy(true, t("Rebuilding sprite…"));
    try {
      project = await api(`/api/projects/${project.slug}/sheets/${action}/${view}/undo`, { method: "POST" });
      toast(t("Previous drawing restored"));
    } catch (err) {
      toast(err.message, true);
    } finally {
      busy(false);
    }
    await render();
  }));
  el.querySelectorAll("[data-rm-sheet]").forEach((b) => b.addEventListener("click", async () => {
    const [action, view] = b.dataset.rmSheet.split("|");
    project = await api(`/api/projects/${project.slug}/sheets/${action}/${view}`, { method: "DELETE" });
    await render();
  }));
}

function renderModelCheck(card, p) {
  const mc = p.model_check;
  const running = p.jobs?.inspect?.state === "running";
  const el = card("span-12", t("3D model check"), t("Images of the real model, from the same camera as the sprites · check before rigging"));
  if (p.jobs?.gen3d?.state === "running") {
    el.insertAdjacentHTML("beforeend", `<p class="hint">${t("⏳ Building the 3D model locally from the {views} images (Hunyuan3D-2mv → colour projection). It takes a few minutes; the model check starts on its own when done", { views: Object.keys(p.views || {}).map((v) => VIEW_LABEL[v]).join(" / ") })}</p>
      <div class="joblog">${esc((p.jobs.gen3d.tail || []).join("\n"))}</div>`);
    return;
  }
  if (!p.model_files?.length) {
    const failed = p.jobs?.gen3d?.state === "failed";
    el.insertAdjacentHTML("beforeend", p.gen3d_available
      ? `<p class="hint">${failed ? t("❌ Model creation failed: see the log in the Create 3D model step") : t("No model yet: it is built locally once the front / side / back images are in (or build it from the images you have in the Create 3D model step)")}</p>`
      : `<p class="hint">${t("No model yet: upload a model file in the Create 3D model step and the tool will shoot 8 directions to check here")}</p>`);
    return;
  }
  if (running || !mc) {
    el.insertAdjacentHTML("beforeend", `<p class="hint">${running ? t("⏳ Shooting the model in 8 directions with Blender…") : t("Not checked yet")}</p>
      ${running ? "" : `<button class="btn small" data-mc="rerun">${t("Check model")}</button>`}
      ${p.jobs?.inspect?.state === "failed" ? `<div class="joblog">${esc((p.jobs.inspect.tail || []).join("\n"))}</div>` : ""}`);
    bindModelCheck(el);
    return;
  }
  const icon = { ok: "✅", warn: "⚠️", bad: "❌", info: "ℹ️", check: "👁" };
  const r = p.render || {};
  const views = p.views || {};
  const pairs = VIEWS.filter((v) => views[v]).map((v) => `
    <div class="mc-pair"><figure><img src="${esc(views[v])}" alt="" /><figcaption>${t("{view} image", { view: VIEW_LABEL[v] })}</figcaption></figure>
      ${VIEW_TO_DIRS[v].map((d) => `<figure><img src="${esc(mc.preview_urls[d])}" alt="" /><figcaption>${t("Model {dir}", { dir: d })}</figcaption></figure>`).join("")}</div>`).join("");
  if (pairs) el.insertAdjacentHTML("beforeend", `<div class="hint">${t("View by view: drawing ↔ real model in the same direction")}</div><div class="mc-pairs">${pairs}</div>`);
  el.insertAdjacentHTML("beforeend", `
    <div class="mc-grid">
      <figure class="mc-concept"><img src="${esc(p.images.cutout)}" alt="" /><figcaption>concept</figcaption></figure>
      <div class="mc-views">${BOARD_ORDER.map((d) => `<figure><img src="${esc(mc.preview_urls[d])}" alt="" /><figcaption>${d}</figcaption></figure>`).join("")}</div>
    </div>
    <ul class="mc-checks">${mc.checks.map((c) => `<li class="${c.level}">${icon[c.level] || ""} ${esc(c.text)}</li>`).join("")}</ul>
    <div class="mc-controls">
      <label class="field inline">${t("Model faces")}
        <select data-mc="facing">${["-Y", "+Y", "+X", "-X"].map((f) => `<option ${f === (r.facing || "-Y") ? "selected" : ""}>${f}</option>`).join("")}</select>
      </label>
      <label class="check"><input type="checkbox" data-mc="normalize" ${r.normalize_height !== false ? "checked" : ""} /> ${t("Scale height to {h} m from the profile", { h: esc(p.profile.height_m ?? "?") })}</label>
      <button class="btn small" data-mc="rerun">${t("⟳ Check again")}</button>
      <button class="btn small ${mc.confirmed ? "" : "primary"}" data-mc="ok">${mc.confirmed ? t("✓ Confirmed") : t("✓ Model is correct")}</button>
    </div>`);
  bindModelCheck(el);
}

function bindModelCheck(el) {
  el.querySelectorAll("[data-mc]").forEach((ctl) => {
    const kind = ctl.dataset.mc;
    const ev = ctl.tagName === "BUTTON" ? "click" : "change";
    ctl.addEventListener(ev, async () => {
      try {
        if (kind === "ok") {
          await patch({ notes: { model_ok: true } });
          if (project.jobs?.render?.state === "running") toast(t("The model is rigged and animated: the 8-direction render started automatically"));
          renderBoard();
          schedulePoll();
          return;
        }
        if (kind === "facing") await patch({ render: { facing: ctl.value }, notes: { model_ok: false } });
        if (kind === "normalize") await patch({ render: { normalize_height: ctl.checked }, notes: { model_ok: false } });
        // Any setting change or explicit re-run -> new stills.
        await api(`/api/projects/${project.slug}/inspect`, { method: "POST" });
        project = await api(`/api/projects/${project.slug}`);
        renderBoard();
        renderSteps();
        schedulePoll();
      } catch (err) {
        toast(err.message, true);
      }
    });
  });
}

// Fit every cell's sprite into its canvas with one scale per character (sizes stay comparable).
function layoutCells() {
  const dpr = window.devicePixelRatio || 1;
  for (const c of cells) {
    const r = c.canvas.getBoundingClientRect();
    const w = r.width || 72;
    const h = c.fitHeight ? 200 : r.height || 72;
    c.canvas.style.height = `${h}px`;
    c.canvas.width = Math.round(w * dpr);
    c.canvas.height = Math.round(h * dpr);
    c.w = w;
    c.h = h;
  }
}

let t0 = performance.now();
function tick(now) {
  requestAnimationFrame(tick);
  if (activePage() !== "pipeline" || !anim || !cells.length) return;
  const t = now - t0;
  const b = setBounds(anim, anim.action_types);
  const dpr = window.devicePixelRatio || 1;
  for (const c of cells) {
    if (!c.w) continue;
    const ctx = c.canvas.getContext("2d");
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, c.canvas.width, c.canvas.height);
    const a = anim.getAction(c.type, c.dir);
    if (!a) continue;
    const s = Math.min((c.w - 6) / (b.x1 - b.x0), (c.h - 6) / (b.y1 - b.y0));
    const ox = c.w / 2 - ((b.x0 + b.x1) / 2) * s;
    const oy = c.h / 2 - ((b.y0 + b.y1) / 2) * s;
    const idx = c.mode === "play" ? frameIndexAt(a, t, true) : Math.min(c.mode, a.frames.length - 1);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = "rgba(0,0,0,0.3)";
    ctx.beginPath();
    const sw = (b.x1 - b.x0) * 0.28 * s;
    ctx.ellipse(ox, oy, sw, sw * 0.45, 0, 0, Math.PI * 2);
    ctx.fill();
    drawFrame(ctx, anim, a.frames[idx], ox, oy, { scale: s, smoothing: true });
  }
}
requestAnimationFrame(tick);
window.addEventListener("resize", () => cells.length && layoutCells());

// ───────────────────────── steps panel ─────────────────────────

function stepBody(s) {
  const p = project;
  const prof = p.profile;
  const humanoid = prof.body_type === "humanoid";
  const files = p.model_files || [];
  const fileList = files.length
    ? `<ul class="filelist">${files.map((f) => `<li><span>${esc(f.name)} <small>(${f.kind}, ${(f.size / 1048576).toFixed(1)} MB)</small></span><button data-del="${esc(f.name)}" title="${t("Delete")}">✕</button></li>`).join("")}</ul>`
    : "";
  const dropMini = (hint, kind = "models") => `<label class="dropmini" data-upload="${kind}"><input type="file" multiple accept="${kind !== "models" ? "image/*" : ".fbx,.glb,.gltf,.blend,.obj"}" hidden />${hint}</label>`;

  switch (s.id) {
    case "upload":
      return `<p>${t("Done: the character image is in. New characters start in the box on the left; to use another image for this one, press 🔄 Replace the concept image in the Character info card")}</p>
        <p><a href="${esc(p.images.cutout)}" download="${esc(p.slug)}_front.png">${t("⬇ Download the front view (background removed)")}</a></p>`;
    case "classify":
      return p.ai_pending
        ? `<p>${t("Waiting for your AI assistant: paste this text into its chat (Claude Code, Gemini CLI or Codex), then press refresh when it's done")}</p>
          <p><code>${t("Classify character {slug}", { slug: esc(p.slug) })}</code></p>
          <div class="btn-row">
            <button class="btn small" data-act="copyai">${t("📋 Copy text")}</button>
            <button class="btn small" data-act="refresh">${t("⟳ Refresh")}</button>
            <button class="btn small" data-act="manual">${t("✍ Fill in by hand instead")}</button>
          </div>`
        : `<p>${t("Fill in / check the <b>Character info</b> card: the <b>Description (English)</b> matters most because it starts every drawing prompt, then the <b>Character style</b> (slender / chibi), then confirm")}</p>
          <div class="btn-row">
            <button class="btn small" data-act="confirm">${t("✓ Confirm info")}</button>
          </div>`;
    case "model3d": {
      const job = p.jobs?.gen3d;
      const local = p.gen3d_available
        ? `<p>${t("<b>Build locally</b> with Hunyuan3D-2mv from the images in the views card (starts on its own once front / side / back are in), then project colour from the same images onto the model")}</p>
          <div class="btn-row"><button class="btn small" data-act="gen3d" ${job?.state === "running" ? "disabled" : ""}>${files.length ? t("⟳ Rebuild model") : t("▶ Build model from current images")}</button></div>
          ${job ? `<div class="joblog">${esc(job.state)}${job.elapsed != null ? ` · ${job.elapsed}s` : ""}\n${esc((job.tail || []).join("\n"))}</div>` : ""}`
        : `<p>${t("No local 3D generator installed")}</p>`;
      return `${local}
        <p class="hint">${t("Or use a model from elsewhere (e.g. an already rigged file): drop it here")}</p>
        ${dropMini(t("Drop a .fbx / .glb / .blend model file here"))}${fileList}`;
    }
    case "views": {
      const missing = REQUIRED_VIEWS.filter((v) => !(p.views || {})[v]);
      return missing.length
        ? `<p>${t("Missing views: <b>{views}</b>", { views: missing.map((v) => VIEW_LABEL[v]).join(", ") })}</p>
          <ol><li>${t("Copy the prompt in the <b>Character views</b> card into an image AI (attach the front image as reference)")}</li>
          <li>${t("Drop the result here (or in the Character views card); the tool sorts it into views")}</li></ol>
          ${dropMini(t("⬇ Drop a back / side view or a turnaround sheet here"), "images")}`
        : `<p>${t("All views present. Use these images with multi-view Image-to-3D if the service supports it")}</p>
          ${dropMini(t("⬇ Drop a better view here to replace one"), "images")}`;
    }
    case "sheets": {
      const missing = Object.values(p.sheets2d || {}).flatMap((r) => Object.values(r.views).filter((c) => !c.url).map((c) => `${r.th}-${c.th}`));
      return `<ol><li>${t("In the <b>Poses (AI-drawn)</b> card, press <b>📋 All actions (one prompt)</b>")}</li>
          <li>${t("Paste the prompt into Gemini / ChatGPT and attach 2 images: the character + the <b>🦴 pose guide</b> (the button next to the prompt) so the AI follows the poses")}</li>
          <li>${t("Drop the result in the <b>⬇ Drop the AI-drawn image</b> box in the same card; the tool cuts it into every action and view and builds the sprite")}</li>
          <li>${t("For any action showing ⚠, use that action's 📋 button (or the one in that cell) to redraw it, then drop it in the same box")}</li></ol>
        ${p.renders?.sprite ? `<button class="btn small" data-act="build2d">${t("⟳ Rebuild sprite")}</button>` : ""}
        ${missing.length ? `<p class="hint">${t("{n} images still missing (empty cells borrow the other view for now)", { n: missing.length })}</p>` : `<p>${t("All cells filled ✓")}</p>`}`;
    }
    case "modelcheck":
      return p.model_check
        ? `<p>${t('See the <b>3D model check</b> card on the board: compare the 8 directions with the concept, fix the model\'s facing if it is wrong, then press "Model is correct"')}</p>`
        : `<p>${p.jobs?.inspect?.state === "running" ? t("⏳ Blender is shooting the model…") : t("Upload a model at step 3 and the tool checks it automatically")}</p>`;
    case "rig":
      return humanoid
        ? `<ol>
            <li>${t("Upload the model to <b>Mixamo</b> → Auto-Rig")}</li>
            <li>${t("Download Idle as <b>FBX · With Skin · 30fps</b> → name it <code>{file}</code> (replaces the old file)", { file: `${esc(p.slug)}.fbx` })}</li>
            <li>${t("Other actions <b>Without Skin</b>, named <code>{slug}@walk.fbx</code>, <code>@attack</code>, <code>@hurt</code>, <code>@die</code> · Walk: <b>leave In Place unticked</b>", { slug: esc(p.slug) })}</li>
          </ol>${dropMini(t("Drop animation files (@walk.fbx …) here"))}${fileList}`
        : `<p>${t("<b>Mixamo doesn't work for quadrupeds.</b> Pick one route:")}</p>
          <ul>
            <li>${t("<b>Blender + Rigify</b> (free): Add ▸ Armature ▸ Basic Quadruped → fit the bones to the model → Generate Rig → Ctrl+P With Automatic Weights → make actions named idle, walk, attack, hurt, die, sit, sleep, happy → save <code>{file}</code>", { file: `${esc(p.slug)}.blend` })}</li>
            <li>${t("<b>Auto-Rig Pro</b> (paid add-on): quadruped presets + animation retargeting")}</li>
            <li>${t("<b>AI animal rigging services</b> (e.g. Anything World): upload the model → get a rig + walk (check the service's terms of use yourself)")}</li>
          </ul>
          <p>${t("Walk: let the root really move forward; the script measures the stride speed")}</p>${dropMini(t("Drop an animated .blend or .fbx here"))}${fileList}`;
    case "render": {
      const r = p.render || {};
      const job = p.jobs?.render;
      return `<div class="grid2">
          <label class="field">${t("px per metre (same value for every character)")} <input type="number" data-r="ppm" value="${r.ppm ?? 64}" /></label>
          <label class="field">${t("Frame size")} <input type="number" data-r="size" value="${r.size ?? 256}" /></label>
          <label class="field">${t("Frame step (2 = 15fps)")} <input type="number" data-r="step" value="${r.step ?? 2}" min="1" /></label>
          <label class="check"><input type="checkbox" data-r="mirror" ${r.mirror ? "checked" : ""} /> ${t("mirror 5→8 directions")}</label>
        </div>
        <div class="btn-row">
          <button class="btn small" data-act="render" ${caps.blender && files.length ? "" : "disabled"}>${t("▶ Render 8 directions now")}</button>
          <button class="btn small" data-act="cmd" ${files.length ? "" : "disabled"}>${t("Copy command")}</button>
        </div>
        ${job ? `<div class="joblog">${esc(job.state)}${job.elapsed != null ? ` · ${job.elapsed}s` : ""}\n${esc((job.tail || []).join("\n"))}</div>` : ""}`;
    }
    case "anims": {
      const st = p.anim_status || {};
      const missing = Object.entries(st).filter(([, v]) => !v).map(([k]) => k);
      return missing.length
        ? `<p>${t("Still missing: <b>{list}</b>", { list: missing.map(esc).join(", ") })}</p><p>${t("Add an action with this name in the .blend or upload <code>{file}</code>, then press Render again", { file: `${esc(p.slug)}@${esc(missing[0])}.fbx` })}</p>${dropMini(t("Drop more animation files"))}`
        : `<p>${t("All planned animations present ✓")}</p>`;
    }
    case "sprite":
      return p.renders?.sprite
        ? `<p>${t('Got <code>{spr}</code> + <code>{act}</code>. Open them in the "Result + downloads" card (🔍 Frame by frame / GIF)', { spr: `${esc(p.slug)}.spr`, act: `${esc(p.slug)}.act` })}</p>
          <button class="btn small" data-act="roexport">${t("⟳ Convert again (e.g. after adjusting the hit frame in Playtest)")}</button>`
        : `<p>${t("Converted automatically from the render: scaled to the classic size, palette ≤255 colours, crisp edges + outline, frames reduced to the usual timing, atk / step events added")}</p>
          ${p.renders ? `<button class="btn small" data-act="roexport">${t("▶ Convert now")}</button>` : ""}`;
    case "tune":
      return `<p>${t("Open Playtest → set walk speed, damage frame, attack range → press Save")}</p>
        <button class="btn small" data-act="lab" ${p.renders ? "" : "disabled"}>${t("Open in Playtest")}</button>`;
    case "vfx":
      return `<p>${t("Skill effects are not part of this version: keep the skill plan as notes for your game.")}</p>`;
    case "export":
      if ((p.mode || "2d") === "2d") {
        const r = p.renders?.sprite;
        return r
          ? `${playtestCta()}<div class="btn-row"><a class="btn small primary" href="/api/sprites/download?id=${r.sprite_id}&kind=web">${t("⬇ Web game (PixiJS / Phaser)")}</a>
              <a class="btn small" href="/api/sprites/download?id=${r.sprite_id}&kind=zip">${t("⬇ All images (zip)")}</a></div>
             <p class="hint">${t(".spr / .act files and single frames: the 🔍 button in the result card")}</p>`
          : `<p>${t("Available once the AI-drawn pose images are in")}</p>`;
      }
      return `<p>${t("zip: sheets for every action + character.json (pivot, frame timing, events) + lab_config + profile + portrait")}</p>
        <a class="btn small" href="/api/projects/${esc(p.slug)}/export.zip" data-act="export" ${p.renders ? "" : "aria-disabled='true'"}>${t("⬇ Export for the game")}</a>`;
    default:
      return "";
  }
}

function renderSteps() {
  const ol = $("pipeSteps");
  if (!project) { ol.replaceChildren(); return; }
  const open = new Set([...ol.querySelectorAll("details[open]")].map((d) => d.dataset.id));
  ol.replaceChildren(...project.steps.map((s, i) => {
    const li = document.createElement("li");
    li.className = `step${s.done ? " done" : ""}${s.next ? " next" : ""}`;
    const det = document.createElement("details");
    det.dataset.id = s.id;
    det.open = open.has(s.id) || !!s.next || s.id === "upload" || (s.id === "export" && !!project.renders?.sprite);
    det.innerHTML = `<summary><span class="num" title="${s.done ? esc(t("Done")) : ""}">${i + 1}</span><span>${esc(s.title)}</span><span class="where ${s.where}">${WHERE[s.where]}</span>
        ${s.detail ? `<span class="detail">${esc(s.detail)}</span>` : ""}</summary><div class="body">${stepBody(s)}</div>`;
    li.appendChild(det);
    return li;
  }));
  bindStepActions(ol);
}

function bindStepActions(root) {
  root.querySelectorAll("[data-upload]").forEach((zone) => {
    const input = zone.querySelector("input");
    const opts = zone.dataset.upload === "images" ? { allowModels: false } : { allowImages: false };
    const take = (files) => (zone.dataset.upload === "sheets" ? uploadSheets(files) : handleFiles(files, null, opts));
    input.addEventListener("change", () => take(input.files));
    zone.addEventListener("dragover", (e) => { e.preventDefault(); zone.classList.add("drag"); });
    zone.addEventListener("dragleave", () => zone.classList.remove("drag"));
    zone.addEventListener("drop", (e) => { e.preventDefault(); e.stopPropagation(); zone.classList.remove("drag"); take(e.dataTransfer.files); });
  });
  root.querySelectorAll("[data-del]").forEach((b) => b.addEventListener("click", async (e) => {
    e.preventDefault();
    project = await api(`/api/projects/${project.slug}/files/${encodeURIComponent(b.dataset.del)}`, { method: "DELETE" });
    renderSteps();
  }));
  root.querySelectorAll("[data-r]").forEach((el) => el.addEventListener("change", () => {
    const k = el.dataset.r;
    patch({ render: { [k]: el.type === "checkbox" ? el.checked : Number(el.value) } });
  }));
  root.querySelectorAll("[data-act]").forEach((b) => b.addEventListener("click", async (e) => {
    const act = b.dataset.act;
    if (act === "export") {
      if (!project.renders) { e.preventDefault(); return; }
      setTimeout(() => openProject(project.slug), 1500); // refresh the "exported" tick
      return;
    }
    e.preventDefault();
    if (act === "playtest") {
      window.dispatchEvent(new CustomEvent("lab:add", { detail: labSource(project) }));
      return;
    }
    try {
      if (act === "confirm") await patch({ notes: { profile_confirmed: true } });
      if (act === "manual") {
        await patch({ ai_pending: false });
        await render();
        toast(t("Fill in the description (English) and details in the character info card"));
      }
      if (act === "copyai") {
        await navigator.clipboard.writeText(t("Classify character {slug}", { slug: project.slug }));
        toast(t("Copied: paste it into your AI assistant's chat"));
      }
      if (act === "refresh") await openProject(project.slug);
      if (act === "render") {
        project.job = await api(`/api/projects/${project.slug}/render`, { method: "POST" });
        renderSteps();
        schedulePoll();
      }
      if (act === "cmd") {
        const { command } = await api(`/api/projects/${project.slug}/render-command`);
        await navigator.clipboard.writeText(command);
        toast(t("Command copied (paste it into PowerShell)"));
      }
      if (act === "build2d") {
        busy(true, t("Building sprite…"));
        project = await api(`/api/projects/${project.slug}/build2d`, { method: "POST" });
        await render();
      }
      if (act === "roexport") {
        busy(true, t("Converting to a sprite…"));
        project = await api(`/api/projects/${project.slug}/spr-export`, { method: "POST" });
        await render();
      }
      if (act === "gen3d") {
        project.jobs = { ...(project.jobs || {}), gen3d: await api(`/api/projects/${project.slug}/gen3d`, { method: "POST" }) };
        renderBoard();
        renderSteps();
        schedulePoll();
      }
      if (act === "lab") window.dispatchEvent(new CustomEvent("lab:add", { detail: labSource(project) }));
    } catch (err) {
      toast(err.message, true);
    } finally {
      busy(false);
    }
  }));
}

async function uploadModels(fileList) {
  if (!fileList?.length) return;
  const form = new FormData();
  for (const f of fileList) form.append("files", f);
  busy(true, t("Uploading {n} files…", { n: fileList.length }));
  try {
    project = await api(`/api/projects/${project.slug}/files`, { method: "POST", body: form });
    renderBoard();
    renderSteps();
    schedulePoll();
  } catch (err) {
    toast(err.message, true);
  } finally {
    busy(false);
  }
}

// Poll while Blender runs (model check or render); reload the board when a job finishes.
function anyRunning(jobs) {
  return Object.values(jobs || {}).some((j) => j?.state === "running");
}

function schedulePoll() {
  clearTimeout(pollTimer);
  if (!anyRunning(project?.jobs)) return;
  pollTimer = setTimeout(async () => {
    const jobs = await api(`/api/projects/${project.slug}/jobs`);
    const before = project.jobs;
    project.jobs = jobs;
    project.job = jobs.render;
    if (anyRunning(jobs)) {
      if (before?.gen3d?.state === "running" && jobs.gen3d?.state !== "running") {
        await openProject(project.slug); // model just appeared -> show the check that started on its own
      } else {
        renderSteps();
      }
      schedulePoll();
      return;
    }
    for (const kind of ["gen3d", "inspect", "render"]) {
      if (before?.[kind]?.state === "running") {
        const ok = jobs[kind]?.state === "done";
        const label = { render: "Render", inspect: t("Model check"), gen3d: t("Create 3D model") }[kind];
        toast(ok ? t("{label} done", { label }) : t("{label} failed: see the log", { label }), !ok);
      }
    }
    await openProject(project.slug);
  }, 2000);
}

// ───────────────────────── wiring ─────────────────────────

const drop = $("pipeDrop");
$("pipeGroup").addEventListener("change", (e) => pickGroup(e.target.value));
$("pipeFile").addEventListener("change", (e) => { createFromFile(e.target.files[0]); e.target.value = ""; });
drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("drag"); });
drop.addEventListener("dragleave", () => drop.classList.remove("drag"));
drop.addEventListener("drop", (e) => { e.preventDefault(); e.stopPropagation(); drop.classList.remove("drag"); createFromFile(e.dataTransfer.files[0]); });

let started = false;
async function onPage(page) {
  if (page !== "pipeline") return;
  if (!started) {
    started = true;
    await loadCaps();
    await loadList();
    const first = $("pipeProjects").querySelector("li.in-group:not(.empty)");
    if (first) first.click();
  } else if (project) {
    layoutCells();
  }
}
window.addEventListener("pagechange", (e) => onPage(e.detail.page));
onPage(activePage()); // the initial pagechange may have fired before this module loaded
