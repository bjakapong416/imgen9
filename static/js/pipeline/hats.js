// Headgear card: a hat/helmet as its own layer on the head of every frame (found from the drawing),
// so a game can swap it like equipment. Drop a hat picture or have the AI draw one from the prompt.

import { t } from "../i18n.js";

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

export function renderHats(card, p, deps) {
  const h = p.hats2d;
  if (!h) return;
  const active = h.items.find((i) => i.id === h.active);
  const el = card("span-12", t("🎩 Headgear (separate layer · swappable in game)"),
    t("A hat or helmet laid on the head in every frame (hidden while lying down) · the download also has it alone as <slug>_hat"));
  el.insertAdjacentHTML("beforeend", `
    <div class="weapon-desc">
      <label>${t("Headgear (English, used in the prompt)")}</label>
      <input data-h-desc value="${esc(h.desc_en)}" placeholder="e.g. a pointed purple wizard hat with a gold star" />
      <button class="btn small" data-h-prompt>📋 ${t("Prompt to draw the headgear")}</button>
    </div>
    <div class="weapon-list">
      <div class="weapon-item ${!h.active ? "active" : ""}" data-h-pick=""><div class="sheet-empty">${t("— No headgear —")}</div></div>
      ${h.items.map((it) => `
        <div class="weapon-item ${it.id === h.active ? "active" : ""}" data-h-pick="${esc(it.id)}">
          <img src="${esc(it.url)}" alt="" />
          <input data-h-name="${esc(it.id)}" value="${esc(it.name)}" />
          <button class="rm" data-h-rm="${esc(it.id)}" title="${t("Delete")}">✕</button>
        </div>`).join("")}
      <label class="weapon-drop" data-h-drop><input type="file" accept="image/*" hidden />＋ ${t("Drop a headgear image")}</label>
    </div>
    ${active ? `<div class="var-controls">
      <label>${t("Size")} <input type="range" min="0.4" max="2" step="0.05" data-h-k="size" value="${active.size}" /> ×<span>${active.size}</span></label>
      <label>${t("Left / right")} <input type="range" min="-0.2" max="0.2" step="0.005" data-h-k="dx" value="${active.dx}" /></label>
      <label>${t("Up / down")} <input type="range" min="-0.2" max="0.2" step="0.005" data-h-k="dy" value="${active.dy}" /></label>
      <span class="hint">${t("Changes rebuild the sprite when you let go of the slider.")}</span></div>` : ""}`);

  const put = async (patch, msg) => {
    deps.busy(true, t("Rebuilding sprite…"));
    try {
      deps.update(await deps.api(`/api/projects/${encodeURIComponent(p.slug)}/hats`, {
        method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify(patch),
      }));
      if (msg) deps.toast(msg);
    } catch (err) {
      deps.toast(err.message, true);
    } finally {
      deps.busy(false);
    }
  };
  el.querySelector("[data-h-desc]").addEventListener("change", (e) => put({ desc_en: e.target.value }));
  el.querySelector("[data-h-prompt]").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(h.prompt);
      deps.toast(t("Headgear prompt copied · attach the character image so the style matches"));
    } catch {
      deps.toast(t("Could not copy"), true);
    }
  });
  el.querySelectorAll("[data-h-pick]").forEach((d) => d.addEventListener("click", (e) => {
    if (e.target.closest("input, button")) return;
    const id = d.dataset.hPick || null;
    if (id !== h.active) put({ active: id }, id ? t("Headgear changed") : t("Headgear removed"));
  }));
  el.querySelectorAll("[data-h-name]").forEach((i) => i.addEventListener("change", () => put({ items: { [i.dataset.hName]: { name: i.value } } })));
  el.querySelectorAll("[data-h-rm]").forEach((b) => b.addEventListener("click", async () => {
    try {
      deps.update(await deps.api(`/api/projects/${encodeURIComponent(p.slug)}/hats/${encodeURIComponent(b.dataset.hRm)}`, { method: "DELETE" }));
    } catch (err) {
      deps.toast(err.message, true);
    }
  }));
  el.querySelectorAll("[data-h-k]").forEach((r) => {
    r.addEventListener("input", () => { if (r.nextElementSibling) r.nextElementSibling.textContent = r.value; });
    r.addEventListener("change", () => put({ items: { [active.id]: { [r.dataset.hK]: Number(r.value) } } }));
  });
  const drop = el.querySelector("[data-h-drop]");
  const upload = async (files) => {
    for (const f of [...files].filter((x) => x.type.startsWith("image/"))) {
      deps.busy(true, t("Removing the background…"));
      try {
        const form = new FormData();
        form.append("file", f);
        form.append("name", f.name.replace(/\.[^.]+$/, ""));
        deps.update(await deps.api(`/api/projects/${encodeURIComponent(p.slug)}/hats`, { method: "POST", body: form }));
        deps.toast(t("Headgear added"));
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
}
