"""
AI steps of the Pipeline, done by Claude Code in chat (no API key needed).

    python -m tools.ai_tasks list                 projects waiting for AI classification
    python -m tools.ai_tasks show <slug>          image paths + current profile + JSON schema
    python -m tools.ai_tasks apply <slug> <file>  validate a profile JSON and save it
    python -m tools.ai_tasks review <slug> [out]  contact sheet of every drawn row, for an art review
    python -m tools.ai_tasks apply-review <slug> <file>   save the review (verdict + notes per row)

The profile schema is core.classify.CreatureProfile - the same one the API path uses - so a
profile written by Claude Code and one from the API are interchangeable.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.classify import CreatureProfile  # noqa: E402
from core.config import settings  # noqa: E402
from core.layout import Layout  # noqa: E402

layout = Layout(settings.projects_dir, settings.renders_dir)

sys.stdout.reconfigure(encoding="utf-8")


def _projects():
    for d in sorted(settings.projects_dir.glob("*/*/project.json")):
        yield d.parent, json.loads(d.read_text(encoding="utf-8"))


def cmd_list() -> None:
    pending = [(d, p) for d, p in _projects() if p.get("ai_pending")]
    if not pending:
        print("No projects waiting for AI classification.")
    for d, p in pending:
        print(f"{p['slug']}\t{p['profile'].get('name_th', '')}\t{d / 'concept.png'}")


def cmd_show(slug: str) -> None:
    d = layout.project_dir(slug)
    p = json.loads((d / "project.json").read_text(encoding="utf-8"))
    print("concept:", d / "concept.png")
    print("cutout: ", d / "cutout.png")
    for v in sorted((d / "views").glob("*.png")) if (d / "views").exists() else []:
        print(f"view {v.stem}:", v)
    print("ai_pending:", p.get("ai_pending", False))
    print("current profile:", json.dumps({k: v for k, v in p["profile"].items() if not k.startswith("_")}, ensure_ascii=False, indent=2))
    print("schema:", json.dumps(CreatureProfile.model_json_schema(), ensure_ascii=False))


def cmd_apply(slug: str, file: str) -> None:
    d = layout.project_dir(slug)
    path = d / "project.json"
    p = json.loads(path.read_text(encoding="utf-8"))
    data = json.loads(Path(file).read_text(encoding="utf-8"))
    profile = CreatureProfile.model_validate(data).model_dump()  # raises with a clear message if invalid
    profile["palette"] = p["profile"].get("palette", [])
    profile["_method"] = "claude_code"
    profile["_model"] = "Claude Code (chat)"
    profile["_confidence"] = {}
    p["profile"] = profile
    p["ai_pending"] = False
    path.write_text(json.dumps(p, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Saved AI profile for {slug}: {profile['name_th']} · {profile['body_type']} · {profile['element']} · rank {profile['rank']}")


REVIEW_VERDICTS = ("ok", "fix", "redraw")


def cmd_review(slug: str, out: str | None = None) -> None:
    """One labelled image of every drawn row (frames left to right, the row key on the left), with
    the reference views on top, so the reviewer sees the whole character at once."""
    from PIL import Image, ImageDraw

    from core import sprite2d

    d = layout.project_dir(slug)
    p = json.loads((d / "project.json").read_text(encoding="utf-8"))
    rows = []
    for f in sorted((d / "sheets").glob("*.png")):
        key = f.stem
        if key == "all_source" or "_" not in key:
            continue
        frames = sprite2d.frames_of(Image.open(f).convert("RGBA"))
        if frames:
            rows.append((key, frames))
    if not rows:
        raise SystemExit("No drawn rows yet.")
    H, LABEL, GAP = 150, 150, 8
    def fit(im):
        k = H / im.height
        return im.resize((max(1, int(im.width * k)), H))
    refs = [fit(Image.open(v).convert("RGBA")) for v in sorted((d / "views").glob("*.png"))] if (d / "views").exists() else []
    lines = [("reference", refs)] + [(k, [fit(f) for f in fr]) for k, fr in rows]
    W = max(LABEL + sum(im.width + GAP for im in ims) for _, ims in lines)
    sheet = Image.new("RGB", (W, (H + GAP) * len(lines)), (255, 255, 255))
    dr = ImageDraw.Draw(sheet)
    for r, (key, ims) in enumerate(lines):
        y = r * (H + GAP)
        dr.text((6, y + H // 2 - 6), key, fill=(0, 0, 0))
        x = LABEL
        for im in ims:
            sheet.paste(im, (x, y), im)
            x += im.width + GAP
        dr.line([(0, y + H + GAP // 2), (W, y + H + GAP // 2)], fill=(210, 210, 210))
    target = Path(out) if out else d / "sheets" / "review_sheet.png"
    sheet.save(target)
    rep = layout.render_dir(slug) / "sprite" / "build.json"
    checks = json.loads(rep.read_text(encoding="utf-8")).get("jitter", {}) if rep.exists() else {}
    print("contact sheet:", target)
    print("description:", p["profile"].get("description_en", ""))
    print("body style:", (p.get("sprite2d") or {}).get("body", "default"))
    for key, frames in rows:
        print(f"{key}: {len(frames)} frames · automatic check: {'; '.join(checks.get(key, {}).get('issues', [])) or 'ok'}")
    print('review JSON: {"summary": "...", "rows": {"<row key>": {"verdict": "ok|fix|redraw", "notes": "..."}}}')


def cmd_apply_review(slug: str, file: str) -> None:
    d = layout.project_dir(slug)
    path = d / "project.json"
    p = json.loads(path.read_text(encoding="utf-8"))
    data = json.loads(Path(file).read_text(encoding="utf-8"))
    rows = {}
    for key, r in (data.get("rows") or {}).items():
        verdict = r.get("verdict")
        if verdict not in REVIEW_VERDICTS:
            raise SystemExit(f"{key}: verdict must be one of {REVIEW_VERDICTS}")
        rows[key] = {"verdict": verdict, "notes": str(r.get("notes", ""))[:600]}
    import time
    p["art_review"] = {"date": time.strftime("%Y-%m-%dT%H:%M:%S"), "by": "Claude Code (chat)",
                       "summary": str(data.get("summary", ""))[:1200], "rows": rows}
    path.write_text(json.dumps(p, indent=2, ensure_ascii=False), encoding="utf-8")
    bad = [k for k, r in rows.items() if r["verdict"] != "ok"]
    print(f"Saved art review for {slug}: {len(rows)} rows, {len(bad)} to fix ({', '.join(bad) or 'none'})")


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["list"]:
        cmd_list()
    elif args[:1] == ["show"] and len(args) == 2:
        cmd_show(args[1])
    elif args[:1] == ["apply"] and len(args) == 3:
        cmd_apply(args[1], args[2])
    elif args[:1] == ["review"] and len(args) in (2, 3):
        cmd_review(args[1], args[2] if len(args) == 3 else None)
    elif args[:1] == ["apply-review"] and len(args) == 3:
        cmd_apply_review(args[1], args[2])
    else:
        raise SystemExit(__doc__)
