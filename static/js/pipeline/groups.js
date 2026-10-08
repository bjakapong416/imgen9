// Projects (groups of characters): the grouped list on the left, the project page, and the
// "which project" pickers. A project holds the characters of one game or theme and the settings
// they share (art style, character style, sprite detail, a style reference image).

import { t } from "../i18n.js";

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

export const DEFAULT_GROUP = "my_characters";
export const groupName = (g) => (g?.name || (g?.id === DEFAULT_GROUP ? t("My characters") : g?.id || ""));

const folded = new Set(); // projects folded in the list (this page session)

/** The left list: each project with its characters under it. */
export function renderGroupList(ul, groups, items, { activeSlug, activeGroup, onCharacter, onGroup, onNew }) {
  ul.replaceChildren(...groups.flatMap((g) => {
    const chars = items.filter((c) => c.group === g.id);
    const head = document.createElement("li");
    head.className = `group-head${activeGroup === g.id ? " active" : ""}`;
    head.innerHTML = `<button class="fold" title="${esc(t("Show / hide its characters"))}">${folded.has(g.id) ? "▸" : "▾"}</button>
      <span class="g-name">📁 ${esc(groupName(g))}</span><small>${chars.length}</small>
      <button class="g-new" title="${esc(t("New character in this project"))}">＋</button>`;
    head.querySelector(".fold").addEventListener("click", (e) => {
      e.stopPropagation();
      folded.has(g.id) ? folded.delete(g.id) : folded.add(g.id);
      renderGroupList(ul, groups, items, { activeSlug, activeGroup, onCharacter, onGroup, onNew });
    });
    head.querySelector(".g-new").addEventListener("click", (e) => { e.stopPropagation(); onNew(g.id); });
    head.addEventListener("click", () => onGroup(g.id));
    if (folded.has(g.id)) return [head];
    const rows = chars.map((p) => {
      const li = document.createElement("li");
      li.className = `in-group${p.slug === activeSlug ? " active" : ""}`;
      li.innerHTML = `<img src="${esc(p.thumb)}" alt="" /><div><div>${esc(p.name)}</div><small>${esc(p.label || "")}</small></div>`;
      li.addEventListener("click", () => onCharacter(p.slug));
      return li;
    });
    if (!rows.length) {
      const li = document.createElement("li");
      li.className = "in-group empty";
      li.textContent = t("No characters yet: press ＋ or drop an image above");
      rows.push(li);
    }
    return [head, ...rows];
  }));
}

/** <option>s for a project picker; "+ New project…" at the end when withNew. */
export function groupOptions(groups, current, withNew = true) {
  return groups.map((g) => `<option value="${esc(g.id)}" ${g.id === current ? "selected" : ""}>📁 ${esc(groupName(g))}</option>`).join("")
    + (withNew ? `<option value="__new">＋ ${esc(t("New project…"))}</option>` : "");
}

/** A small in-app dialog with one text field; resolves to the text, or null when cancelled. */
function askText(title, placeholder) {
  return new Promise((resolve) => {
    const dlg = document.createElement("dialog");
    dlg.className = "ask-dialog";
    dlg.innerHTML = `<form method="dialog">
      <h3>${esc(title)}</h3>
      <input name="v" autocomplete="off" placeholder="${esc(placeholder)}" />
      <div class="btn-row"><button class="btn" value="">${esc(t("Cancel"))}</button>
        <button class="btn primary" value="ok">${esc(t("Create"))}</button></div></form>`;
    document.body.appendChild(dlg);
    const done = (v) => { dlg.close(); dlg.remove(); resolve(v || null); };
    dlg.querySelector("form").addEventListener("submit", (e) => {     // Enter, or either button
      e.preventDefault();
      done(e.submitter?.value === "" ? null : dlg.querySelector("input").value.trim());
    });
    dlg.addEventListener("cancel", (e) => { e.preventDefault(); done(null); });   // Esc
    dlg.showModal();
    dlg.querySelector("input").focus();
  });
}

/** Ask for a name and create a project; returns its id, or null. */
export async function askNewGroup(api, toast) {
  const name = await askText(t("New project"), t("A game or a theme, e.g. My RPG"));
  if (!name) return null;
  try {
    const g = await api("/api/groups", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ name }) });
    toast(t("Project created: {name}", { name: groupName(g) }));
    return g.id;
  } catch (err) {
    toast(err.message, true);
    return null;
  }
}

/** The project page: shared settings, its characters, downloads. */
export function renderGroupPage(board, g, deps) {
  const { api, toast, busy, onCharacter, onNew, reload, labAdd } = deps;
  board.replaceChildren();
  const put = async (patch) => {
    try {
      const ng = await api(`/api/groups/${g.id}`, { method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify(patch) });
      toast(t("Saved · characters of this project use it unless they set their own"));
      await reload(ng);
    } catch (err) {
      toast(err.message, true);
    }
  };
  const built = g.characters.filter((c) => c.sprite_id);
  const opt = (map, cur, none) => `<option value="" ${!cur ? "selected" : ""}>${esc(none)}</option>`
    + Object.entries(map).map(([k, v]) => `<option value="${esc(k)}" ${String(k) === String(cur ?? "") ? "selected" : ""}>${esc(v)}</option>`).join("");

  const head = document.createElement("section");
  head.className = "bcard span-12 group-page";
  head.innerHTML = `
    <h3>📁 ${esc(groupName(g))} <small>${t("{n} characters · {b} built", { n: g.characters.length, b: built.length })}</small></h3>
    <div class="group-actions">
      <button class="btn primary" data-new>＋ ${t("New character in this project")}</button>
      <a class="btn ${built.length ? "" : "disabled"}" ${built.length ? `href="/api/groups/${esc(g.id)}/download.zip"` : ""}>⬇ ${t("Download the whole project (zip)")}</a>
      <button class="btn" data-lab ${built.length ? "" : "disabled"}>▶ ${t("Add every character to Playtest")}</button>
    </div>
    <div class="group-chars">${g.characters.map((c) => `
      <button class="g-char" data-slug="${esc(c.slug)}"><img src="${esc(c.thumb)}" alt="" /><span>${esc(c.name)}</span>
        <small>${c.sprite_id ? "✓ " + esc(t("Built")) : esc(t("Not built yet"))}</small></button>`).join("")
      || `<p class="hint">${t("No characters yet: press “New character in this project” or drop an image on the left")}</p>`}</div>`;
  board.appendChild(head);

  const set = document.createElement("section");
  set.className = "bcard span-12";
  set.innerHTML = `
    <h3>🎨 ${t("Shared by every character of this project")} <small>${t("A character can still choose its own in its cards")}</small></h3>
    <div class="group-form">
      <label>${t("Project name")}</label><input data-g="name" value="${esc(g.name || "")}" placeholder="${esc(groupName(g))}" />
      <label title="${esc(t("Added to every drawing prompt of every character here, so the whole set looks like one game"))}">${t("Art style (English)")}</label>
      <textarea data-g="style_en" rows="2" placeholder="e.g. dark fantasy, hand-painted look, muted earthy colours, thick dark outlines">${esc(g.style_en || "")}</textarea>
      <label>${t("Character style")}</label><select data-g="body">${opt(g.body_options, g.body, t("— each character decides —"))}</select>
      <label>${t("Sprite detail")}</label><select data-g="detail">${opt(g.detail_options, g.detail, t("— each character decides —"))}</select>
      <label title="${esc(t("Attach it with every prompt: the AI copies its line art and colouring"))}">${t("Style reference image")}</label>
      <div class="style-ref">
        ${g.style_image ? `<img src="${esc(g.style_image)}" alt="" /><button class="btn small" data-style-rm>✕ ${t("Remove")}</button>
          <a class="btn small" href="${esc(g.style_image)}" download="${esc(g.id)}_style.png">⬇ ${t("Download (attach it to the AI)")}</a>` : ""}
        <label class="dropmini"><input type="file" accept="image/*" hidden data-style-up />${g.style_image ? t("Replace") : t("＋ Add a picture whose look every character should follow")}</label>
      </div>
    </div>
    ${g.id !== "my_characters" ? `<p class="hint"><button class="btn small" data-del ${g.characters.length ? "disabled" : ""}>🗑 ${t("Delete this project")}</button>
      ${g.characters.length ? t("Move or delete its characters first.") : ""}</p>` : ""}`;
  board.appendChild(set);

  head.querySelector("[data-new]").addEventListener("click", () => onNew(g.id));
  head.querySelector("[data-lab]")?.addEventListener("click", () => labAdd(built));
  head.querySelectorAll("[data-slug]").forEach((b) => b.addEventListener("click", () => onCharacter(b.dataset.slug)));
  set.querySelectorAll("[data-g]").forEach((el) => el.addEventListener("change", () => {
    const k = el.dataset.g;
    put({ [k]: k === "detail" ? (el.value ? Number(el.value) : null) : (el.value || null) });
  }));
  set.querySelector("[data-style-up]").addEventListener("change", async (e) => {
    const f = e.target.files[0];
    if (!f) return;
    busy(true, t("Saving…"));
    try {
      const form = new FormData();
      form.append("file", f);
      await reload(await api(`/api/groups/${g.id}/style-image`, { method: "POST", body: form }));
      toast(t("Style reference saved · every prompt of this project now asks the AI to match it"));
    } catch (err) {
      toast(err.message, true);
    } finally {
      busy(false);
    }
  });
  set.querySelector("[data-style-rm]")?.addEventListener("click", async () => {
    await reload(await api(`/api/groups/${g.id}/style-image`, { method: "DELETE" }));
  });
  set.querySelector("[data-del]")?.addEventListener("click", async () => {
    if (!window.confirm(t("Delete the project “{name}”?", { name: groupName(g) }))) return;
    try {
      await api(`/api/groups/${g.id}`, { method: "DELETE" });
      await reload(null);
    } catch (err) {
      toast(err.message, true);
    }
  });
}
