// Top-level page tabs. The active page is mirrored on <body data-page> so keyboard handlers
// can ignore keys meant for another page; a "pagechange" event lets pages start/stop work.

const pages = [...document.querySelectorAll(".page")];
const buttons = [...document.querySelectorAll("#tabs button")];

export function activePage() {
  return document.body.dataset.page || "pipeline";
}

export function showPage(name) {
  document.body.dataset.page = name;
  for (const p of pages) p.hidden = p.id !== `page-${name}`;
  for (const b of buttons) b.classList.toggle("active", b.dataset.page === name);
  try { localStorage.setItem("page", name); } catch { /* storage blocked */ }
  window.dispatchEvent(new CustomEvent("pagechange", { detail: { page: name } }));
}

for (const b of buttons) b.addEventListener("click", () => showPage(b.dataset.page));

let initial = "pipeline";
try { initial = localStorage.getItem("page") || "pipeline"; } catch { /* storage blocked */ }
if (!buttons.some((b) => b.dataset.page === initial)) initial = "pipeline";
// Defer so every page module has registered its pagechange listener.
queueMicrotask(() => showPage(initial));
