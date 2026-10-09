// 🔌 Connections: which AI tools this PC has (checked locally by /api/connections) and which image AI
// the user draws with. Opens by itself the first time; the top-bar button shows a coloured dot.

import { t } from "./i18n.js";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

export const IMAGE_AIS = {
  gemini: { label: "Gemini", url: "https://gemini.google.com/app", cls: "ai-gemini" },
  chatgpt: { label: "ChatGPT", url: "https://chatgpt.com/", cls: "ai-chatgpt" },
  generic: { label: t("Other AI"), url: "", cls: "ai-other" },
};

export function imageAi() {
  try { return localStorage.getItem("pipe:target") || "gemini"; } catch { return "gemini"; }
}

function setImageAi(k) {
  try { localStorage.setItem("pipe:target", k); } catch { /* storage blocked */ }
  window.dispatchEvent(new CustomEvent("conn:target", { detail: { target: k } }));
}

let status = null;

async function load(refresh = false) {
  const r = await fetch(`/api/connections${refresh ? "?refresh=true" : ""}`);
  status = await r.json();
  paintButton();
  return status;
}

function paintButton() {
  const ok = ASSISTANTS.some((a) => status?.tools?.[a.key]?.found);
  $("connBtn").classList.toggle("ok", ok);
  $("connBtn").title = ok ? t("AI assistant found · image AI: {ai}", { ai: IMAGE_AIS[imageAi()]?.label || "" })
    : t("No AI assistant found: you can still type the character info yourself");
}

function pill(on, yes, no, optional = false) {
  return `<span class="pill ${on ? "on" : optional ? "opt" : "off"}">${on ? "● " + yes : "○ " + no}</span>`;
}

const need = (required) => required
  ? `<span class="need req">${t("Required")}</span>`
  : `<span class="need opt">${t("Not required")}</span>`;

// AI assistants that work in a chat in this folder: any of them can do the AI tasks in CLAUDE.md
// (Gemini CLI reads GEMINI.md, Codex reads AGENTS.md; both point there).
const ASSISTANTS = [
  { key: "claude_code", maker: "Anthropic" },
  { key: "gemini_cli", maker: "Google" },
  { key: "codex_cli", maker: "OpenAI · ChatGPT" },
];

function render() {
  const s = status;
  const tools = s.tools;
  const cur = imageAi();
  const found = ASSISTANTS.filter((a) => tools[a.key]?.found);
  $("connBody").innerHTML = `
    <section class="conn-summary">
      <div><span class="need req">${t("Required")}</span> ${t("only one thing: an image AI on the web (Gemini or ChatGPT) to draw the poses")}</div>
      <div><span class="need opt">${t("Not required")}</span> ${t("everything else: without it the tool still works, you just type the character description yourself")}</div>
    </section>

    <section class="conn-card c-image">
      <div class="conn-head"><span class="conn-icon">🖼</span><div><b>${t("Image AI (draws the poses)")}</b>
        <small>${t("The tool writes the prompt, you paste it into this AI on the web")}</small></div>${need(true)}</div>
      <div class="conn-choice">${Object.entries(IMAGE_AIS).map(([k, a]) =>
        `<button class="ai-btn ${a.cls} ${k === cur ? "active" : ""}" data-ai="${k}">${k === cur ? "✓ " : ""}${esc(a.label)}</button>`).join("")}</div>
      ${IMAGE_AIS[cur]?.url ? `<a class="btn conn-go ${IMAGE_AIS[cur].cls}" href="${IMAGE_AIS[cur].url}" target="_blank" rel="noopener">${t("Open {ai} ↗", { ai: esc(IMAGE_AIS[cur].label) })}</a>` : ""}
      <p class="hint">${t("Pick an AI that can make pictures. Claude can't draw images, so it isn't in this list.")}</p>
      <p class="hint">${t("Sign in on that website in your browser. The tool never looks at your browser or your accounts.")}</p>
    </section>

    <section class="conn-card c-claude">
      <div class="conn-head"><span class="conn-icon">🤖</span><div><b>${t("AI assistant in a chat")}</b>
        <small>${t("Fills in the character info and reviews the drawings. Any one of these is enough")}</small></div>${need(false)}</div>
      <ul class="conn-list">${ASSISTANTS.map((a) => {
        const tl = tools[a.key];
        return `<li><span><b>${esc(tl.name)}</b> <small>${esc(a.maker)}</small> <code>${esc(tl.cmd)}</code></span>
          ${pill(tl.found, tl.version ? esc(tl.version) : t("Found"), t("Not found"), true)}
          ${tl.found ? "" : `<a href="${tl.install}" target="_blank" rel="noopener">${t("Install ↗")}</a>`}</li>`;
      }).join("")}</ul>
      ${found.length
        ? `<p>${t("Found: {names}. Open it in the ImGen9 folder and type {cmd}", { names: esc(found.map((a) => tools[a.key].name).join(", ")), cmd: `<code>${esc(t("Classify character {slug}", { slug: "…" }))}</code>` })}</p>
           <p class="hint">${t("If it asks you to sign in, do it once in the terminal; the tool never touches your sign-in.")}</p>`
        : `<p>${t("None found: type the character description yourself in the Character info card, it works the same.")}</p>`}
    </section>

    <section class="conn-card c-keys">
      <div class="conn-head"><span class="conn-icon">🔑</span><div><b>${t("API keys")}</b>
        <small>${t("For a future automatic mode (paid per image) · only “set or not” is shown, never the key")}</small></div>${need(false)}</div>
      <ul class="conn-list">${Object.values(s.keys).map((k) => `<li><span>${esc(k.name)} <code>${esc(k.vars[0])}</code></span>
        ${pill(k.set, t("Set"), t("Not set"), true)}
        ${k.set ? "" : `<a href="${k.get}" target="_blank" rel="noopener">${t("Get a key ↗")}</a>`}</li>`).join("")}</ul>
      <p class="hint">${t("Set a key as an environment variable before starting the server; never paste keys into a chat.")}</p>
    </section>

    <section class="conn-card c-blender">
      <div class="conn-head"><span class="conn-icon">🧊</span><div><b>Blender</b>
        <small>${t("Only for the experimental 3D route; the 2D route never uses it")}</small></div>${need(false)}${pill(s.blender, t("Found"), t("Not found"), true)}</div>
    </section>
    <p class="hint conn-foot">${t("Checked on this PC at {time} · nothing is sent anywhere", { time: esc(s.checked) })}</p>`;
  $("connBody").querySelectorAll("[data-ai]").forEach((b) => b.addEventListener("click", () => { setImageAi(b.dataset.ai); render(); paintButton(); }));
}

export async function openConnections() {
  const dlg = $("connDialog");
  if (!status) await load();
  render();
  if (!dlg.open) dlg.showModal();
  try { localStorage.setItem("conn:seen", "1"); } catch { /* storage blocked */ }
}

$("connBtn").addEventListener("click", openConnections);
$("connClose").addEventListener("click", () => $("connDialog").close());
$("connRefresh").addEventListener("click", async () => {
  $("connRefresh").disabled = true;
  try { await load(true); render(); } finally { $("connRefresh").disabled = false; }
});
$("connDialog").addEventListener("click", (e) => { if (e.target === $("connDialog")) $("connDialog").close(); });

// The running version, next to the name (shown in bug reports; newer versions are on the Releases page).
fetch("/api/version").then((r) => r.json()).then((v) => {
  $("appVersion").textContent = `v${v.version}`;
  $("appVersion").href = v.releases;
}).catch(() => { /* older server: no version */ });

load().then(() => {
  let seen = false;
  try { seen = !!localStorage.getItem("conn:seen"); } catch { seen = true; }
  if (!seen) openConnections();
}).catch(() => { /* server without the endpoint: leave the button plain */ });
