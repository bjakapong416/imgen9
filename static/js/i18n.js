// UI language. English is the source text in the code: wrap it in t(). Other languages are
// dictionaries in /static/locales/<code>.json (English text -> translation), shared with the server
// (core/i18n.py), so a new language is one new JSON file plus an entry in LANGUAGES.
//
//   t("Copy prompt")                    -> "คัดลอก prompt" in Thai
//   t("Got {n} frames", { n: 7 })       -> placeholders by name; escape values before HTML use
//
// Static HTML: mark elements with data-i18n (text), data-i18n-title, data-i18n-placeholder.

export const LANGUAGES = { en: "English", th: "ไทย" };

function pickLang() {
  try {
    const saved = localStorage.getItem("lang");
    if (saved && saved in LANGUAGES) return saved;
  } catch { /* storage blocked */ }
  const nav = (navigator.language || "en").slice(0, 2).toLowerCase();
  return nav in LANGUAGES ? nav : "en";
}

export const lang = pickLang();
let table = {};
if (lang !== "en") {
  try {
    table = await fetch(`/static/locales/${lang}.json`).then((r) => (r.ok ? r.json() : {}));
  } catch { /* missing dictionary: show English */ }
}
// The server reads this cookie for the text it writes (step titles, checks, errors).
document.cookie = `lang=${lang}; path=/; max-age=31536000; samesite=lax`;
document.documentElement.lang = lang;

export function t(text, vars) {
  let s = table[text] ?? text;
  if (vars) s = s.replace(/\{(\w+)\}/g, (m, k) => (k in vars ? String(vars[k]) : m));
  return s;
}

export function setLang(code) {
  try { localStorage.setItem("lang", code); } catch { /* ignore */ }
  document.cookie = `lang=${code}; path=/; max-age=31536000; samesite=lax`;
  location.reload();
}

/** Translate the static HTML once, and wire the language picker in the top bar. */
function translatePage() {
  for (const el of document.querySelectorAll("[data-i18n]")) {
    // Keep the English source in the attribute so it survives re-translation.
    const src = el.dataset.i18n || el.innerHTML.trim();
    el.dataset.i18n = src;
    el.innerHTML = t(src);
  }
  for (const attr of ["title", "placeholder"]) {
    for (const el of document.querySelectorAll(`[data-i18n-${attr}]`)) {
      const src = el.getAttribute(`data-i18n-${attr}`) || el.getAttribute(attr) || "";
      el.setAttribute(`data-i18n-${attr}`, src);
      el.setAttribute(attr, t(src));
    }
  }
  const sel = document.getElementById("langSel");
  if (sel && !sel.options.length) {
    sel.innerHTML = Object.entries(LANGUAGES).map(([k, v]) => `<option value="${k}" ${k === lang ? "selected" : ""}>${v}</option>`).join("");
    sel.addEventListener("change", () => setLang(sel.value));
  }
}
translatePage();
