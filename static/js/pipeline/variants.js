// Colour variants (palette swaps): the same sprite in other colours, no redrawing. Pick the colours
// to change (a hue range — click a swatch of the sprite's own colours), turn their hue, scale their
// saturation/brightness, preview on the character image, save. Each variant becomes its own sprite.

import { t } from "../i18n.js";

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const draft = { name: "", hue_from: 0, hue_to: 360, shift: 120, sat: 1, light: 1 };

function previewUrl(slug, r) {
  const q = new URLSearchParams({ hue_from: r.hue_from, hue_to: r.hue_to, shift: r.shift, sat: r.sat, light: r.light });
  return `/api/projects/${encodeURIComponent(slug)}/variant-preview?${q}`;
}

export function renderVariants(card, p, deps) {
  const v = p.variants2d || { items: [], swatches: [] };
  const el = card("span-12", t("🎨 Colour variants"),
    t("The same sprite in other colours without redrawing: pick the colours to change, turn their hue · each variant is its own sprite"));
  el.insertAdjacentHTML("beforeend", `
    <div class="var-editor">
      <img class="var-preview" data-v-preview alt="" />
      <div class="var-controls">
        <div class="var-swatches"><span class="hint">${t("The sprite's colours — click one to change only that colour group:")}</span>
          ${v.swatches.map((s) => `<button class="var-sw" data-hue="${s.hue}" style="background:${esc(s.rgb)}" title="${s.hue}°"></button>`).join("")}
          <button class="btn small" data-v-all>${t("All colours")}</button></div>
        <label>${t("Colours from hue")} <input type="range" min="0" max="360" data-v="hue_from" /> <span data-o="hue_from"></span>°</label>
        <label>${t("to hue")} <input type="range" min="0" max="360" data-v="hue_to" /> <span data-o="hue_to"></span>°</label>
        <label>${t("Turn hue by")} <input type="range" min="-180" max="180" data-v="shift" /> <span data-o="shift"></span>°</label>
        <label>${t("Saturation")} <input type="range" min="0" max="2" step="0.05" data-v="sat" /> ×<span data-o="sat"></span></label>
        <label>${t("Brightness")} <input type="range" min="0.4" max="1.6" step="0.05" data-v="light" /> ×<span data-o="light"></span></label>
        <div class="btn-row"><input data-v-name placeholder="${esc(t("Name, e.g. Ice mage"))}" />
          <button class="btn small primary" data-v-add>＋ ${t("Add variant")}</button></div>
      </div>
    </div>
    <div class="var-list">
      ${v.items.map((r) => `
        <div class="var-item">
          <img src="${esc(previewUrl(p.slug, r))}" alt="" />
          <b>${esc(r.name)}</b>
          <div class="btn-row">
            ${r.sprite_id ? `<a class="btn small" href="/api/sprites/demo/${r.sprite_id}/demo.html" target="_blank" rel="noopener">▶ ${t("Demo")}</a>
              <a class="btn small" href="/api/sprites/download?id=${r.sprite_id}&kind=web">⬇ ${t("Web game")}</a>` : `<span class="hint">${t("Built with the next sprite build")}</span>`}
            <button class="rm" data-v-rm="${esc(r.id)}" title="${t("Delete")}">✕</button>
          </div>
        </div>`).join("") || `<p class="hint">${t("No variants yet.")}</p>`}
    </div>`);

  const img = el.querySelector("[data-v-preview]");
  let timer = 0;
  const sync = () => {
    for (const k of ["hue_from", "hue_to", "shift", "sat", "light"]) {
      el.querySelector(`[data-v="${k}"]`).value = draft[k];
      el.querySelector(`[data-o="${k}"]`).textContent = draft[k];
    }
    clearTimeout(timer);
    timer = setTimeout(() => { img.src = previewUrl(p.slug, draft); }, 120);
  };
  el.querySelectorAll("[data-v]").forEach((inp) => inp.addEventListener("input", () => { draft[inp.dataset.v] = Number(inp.value); sync(); }));
  el.querySelectorAll("[data-hue]").forEach((b) => b.addEventListener("click", () => {
    const h = Number(b.dataset.hue);
    draft.hue_from = (h + 340) % 360;
    draft.hue_to = (h + 20) % 360;
    sync();
  }));
  el.querySelector("[data-v-all]").addEventListener("click", () => { draft.hue_from = 0; draft.hue_to = 360; sync(); });
  const name = el.querySelector("[data-v-name]");
  name.value = draft.name;
  name.addEventListener("input", () => { draft.name = name.value; });

  const save = async (items, msg) => {
    deps.busy(true, t("Rebuilding sprite…"));
    try {
      deps.update(await deps.api(`/api/projects/${encodeURIComponent(p.slug)}/variants`, {
        method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify({ variants: items }),
      }));
      deps.toast(msg);
    } catch (err) {
      deps.toast(err.message, true);
    } finally {
      deps.busy(false);
    }
  };
  el.querySelector("[data-v-add]").addEventListener("click", () => {
    const rule = { ...draft, name: draft.name.trim() || t("Variant {n}", { n: v.items.length + 1 }) };
    draft.name = "";
    save([...v.items, rule], t("Variant added"));
  });
  el.querySelectorAll("[data-v-rm]").forEach((b) => b.addEventListener("click", () =>
    save(v.items.filter((r) => r.id !== b.dataset.vRm), t("Variant removed"))));
  sync();
}
