// Docs tab: the user guide, one HTML file per language (static/docs/guide.<lang>.html, English as the
// fallback). The table of contents is built from the sections' <h2>s and follows the scroll.
// Other pages can link to a section with openDocs("step4").

import { lang, t } from "../i18n.js";
import { showPage } from "../tabs.js";

const $ = (id) => document.getElementById(id);
let loaded = false;

async function load() {
  if (loaded) return;
  loaded = true;
  let html = "";
  for (const code of [lang, "en"]) {
    const r = await fetch(`/static/docs/guide.${code}.html`).catch(() => null);
    if (r?.ok) { html = await r.text(); break; }
  }
  const body = $("docsBody");
  body.innerHTML = html || `<p>${t("The guide could not be loaded.")}</p>`;
  const toc = $("docsToc");
  toc.replaceChildren(...[...body.querySelectorAll("section[id]")].map((s) => {
    const a = document.createElement("a");
    a.href = `#docs-${s.id}`;
    a.dataset.sec = s.id;
    a.textContent = s.querySelector("h2")?.textContent || s.id;
    a.addEventListener("click", (e) => { e.preventDefault(); scrollTo(s.id); });
    return a;
  }));
  // Highlight the section being read.
  const io = new IntersectionObserver((entries) => {
    for (const en of entries) {
      if (en.isIntersecting) {
        for (const a of toc.querySelectorAll("a")) a.classList.toggle("active", a.dataset.sec === en.target.id);
      }
    }
  }, { root: body, rootMargin: "0px 0px -70% 0px" });
  body.querySelectorAll("section[id]").forEach((s) => io.observe(s));
  const want = location.hash.match(/^#docs-(\w+)/)?.[1];
  if (want) scrollTo(want);
}

function scrollTo(id) {
  const s = document.getElementById(id);
  const body = $("docsBody");
  if (!s || !body) return;
  // Scroll the guide pane itself (scrollIntoView can stop early or move the outer page).
  body.scrollTo({ top: s.getBoundingClientRect().top - body.getBoundingClientRect().top + body.scrollTop - 8, behavior: "smooth" });
  history.replaceState(null, "", `#docs-${id}`);
}

/** Open the guide at a section, e.g. from a "?" link in another tab. */
export async function openDocs(section) {
  showPage("docs");
  await load();
  if (section) scrollTo(section);
}

window.addEventListener("pagechange", (e) => { if (e.detail.page === "docs") load(); });
window.addEventListener("docs:open", (e) => openDocs(e.detail?.section));
document.addEventListener("click", (e) => {
  const a = e.target.closest("[data-docs]");
  if (a) { e.preventDefault(); openDocs(a.dataset.docs); }
});
