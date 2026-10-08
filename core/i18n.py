"""
UI language for text the server writes (step titles, checks, error messages).

English is the source text in the code: wrap it in `_()`. Other languages are dictionaries in
static/locales/<code>.json (English text -> translation), shared with the browser (static/js/i18n.js),
so a new language is one new JSON file. Placeholders use str.format names: `_("Got {n} frames", n=7)`.

The language of a request comes from the `lang` cookie the browser sets, else Accept-Language;
`LanguageMiddleware` in main.py stores it for the duration of the request.
"""

from __future__ import annotations

import contextvars
import json
from functools import lru_cache
from pathlib import Path

LOCALES_DIR = Path(__file__).resolve().parent.parent / "static" / "locales"
DEFAULT = "en"

_lang: contextvars.ContextVar[str] = contextvars.ContextVar("lang", default=DEFAULT)


@lru_cache(maxsize=None)
def _table(code: str) -> dict[str, str]:
    f = LOCALES_DIR / f"{code}.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}


def supported() -> set[str]:
    return {DEFAULT} | {f.stem for f in LOCALES_DIR.glob("*.json") if not f.stem.startswith("_")}


def pick(cookie: str | None, accept_language: str | None) -> str:
    """Language for a request: the cookie if supported, else the first supported Accept-Language."""
    ok = supported()
    if cookie in ok:
        return cookie
    for part in (accept_language or "").split(","):
        code = part.split(";")[0].strip().lower()[:2]
        if code in ok:
            return code
    return DEFAULT


def set_lang(code: str) -> contextvars.Token:
    return _lang.set(code if code in supported() else DEFAULT)


def reset(token: contextvars.Token) -> None:
    _lang.reset(token)


def current() -> str:
    return _lang.get()


def N_(text: str) -> str:
    """Mark English text for translation later (e.g. stored in a file, translated when shown)."""
    return text


def tr(item) -> str:
    """Translate a stored [text, {placeholders}] pair (see N_) in the request's language."""
    text, kw = item
    return _(text, **(kw or {}))


def _(text: str, **kw) -> str:
    """Translate English `text` into the request's language, then fill `{placeholders}`."""
    code = _lang.get()
    if code != DEFAULT:
        text = _table(code).get(text, text)
    return text.format(**kw) if kw else text
