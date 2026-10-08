"""
Translation check: every English UI string in the code has an entry in each language file.

    python -m tools.i18n_check            # report missing / unused entries
    python -m tools.i18n_check --merge    # first fold static/locales/_<lang>_part_*.json into <lang>.json

Strings are found as the first literal argument of t("...") in static/js/**/*.js, of _("...") in
Python files, and in data-i18n / data-i18n-title / data-i18n-placeholder in static/index.html.
"""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCALES = ROOT / "static" / "locales"
JS_T = re.compile(r"""\bt\(\s*(?P<q>["'])(?P<s>(?:\\.|(?!(?P=q)).)*)(?P=q)""", re.S)
HTML_TEXT = re.compile(r"<(?P<tag>[a-z0-9]+)(?P<attrs>[^>]*\sdata-i18n(?:=\"(?P<src>[^\"]*)\")?[^>]*)>(?P<body>.*?)</(?P=tag)>", re.S)


def js_strings() -> set[str]:
    out = set()
    for f in (ROOT / "static" / "js").rglob("*.js"):
        code = "\n".join(ln for ln in f.read_text(encoding="utf-8").splitlines()
                         if not ln.lstrip().startswith(("//", "*", "/*")))
        for m in JS_T.finditer(code):
            s = m["s"]
            if m["q"] == "'":                       # 'it\'s "x"' -> JSON string syntax
                s = s.replace("\\'", "'").replace('"', '\\"')
            out.add(json.loads('"' + s + '"'))
    return out


def py_strings() -> set[str]:
    out = set()
    for f in [ROOT / "main.py", *(ROOT / "core").glob("*.py")]:
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("_", "N_")
                    and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
                out.add(node.args[0].value)
    return out


def html_strings() -> set[str]:
    src = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    out = {(m["src"] or m["body"].strip()) for m in HTML_TEXT.finditer(src)}
    for tag in re.finditer(r"<[^>]+>", src):
        t = tag.group(0)
        for attr in ("title", "placeholder"):
            if f"data-i18n-{attr}" in t:
                m = re.search(rf'data-i18n-{attr}="([^"]+)"', t) or re.search(rf'\s{attr}="([^"]*)"', t)
                if m:
                    out.add(m.group(1))
    return {s for s in out if s}


def merge(code: str) -> None:
    target = LOCALES / f"{code}.json"
    table = json.loads(target.read_text(encoding="utf-8")) if target.exists() else {}
    conflicts = []
    for part in sorted(LOCALES.glob(f"_{code}_part_*.json")):
        for k, v in json.loads(part.read_text(encoding="utf-8")).items():
            if k in table and table[k] != v:
                conflicts.append((k, table[k], v, part.name))
            table.setdefault(k, v)
    target.write_text(json.dumps(dict(sorted(table.items())), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for k, a, b, src in conflicts:
        print(f"conflict ({src}): {k!r}: kept {a!r}, other {b!r}")
    print(f"{code}.json: {len(table)} entries, {len(conflicts)} conflicts")


def main() -> int:
    if "--merge" in sys.argv:
        for part in LOCALES.glob("_*_part_*.json"):
            merge(part.name.split("_")[1])
            break
    used = js_strings() | py_strings() | html_strings()
    bad = 0
    for f in sorted(LOCALES.glob("*.json")):
        if f.stem.startswith("_"):
            continue
        table = json.loads(f.read_text(encoding="utf-8"))
        missing = sorted(used - table.keys())
        unused = sorted(table.keys() - used)
        print(f"{f.name}: {len(table)} entries, {len(missing)} missing, "
              f"{len(unused)} not found as literals (fine if translated through a variable, e.g. _(label))")
        for s in missing[:40]:
            print("  missing:", s[:100])
        bad += len(missing)
    print(f"strings in code: {len(used)}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
