"""
One image -> game-ready character: a project per character with a step checklist.

projects/<slug>/
    concept.png        the uploaded image, untouched
    cutout.png         background removed + trimmed
    project.json       profile (classification, editable), plan, step notes
    model/             3D model + animation files (Meshy / Mixamo / .blend), uploaded by the user
    render.log         output of the last Blender run
The rendered sprites live in renders/<slug>/ (shared with Playtest).

Each step reports where it runs:
    local    - this tool does it
    blender  - this tool runs Blender for you
    external - a web service or manual work (we tell you exactly what to do and detect the result)
    missing  - not covered locally yet (documented in docs/ONE_IMAGE_TO_GAME.md)
"""

from __future__ import annotations

import glob
import io
import json
import os
import re
import shutil
import subprocess
import threading
import time
import zipfile
from pathlib import Path

import numpy as np
from PIL import Image

from .classify import classify
from .i18n import _, tr
from .layout import DEFAULT_GROUP, GROUP_KEYS, Layout, LayoutError
from .image_processing import load_image, process_character
from .spr_export import export as spr_export
from . import sprite2d, weapons
from .sprite_packer import needs_pack, pack
from .views import (ACTION_SHEET_MIN, REQUIRED_VIEWS, VIEW_LABELS, VIEWS, default_assignment, figure_count,
                    split_sheet, turnaround_prompt)

SLUG_RE = re.compile(r"^[a-z0-9_\-]{1,64}$")
MODEL_EXTS = {".fbx", ".glb", ".gltf", ".blend", ".obj"}
ANIM_ALIASES = {
    "idle": r"idle|stand|breath|wait",
    "walk": r"walk|move",
    "run": r"run|sprint|dash",
    "attack": r"attack|atk|bite|slash|claw|punch|strike",
    "hurt": r"hurt|damage|hit_react|hit",
    "die": r"die|death|dead",
    "sit": r"sit",
    "sleep": r"sleep|lie|rest",
    "happy": r"happy|cheer|victory|joy",
    "cast": r"cast|skill|spell",
}


class ProjectError(ValueError):
    pass


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")
    return s[:48] or f"char_{int(time.time())}"


def ImageOps_mirror(path: Path) -> None:
    from PIL import ImageOps

    ImageOps.mirror(Image.open(path)).save(path)


def find_blender() -> str | None:
    env = os.getenv("BLENDER_PATH")
    if env and Path(env).exists():
        return env
    hits = sorted(glob.glob("C:/Program Files/Blender Foundation/Blender */blender.exe"))
    if hits:
        return hits[-1]
    return shutil.which("blender")


class ProjectStore:
    def __init__(self, root: Path, renders_dir: Path, settings):
        self.root = root
        self.renders_dir = renders_dir
        self.settings = settings
        self.root.mkdir(parents=True, exist_ok=True)
        self.layout = Layout(root, renders_dir)
        self.layout.ensure_group(DEFAULT_GROUP)
        self._jobs: dict[str, dict] = {}
        self._lock = threading.Lock()

    # ───────── paths / io ─────────
    def _dir(self, slug: str) -> Path:
        if not SLUG_RE.match(slug or ""):
            raise ProjectError("Invalid project id.")
        try:
            d = self.layout.project_dir(slug)
        except LayoutError:
            raise ProjectError(f"No project '{slug}'.") from None
        if not (d / "project.json").exists():
            raise ProjectError(f"No project '{slug}'.")
        return d

    def _rdir(self, slug: str) -> Path:
        """renders/<project>/<character>/"""
        return self.layout.render_dir(slug)

    def _sid(self, slug: str, rest: str) -> str:
        from .sprite_library import own_sprite_id
        return own_sprite_id(self.layout.sprite_rel(slug, rest))

    def _read(self, d: Path) -> dict:
        p = json.loads((d / "project.json").read_text(encoding="utf-8"))
        p["_group"] = self._group_settings(d)        # the project's shared settings (never saved here)
        p["_group"]["has_style_image"] = (d.parent / "style.png").exists()
        return p

    def _write(self, d: Path, data: dict) -> None:
        data.pop("_group", None)
        data["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        (d / "project.json").write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    # ───────── create ─────────
    def create(self, data: bytes, filename: str, name: str = "", method: str = "auto", group: str = "") -> dict:
        group = group or DEFAULT_GROUP
        try:
            self.layout.read_group(group)
        except LayoutError as exc:
            raise ProjectError(str(exc)) from None
        s = self.settings
        result = process_character(
            data, remove_bg=True, trim=True, padding=4, model=s.rembg_model, post_process=s.rembg_post_process,
            alpha_threshold=s.alpha_threshold, max_pixels=s.max_image_pixels,
        )
        self._not_an_action_sheet(result.image)
        name_hint = name or Path(filename or "").stem
        # "claude_code": fill a provisional guess now; Claude Code finishes it in chat (tools/ai_tasks.py).
        ai_pending = method == "claude_code"
        profile = classify(result.image, name_hint, "heuristic" if ai_pending else method)

        # Thai names slugify to nothing; fall back to the English id, then the file name.
        candidates = [name, profile.get("name_en") if profile.get("name_en") != "character" else "", Path(filename or "").stem]
        slug = next((s for s in (slugify(c) for c in candidates if c) if not s.startswith("char_")), slugify(""))
        if not profile.get("name_th") or profile["name_th"] == _("New character"):
            profile["name_th"] = name or Path(filename or "").stem or _("New character")
        base, n = slug, 2
        while self.layout.exists(slug):
            slug = f"{base}_{n}"
            n += 1
        d = self.root / group / slug
        (d / "model").mkdir(parents=True)
        self.layout.forget()
        Image.open(io.BytesIO(data)).save(d / "concept.png")
        (d / "views").mkdir()
        figures, extras = split_sheet(result.image)
        self._save_extras(d, extras)
        if len(figures) >= 2:  # a turnaround sheet: one cut-out per view
            for fig, view in zip(figures, default_assignment(len(figures))):
                fig.save(d / "views" / f"{view}.png")
            figures[0].save(d / "cutout.png")
        else:
            result.image.save(d / "cutout.png")
            result.image.save(d / "views" / "front.png")

        project = {
            "format": "project/1",
            "slug": slug,
            "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "source_file": filename,
            "profile": profile,
            "render": {"ppm": 64, "size": 384 if profile["body_type"] != "humanoid" else 256, "step": 2, "mirror": False},
            "notes": {},
            "ai_pending": ai_pending,
            "views_source": "sheet" if len(figures) >= 2 else "single",
        }
        self._write(d, project)
        self._auto_gen3d(slug)
        return self.get(slug)

    # ───────── read / update ─────────
    def list(self) -> list[dict]:
        out = []
        dirs = [d for g in self.layout.groups() for d in (self.root / g).iterdir() if (d / "project.json").exists()]
        for d in sorted(dirs, key=lambda p: p.stat().st_mtime, reverse=True):
            p = self._read(d)
            slug = p["slug"]
            built = (self.renders_dir / d.parent.name / slug / "sprite" / f"{slug}.act").exists()
            out.append({"slug": slug, "name": p["profile"].get("name_th"), "element": p["profile"].get("element"),
                        "body_type": p["profile"].get("body_type"), "thumb": f"/projects/{slug}/cutout.png",
                        "group": d.parent.name,
                        "sprite_id": self._sid(slug, f"sprite/{slug}.act") if built else None})
        return out

    def get(self, slug: str) -> dict:
        d = self._dir(slug)
        p = self._read(d)
        p["images"] = {"concept": f"/projects/{slug}/concept.png", "cutout": f"/projects/{slug}/cutout.png"}
        g = self._group_settings(d)
        p["group"] = {"id": d.parent.name, "name": g.get("name") or "", "style_en": g.get("style_en") or ""}
        p["model_files"] = self._model_files(d)
        p["views"] = self._views(d, slug)
        p.setdefault("mode", "2d")  # 2D (AI-drawn sprites) is the main route; 3D is optional
        p["sheets2d"] = self._sheets2d(p, d, slug)
        p["weapons"] = self._weapons_view(p, d, slug)
        body = self._body2d(p)
        p["sheets2d_body"] = {"value": body, "options": {k: _(v["th"]) for k, v in sprite2d.BODY.items()}}
        p["sheets2d_prompt_all"] = sprite2d.prompt_all(self._desc_en(p), body, style=self._attack_style(p),
                                                       actions=self._actions2d(p), views=self._views2d(p))
        p["sheets2d_magic"] = self._magic(p)
        p["art_review_summary"] = {k: (p.get("art_review") or {}).get(k) for k in ("date", "summary")}
        p["hats2d"] = self._hats_view(p, d, slug)
        p["variants2d"] = self._variants_view(p, slug)
        p["sheets2d_side"] = "side" in self._views2d(p)
        p["sheets2d_extras"] = {"chosen": self._extras(p),
                                "options": {a: _(sprite2d.ACTIONS[a]["th"]) for a in sprite2d.EXTRA_ACTIONS}}
        p["sheets2d_colour_lock"] = (p.get("sprite2d") or {}).get("colour_lock") is not False
        p["sheets2d_detail"] = {"value": self._detail(p), "options": {k: _(v) for k, v in sprite2d.DETAIL.items()}}
        p["turnaround_prompt"] = ((p["profile"].get("turnaround_prompt") or turnaround_prompt(p["profile"]))
                                  + " " + sprite2d.body_sentence(body))
        p["renders"] = self._render_info(slug)
        p["model_check"] = self._model_check(p, d)
        p["gen3d_available"] = self.gen3d_available()
        p["steps"] = self._steps(p, d)
        p["jobs"] = {kind: self.job_status(slug, kind) for kind in ("gen3d", "inspect", "render")}
        p["gen3d_available"] = self.gen3d_available()
        p["job"] = p["jobs"]["render"]
        p["blender"] = find_blender()
        return p

    def update(self, slug: str, patch: dict) -> dict:
        d = self._dir(slug)
        p = self._read(d)
        for key in ("profile", "render", "notes", "sprite2d"):
            if isinstance(patch.get(key), dict):
                p[key] = {**p.get(key, {}), **patch[key]}
        if patch.get("ai_pending") is False:     # the user fills the profile in by hand instead
            p.pop("ai_pending", None)
        self._write(d, p)
        # Workflow: once the model is confirmed and already rigged + animated, render without asking.
        if (patch.get("notes") or {}).get("model_ok"):
            mc = self._model_check(p, d)
            job = self._jobs.get((slug, "render"))
            if mc and mc.get("armature") and mc.get("actions") and find_blender() and not (job and job["state"] == "running"):
                try:
                    self.start_render(slug)
                except ProjectError:
                    pass
        return self.get(slug)

    def reclassify(self, slug: str, method: str) -> dict:
        d = self._dir(slug)
        p = self._read(d)
        img = Image.open(d / "cutout.png").convert("RGBA")
        if method == "claude_code":
            p["ai_pending"] = True  # Claude Code picks it up from tools/ai_tasks.py
        else:
            p["profile"] = classify(img, p["profile"].get("name_th", ""), method)
        self._write(d, p)
        return self.get(slug)

    def add_model_file(self, slug: str, filename: str, stream, limit: int) -> dict:
        d = self._dir(slug)
        name = Path(filename or "").name
        if Path(name).suffix.lower() not in MODEL_EXTS:
            raise ProjectError(_('File {name} is not a 3D model: this slot takes {exts} (put images in the "Character views" card)',
                                 name=name, exts=", ".join(sorted(MODEL_EXTS))))
        target = d / "model" / name
        size = 0
        with target.open("wb") as f:
            while chunk := stream.read(1024 * 1024):
                size += len(chunk)
                if size > limit:
                    f.close()
                    target.unlink(missing_ok=True)
                    raise ProjectError(f"{name} is larger than {limit // (1024 * 1024)} MB.")
                f.write(chunk)
        self._invalidate_model_check(d)
        if find_blender():
            try:
                self.start_inspect(slug)
            except ProjectError:
                pass  # e.g. an inspection is already running; the user can re-run it
        return self.get(slug)

    def remove_model_file(self, slug: str, filename: str) -> dict:
        d = self._dir(slug)
        target = d / "model" / Path(filename).name
        if target.exists():
            target.unlink()
        self._invalidate_model_check(d)
        return self.get(slug)

    # ───────── 2D route: AI-drawn action strips -> sprite ─────────
    @staticmethod
    def _body2d(p: dict) -> str:
        body = (p.get("sprite2d") or {}).get("body") or (p.get("_group") or {}).get("body")
        if body in sprite2d.BODY:
            return body
        # "heads tall" means nothing for a four-legged beast: monsters follow their concept by default
        return sprite2d.DEFAULT_BODY if p["profile"].get("body_type", "humanoid") == "humanoid" else "reference"

    @staticmethod
    def _magic(p: dict) -> bool:
        """Has a magic attack (the "cast" action): the user's choice, else whether the plan has a cast."""
        magic = (p.get("sprite2d") or {}).get("magic")
        if isinstance(magic, bool):
            return magic
        return any(a.get("key") == "cast" for a in p["profile"].get("animations") or [])

    @staticmethod
    def _extras(p: dict) -> list[str]:
        """Optional actions (run, sit, sleep, happy): the user's choice, else what the plan asks for."""
        chosen = (p.get("sprite2d") or {}).get("extras")
        if isinstance(chosen, list):
            return [a for a in chosen if a in sprite2d.EXTRA_ACTIONS]
        planned = {a.get("key") for a in p["profile"].get("animations") or []}
        return [a for a in sprite2d.EXTRA_ACTIONS if a in planned]

    @staticmethod
    def _views2d(p: dict) -> list[str]:
        """The drawings this character has: front 3/4 + back 3/4, plus a side view if chosen."""
        return sprite2d.views_for(bool((p.get("sprite2d") or {}).get("side")))

    @staticmethod
    def _detail(p: dict) -> int:
        try:
            v = int((p.get("sprite2d") or {}).get("detail") or (p.get("_group") or {}).get("detail") or 1)
        except (TypeError, ValueError):
            return 1
        return v if v in sprite2d.DETAIL else 1

    def _actions2d(self, p: dict) -> list[str]:
        return sprite2d.actions_for(self._magic(p), self._extras(p))

    def _desc_en(self, p: dict) -> str:
        """The character sentence every sprite prompt starts with, plus what is in its hands:
        nothing when the weapon is a separate layer, else the weapon description."""
        prof = p["profile"]
        desc = prof.get("description_en") or ", ".join(
            x for x in [prof.get("name_en", "character").replace("_", " "), prof.get("breed", "")] if x)
        g = p.get("_group") or {}
        if g.get("style_en"):                 # the project's look, shared by all its characters
            desc = desc.rstrip(". ") + ". Art style of this game: " + g["style_en"].rstrip(". ")
        if g.get("has_style_image"):
            desc = desc.rstrip(". ") + ". Also match the line art and colouring of the attached style reference image"
        w = self._weapon_cfg(p)
        if w["layer"]:
            return desc.rstrip(". ") + ". " + sprite2d.empty_hands(self._weapon_hand(p) or "r", w["attack_only"])
        held = w["desc_en"] or (weapons.TYPES[w["type"]]["en"] if w["type"] else "")
        return f"{desc}, holding {held}" if held else desc

    def _attack_style(self, p: dict) -> str | None:
        """The attack the body is drawn with: set by the character's weapon type (None = unarmed)."""
        t = self._weapon_cfg(p)["type"]
        return weapons.style_of(t) if t else None

    def _weapon_hand(self, p: dict) -> str | None:
        t = self._weapon_cfg(p)["type"]
        return weapons.TYPES[t]["hand"] if t else None

    @staticmethod
    def _weapon_cfg(p: dict) -> dict:
        w = dict(p.get("weapons") or {})
        w.setdefault("layer", False)          # True: weapon drawn as its own layer (swappable in the game)
        w.setdefault("desc_en", "")           # what the character holds, for prompts
        w.setdefault("items", [])             # [{id, name, grip: [x, y], size}]
        w.setdefault("active", None)
        w.setdefault("overrides", {})         # per-frame corrections, see weapons.anchors
        w.setdefault("attack_only", True)     # the layer shows only while attacking (idle/walk/hurt/die: hands free)
        # The character's weapon type: decides the attack the body is drawn with (prompts, pose guide).
        # "" = unarmed (the user's choice); missing = projects from before types: a staff if any weapon is set.
        if w.get("type") is None or (w["type"] and w["type"] not in weapons.TYPES):
            w["type"] = weapons.DEFAULT_TYPE if (w["items"] or w["desc_en"] or w["layer"]) else ""
        return w

    def _sheets2d(self, p: dict, d: Path, slug: str) -> dict:
        rep_path = self._rdir(slug) / "sprite" / "build.json"
        jitter = {}
        if rep_path.exists():
            jitter = json.loads(rep_path.read_text(encoding="utf-8")).get("jitter", {})
        desc, body, style = self._desc_en(p), self._body2d(p), self._attack_style(p)
        out = {}
        for action in self._actions2d(p):
            spec = sprite2d.ACTIONS[action]
            row = {"th": _(spec["th"]), "frames": spec["frames"], "views": {},
                   "prompt_both": sprite2d.prompt_both(action, desc, body, style=style, views=self._views2d(p))}
            for view in self._views2d(p):
                vspec = sprite2d.VIEWS2D[view]
                f = sprite2d.sheet_path(d, action, view)
                cell = {"th": _(vspec["th"]),"prompt": sprite2d.prompt(action, view, desc, body, style=style)}
                if f.exists():
                    cell["url"] = f"/projects/{slug}/sheets/{f.name}?v={int(f.stat().st_mtime)}"
                    cell["check"] = chk = jitter.get(f"{action}_{view}")
                    if chk and chk.get("issue_keys") is not None:      # in the viewer's language
                        chk["issues"] = [tr(k) for k in chk["issue_keys"]]
                    cell["turned"] = (p.get("sheet_flips") or {}).get(f"{action}_{view}") or []
                    cell["edited"] = bool(((p.get("sprite2d") or {}).get("frame_edits") or {}).get(f"{action}_{view}"))
                    cell["history"] = len(list(self._history_dir(d, action, view).glob("*.png")))
                    cell["review"] = ((p.get("art_review") or {}).get("rows") or {}).get(f"{action}_{view}")
                row["views"][view] = cell
            out[action] = row
        return out

    def add_sheet(self, slug: str, action: str, view: str, data: bytes) -> dict:
        if action != "all" and (action not in sprite2d.ACTIONS or (view not in sprite2d.VIEWS2D and view != "both")):
            raise ProjectError("Unknown action or view.")
        d = self._dir(slug)
        img = self._cutout_bytes(data)
        try:
            if action == "all":   # one image, every action: front 3/4 and back 3/4 row per action
                pp = self._read(d)
                pieces = sprite2d.split_all(img, self._actions2d(pp), self._views2d(pp))
                (d / "sheets").mkdir(exist_ok=True)
                img.save(d / "sheets" / "all_source.png")   # kept so the grid can be re-cut later
            elif view == "both":  # one image, a 2-row grid: front 3/4 on top, back 3/4 below
                vs = self._views2d(self._read(d))
                pieces = {(action, v): im for v, im in zip(vs, sprite2d.split_grid(img, len(vs)))}
            else:
                pieces = {(action, view): img}
        except ValueError as exc:
            raise ProjectError(str(exc)) from exc
        p = self._read(d)
        turned = p.setdefault("sheet_flips", {})
        def idle_of(v):                       # the facing reference for a walk strip
            src = pieces.get(("idle", v))
            if src is None and sprite2d.sheet_path(d, "idle", v).exists():
                src = Image.open(sprite2d.sheet_path(d, "idle", v)).convert("RGBA")
            return sprite2d.frames_of(src) if src is not None else None

        for (a, v), piece in sorted(pieces.items(), key=lambda kv: kv[0][0] != "idle"):
            f = sprite2d.sheet_path(d, a, v)
            f.parent.mkdir(exist_ok=True)
            fixed = (sprite2d.facing_flips(sprite2d.frames_of(piece), idle_of(v) if a == "walk" else None)
                     if a in sprite2d.IN_PLACE else [])
            if fixed:                         # frames the AI turned around: face them like the rest
                piece = sprite2d.mirror_frames(piece, fixed)
            turned[f"{a}_{v}"] = [i + 1 for i in fixed]
            self._keep_history(d, a, v)
            piece.save(f)
            (p.get("sprite2d") or {}).get("frame_edits", {}).pop(f"{a}_{v}", None)   # edits were for the old drawing
        self._write(d, p)
        return self.build_2d(slug)

    # ───────── drawing history (undo a replaced strip) ─────────
    HISTORY_KEEP = 5

    @staticmethod
    def _history_dir(d: Path, action: str, view: str) -> Path:
        return d / "sheets" / "history" / f"{action}_{view}"

    def _keep_history(self, d: Path, action: str, view: str) -> None:
        """Move the strip about to be replaced into its history (newest last), keeping a few."""
        f = sprite2d.sheet_path(d, action, view)
        if not f.exists():
            return
        h = self._history_dir(d, action, view)
        h.mkdir(parents=True, exist_ok=True)
        old = sorted(h.glob("*.png"))
        n = int(old[-1].stem) + 1 if old and old[-1].stem.isdigit() else 1
        shutil.move(str(f), str(h / f"{n:04d}.png"))
        for extra in sorted(h.glob("*.png"))[:-self.HISTORY_KEEP]:
            extra.unlink()

    def undo_sheet(self, slug: str, action: str, view: str) -> dict:
        """Bring back the drawing this strip had before the last replacement."""
        if action not in sprite2d.ACTIONS or view not in sprite2d.VIEWS2D:
            raise ProjectError("Unknown action or view.")
        d = self._dir(slug)
        old = sorted(self._history_dir(d, action, view).glob("*.png"))
        if not old:
            raise ProjectError(_("No earlier drawing to go back to."))
        shutil.move(str(old[-1]), str(sprite2d.sheet_path(d, action, view)))
        p = self._read(d)
        (p.get("sprite2d") or {}).get("frame_edits", {}).pop(f"{action}_{view}", None)
        self._write(d, p)
        return self.build_2d(slug)

    # ───────── per-frame editor ─────────
    def strip_frames(self, slug: str, action: str, view: str) -> dict:
        """What the frame editor shows: the drawing's frames (boxes on the strip image, feet), the
        current edit list, replacement frames, and which frames the sheet check found odd."""
        if action not in sprite2d.ACTIONS or view not in sprite2d.VIEWS2D:
            raise ProjectError("Unknown action or view.")
        d = self._dir(slug)
        p = self._read(d)
        f = sprite2d.sheet_path(d, action, view)
        if not f.exists():
            raise ProjectError(_("This action has no drawing yet."))
        boxes: list = []
        frames = sprite2d.frames_of(Image.open(f).convert("RGBA"), boxes)
        feet = []
        for fr, b in zip(frames, boxes):
            fx, fy, _h = sprite2d.feet_anchor(fr)
            feet.append([float(b[0] + fx), float(b[1] + fy)])
        key = f"{action}_{view}"
        edits = ((p.get("sprite2d") or {}).get("frame_edits") or {}).get(key)
        rep = self._rdir(slug) / "sprite" / "build.json"
        check = json.loads(rep.read_text(encoding="utf-8")).get("jitter", {}).get(key, {}) if rep.exists() else {}
        files = {}
        for e in edits or []:
            if "file" in e:
                ff = sprite2d.frame_edit_dir(d) / Path(str(e["file"])).name
                if ff.exists():
                    files[ff.name] = f"/projects/{slug}/sheets/frames/{ff.name}?v={int(ff.stat().st_mtime)}"
        return {"url": f"/projects/{slug}/sheets/{f.name}?v={int(f.stat().st_mtime)}",
                "boxes": [[int(v) for v in b] for b in boxes], "feet": feet,
                "height": int(sprite2d.feet_anchor(frames[0])[2]) if frames else 1,
                "edits": edits or [{"src": i, "flip": False, "dx": 0, "dy": 0, "hold": 1} for i in range(len(frames))],
                "edited": bool(edits), "files": files, "frame_ms": sprite2d.ACTIONS[action]["ms"],
                "loop": bool(sprite2d.ACTIONS[action].get("loop")),
                "odd_frames": check.get("odd_frames", []), "issues": check.get("issues", []),
                "history": len(list(self._history_dir(d, action, view).glob("*.png")))}

    def save_frame_edits(self, slug: str, action: str, view: str, edits: list | None) -> dict:
        """Store (or with None, drop) one strip's per-frame corrections, then rebuild the sprite."""
        if action not in sprite2d.ACTIONS or view not in sprite2d.VIEWS2D:
            raise ProjectError("Unknown action or view.")
        d = self._dir(slug)
        p = self._read(d)
        store = p.setdefault("sprite2d", {}).setdefault("frame_edits", {})
        key = f"{action}_{view}"
        if not edits:
            store.pop(key, None)
        else:
            clean = []
            for e in edits[:64]:
                if not isinstance(e, dict):
                    continue
                c = {"flip": bool(e.get("flip")), "dx": max(-200.0, min(200.0, float(e.get("dx", 0)))),
                     "dy": max(-200.0, min(200.0, float(e.get("dy", 0)))),
                     "hold": max(1, min(sprite2d.MAX_HOLD, int(e.get("hold", 1))))}
                if isinstance(e.get("file"), str):
                    c["file"] = Path(e["file"]).name
                else:
                    c["src"] = int(e.get("src", 0))
                clean.append(c)
            if not clean:
                raise ProjectError(_("A strip needs at least one frame."))
            store[key] = clean
        self._write(d, p)
        return self.build_2d(slug)

    def add_frame_image(self, slug: str, action: str, view: str, data: bytes) -> dict:
        """A single replacement frame (background removed): returns its file name for the edit list."""
        if action not in sprite2d.ACTIONS or view not in sprite2d.VIEWS2D:
            raise ProjectError("Unknown action or view.")
        d = self._dir(slug)
        img = self._cutout_bytes(data)
        out = sprite2d.frame_edit_dir(d)
        out.mkdir(parents=True, exist_ok=True)
        n = len(list(out.glob(f"{action}_{view}_*.png"))) + 1
        name = f"{action}_{view}_{n:03d}.png"
        img.save(out / name)
        return {"file": name, "url": f"/projects/{slug}/sheets/frames/{name}?v={int((out / name).stat().st_mtime)}"}

    def flip_sheet(self, slug: str, action: str, view: str) -> dict:
        """Mirror every frame of one strip in place: the AI drew the whole strip facing the wrong way."""
        d = self._dir(slug)
        f = sprite2d.sheet_path(d, action, view)
        if action not in sprite2d.ACTIONS or view not in sprite2d.VIEWS2D or not f.exists():
            raise ProjectError("This action has no drawing yet.")
        sprite2d.mirror_frames(Image.open(f).convert("RGBA")).save(f)
        return self.build_2d(slug)

    def remove_sheet(self, slug: str, action: str, view: str) -> dict:
        d = self._dir(slug)
        sprite2d.sheet_path(d, action, view).unlink(missing_ok=True)
        try:
            return self.build_2d(slug)
        except ValueError:
            return self.get(slug)

    def reference_board(self, slug: str, action: str = "all", view: str = "both") -> bytes:
        """One image to attach instead of two: the character (front 3/4 and back views) on top, the
        pose guide for the same prompt below, on white with a clear gap between them."""
        d = self._dir(slug)
        guide = Image.open(io.BytesIO(self.pose_guide(slug, action, view))).convert("RGB")
        refs = []
        for name in ("front_3q", "front", "back"):
            f = d / "views" / f"{name}.png"
            if f.exists() and not (name == "front" and refs):
                refs.append(Image.open(f).convert("RGBA"))
        if not refs and (d / "cutout.png").exists():
            refs.append(Image.open(d / "cutout.png").convert("RGBA"))
        top_h = max(320, min(720, guide.height // 3))
        scaled = []
        for im in refs:
            bbox = im.getchannel("A").getbbox() or (0, 0, im.width, im.height)
            im = im.crop(bbox)
            k = (top_h - 40) / im.height
            scaled.append(im.resize((max(1, round(im.width * k)), top_h - 40), Image.LANCZOS))
        gap, pad = 60, 40
        top_w = sum(im.width for im in scaled) + pad * (len(scaled) + 1)
        width = max(guide.width, top_w)
        board = Image.new("RGB", (width, top_h + gap + guide.height), (255, 255, 255))
        x = (width - top_w) // 2 + pad
        for im in scaled:
            board.paste(im, (x, 20), im)
            x += im.width + pad
        y_line = top_h + gap // 2
        for dx in range(width // 8, width - width // 8):
            board.putpixel((dx, y_line), (200, 200, 200))
        board.paste(guide, ((width - guide.width) // 2, top_h + gap))
        buf = io.BytesIO()
        board.save(buf, "PNG", optimize=True)
        return buf.getvalue()

    def pose_guide(self, slug: str, action: str = "all", view: str = "both") -> bytes:
        """Stick-figure grid matching the prompt for `action` (all / one action) and `view`."""
        from . import pose_guide
        p = self._read(self._dir(slug))
        if action == "all":
            slots = sprite2d.all_slots(self._actions2d(p))
        elif action in sprite2d.ACTIONS and (view == "both" or view in sprite2d.VIEWS2D):
            slots = [(action, v) for v in self._views2d(p) if view in ("both", v)]
        else:
            raise ProjectError("Unknown action or view.")
        w = self._weapon_cfg(p)
        hand = None if (w["layer"] and w["attack_only"]) else self._weapon_hand(p)   # free hands walk normally
        return pose_guide.png(slots, self._body2d(p), style=self._attack_style(p), hand=hand)

    # ───────── weapons as a separate layer ─────────
    def _weapon_layer(self, p: dict, d: Path) -> dict | None:
        """What sprite2d.build needs to lay the active weapon over every frame (None = no layer)."""
        w = self._weapon_cfg(p)
        item = next((i for i in w["items"] if i["id"] == w["active"]), None)
        return self._weapon_spec(p, d, item) if w["layer"] else None

    def _weapon_spec(self, p: dict, d: Path, item: dict | None) -> dict | None:
        f = d / "weapons" / f"{(item or {}).get('id')}.png"
        if not (item and f.exists()):
            return None
        return {"id": item["id"], "name": item.get("name") or item["id"], "image": Image.open(f).convert("RGBA"),
                "grip": tuple(item.get("grip") or weapons.DEFAULT_GRIP),
                "size": float(item.get("size") or weapons.DEFAULT_SIZE), "body": self._body2d(p),
                "overrides": self._weapon_cfg(p)["overrides"], "type": weapons.type_of(item),
                "actions": list(sprite2d.ATTACKS) if self._weapon_cfg(p)["attack_only"] else None}

    def _weapon_variants(self, p: dict, d: Path) -> list[dict]:
        """The other weapons, built as full sprites for Playtest's weapon picker."""
        w = self._weapon_cfg(p)
        if not w["layer"]:
            return []
        return [s for s in (self._weapon_spec(p, d, i) for i in w["items"] if i["id"] != w["active"]) if s]

    def _weapons_view(self, p: dict, d: Path, slug: str) -> dict:
        w = self._weapon_cfg(p)
        items = []
        for it in w["items"]:
            f = d / "weapons" / f"{it['id']}.png"
            if f.exists():
                items.append({**it, "url": f"/projects/{slug}/weapons/{f.name}?v={int(f.stat().st_mtime)}",
                              "squash": weapons.squash_of(weapons.type_of(it))})
        extras = sorted((d / "extras").glob("*.png")) if (d / "extras").is_dir() else []
        t = w["type"] or weapons.DEFAULT_TYPE
        return {**{k: w[k] for k in ("layer", "desc_en", "active", "type", "attack_only")}, "items": items,
                "overrides": len(w["overrides"]),
                "extras": [{"name": e.name, "url": f"/projects/{slug}/extras/{e.name}"} for e in extras],
                "types": {k: {"th": _(v["th"]), "style": v["style"], "style_th": _(weapons.STYLE_TH[v["style"]])}
                          for k, v in weapons.TYPES.items()},
                "prompt": weapons.prompt(w["desc_en"] or weapons.TYPES[t]["en"], t)}

    def _save_weapon(self, d: Path, p: dict, img: Image.Image, name: str, weapon_type: str | None = None,
                     upright: bool = False) -> None:
        """Add a weapon picture; upright=True when it is already trimmed and standing (a sample)."""
        try:
            img = img if upright else weapons.prepare(img)
        except ValueError as exc:
            raise ProjectError(str(exc)) from exc
        w = self._weapon_cfg(p)
        weapon_type = weapon_type if weapon_type in weapons.TYPES else (w["type"] or weapons.DEFAULT_TYPE)
        spec = weapons.TYPES[weapon_type]
        n = 1 + max([int(i["id"][1:]) for i in w["items"] if i["id"][1:].isdigit()] or [0])
        wid = f"w{n}"
        (d / "weapons").mkdir(exist_ok=True)
        img.save(d / "weapons" / f"{wid}.png")
        w["items"].append({"id": wid, "name": name or wid, "type": weapon_type, "grip": list(spec["grip"]),
                           "size": spec["size"]})
        w["active"] = wid if upright else (w["active"] or wid)      # a sample is added to be tried at once
        w["type"] = w["type"] or weapon_type
        p["weapons"] = w

    def _rebuild_quietly(self, slug: str) -> dict:
        try:
            return self.build_2d(slug)
        except ProjectError:                  # no action sheets yet: nothing to rebuild
            return self.get(slug)

    def add_weapon(self, slug: str, data: bytes, name: str = "", weapon_type: str = "") -> dict:
        d = self._dir(slug)
        p = self._read(d)
        self._save_weapon(d, p, self._cutout_bytes(data), name, weapon_type or None)
        self._write(d, p)
        return self._rebuild_quietly(slug)

    def add_sample_weapon(self, slug: str, weapon_type: str) -> dict:
        """A stand-in weapon of this type (weapons.sample), to try the type before drawing a real one."""
        if weapon_type not in weapons.TYPES:
            raise ProjectError("Unknown weapon type.")
        d = self._dir(slug)
        p = self._read(d)
        self._save_weapon(d, p, weapons.sample(weapon_type), _("{name} (sample)", name=_(weapons.TYPES[weapon_type]["th"])),
                          weapon_type, upright=True)
        p["weapons"]["layer"] = True          # trying a weapon = seeing it laid over the body
        self._write(d, p)
        return self._rebuild_quietly(slug)

    def weapon_from_extra(self, slug: str, extra: str) -> dict:
        """Use a prop found on the turnaround sheet (extras/) as a weapon."""
        d = self._dir(slug)
        f = d / "extras" / Path(extra).name
        if not f.exists():
            raise ProjectError("No such extra image.")
        p = self._read(d)
        self._save_weapon(d, p, Image.open(f).convert("RGBA"), Path(extra).stem)
        self._write(d, p)
        return self._rebuild_quietly(slug)

    def update_weapons(self, slug: str, patch: dict) -> dict:
        """patch: layer, desc_en, active, items: {id: {name, grip, size}}, overrides: {key: {...} | null}."""
        d = self._dir(slug)
        p = self._read(d)
        w = self._weapon_cfg(p)
        if "layer" in patch:
            w["layer"] = bool(patch["layer"])
        if "attack_only" in patch:
            w["attack_only"] = bool(patch["attack_only"])
        if isinstance(patch.get("desc_en"), str):
            w["desc_en"] = patch["desc_en"].strip()[:400]
        if patch.get("active") in {i["id"] for i in w["items"]}:
            w["active"] = patch["active"]
        if patch.get("type") in weapons.TYPES or patch.get("type") == "":
            w["type"] = patch["type"]
        for it in w["items"]:
            ch = (patch.get("items") or {}).get(it["id"]) or {}
            if isinstance(ch.get("name"), str):
                it["name"] = ch["name"][:60]
            if ch.get("type") in weapons.TYPES and ch["type"] != it.get("type"):
                it["type"] = ch["type"]                       # new type: its usual size and grip
                it["size"], it["grip"] = weapons.TYPES[ch["type"]]["size"], list(weapons.TYPES[ch["type"]]["grip"])
            if isinstance(ch.get("size"), (int, float)):
                it["size"] = min(3.0, max(0.1, float(ch["size"])))
            if isinstance(ch.get("grip"), list) and len(ch["grip"]) == 2:
                it["grip"] = [min(1.0, max(0.0, float(v))) for v in ch["grip"]]
        for key, o in (patch.get("overrides") or {}).items():
            if o is None:
                w["overrides"].pop(key, None)
            elif isinstance(o, dict):
                w["overrides"][key] = {k: (bool(v) if k == "behind" else float(v)) for k, v in o.items()
                                       if k in ("dx", "dy", "da", "behind")}
        p["weapons"] = w
        self._write(d, p)
        return self._rebuild_quietly(slug)

    def remove_weapon(self, slug: str, wid: str) -> dict:
        d = self._dir(slug)
        p = self._read(d)
        w = self._weapon_cfg(p)
        w["items"] = [i for i in w["items"] if i["id"] != wid]
        (d / "weapons" / f"{Path(wid).name}.png").unlink(missing_ok=True)
        if w["active"] == wid:
            w["active"] = w["items"][0]["id"] if w["items"] else None
        p["weapons"] = w
        self._write(d, p)
        return self._rebuild_quietly(slug)

    def weapon_frames(self, slug: str, action: str, view: str) -> dict:
        """Everything the hand editor draws for one strip: frame boxes and feet on the strip image,
        the standing height, and each frame's weapon anchor (guide + correction)."""
        if action not in sprite2d.ACTIONS or view not in sprite2d.VIEWS2D:
            raise ProjectError("Unknown action or view.")
        d = self._dir(slug)
        p = self._read(d)
        f = sprite2d.sheet_path(d, action, view)
        if not f.exists():
            raise ProjectError("This action has no drawing yet.")
        boxes: list = []
        frames = sprite2d.frames_of(Image.open(f).convert("RGBA"), boxes)
        feet = [[float(b[0] + fx), float(b[1] + fy)]
                for b, (fx, fy, _) in zip(boxes, sprite2d.strip_anchors(frames, action))]
        w = self._weapon_cfg(p)
        wtype = weapons.type_of(next((i for i in w["items"] if i["id"] == w["active"]), None))
        marks = weapons.anchors(action, view, len(frames), self._body2d(p), w["overrides"], wtype)
        return {"url": f"/projects/{slug}/sheets/{f.name}?v={int(f.stat().st_mtime)}",
                "boxes": [[int(v) for v in b] for b in boxes], "feet": feet,
                "height": int(sprite2d.feet_anchor(frames[0])[2]) if frames else 1,
                "keys": [weapons.anchor_key(action, view, i, wtype) for i in range(len(frames))],
                "anchors": marks}

    @staticmethod
    def _colour_ref(p: dict, d: Path) -> list | None:
        """Reference images the drawn frames' colours are locked to (on unless the user turned it off)."""
        if (p.get("sprite2d") or {}).get("colour_lock") is False:
            return None
        refs = [d / "cutout.png", d / "views" / "back.png"]
        return [Image.open(f).convert("RGBA") for f in refs if f.exists()] or None

    # ───────── colour variants (palette swaps) ─────────
    def _variants_view(self, p: dict, slug: str) -> dict:
        rep = self._rdir(slug) / "sprite" / "build.json"
        built = json.loads(rep.read_text(encoding="utf-8")) if rep.exists() else {}
        items = []
        for r in (p.get("sprite2d") or {}).get("variants") or []:
            f = self._rdir(slug) / "sprite" / "variants" / f"{r['id']}.act"
            items.append({**r, "sprite_id": self._sid(slug, f"sprite/variants/{r['id']}.act") if f.exists() else None})
        return {"items": items, "swatches": built.get("swatches", [])}

    def save_variants(self, slug: str, items: list) -> dict:
        """Replace the project's colour variants (ids kept or assigned v1, v2, ...) and rebuild."""
        from . import variants as var
        d = self._dir(slug)
        p = self._read(d)
        clean, used = [], set()
        for raw in (items or [])[:16]:
            if not isinstance(raw, dict):
                continue
            r = var.clean(raw)
            if not r["id"].isalnum() or r["id"] in used:
                r["id"] = next(f"v{n}" for n in range(1, 99) if f"v{n}" not in used)
            used.add(r["id"])
            clean.append(r)
        p.setdefault("sprite2d", {})["variants"] = clean
        self._write(d, p)
        return self._rebuild_quietly(slug)

    def variant_preview(self, slug: str, rule: dict) -> bytes:
        """The character image recoloured with a variant rule, small, for the live preview."""
        from . import variants as var
        d = self._dir(slug)
        f = d / "views" / "front_3q.png"
        im = Image.open(f if f.exists() else d / "cutout.png").convert("RGBA")
        im.thumbnail((220, 220))
        out = Image.fromarray(var.recolour_image(np.asarray(im), var.clean(rule)), "RGBA")
        buf = io.BytesIO()
        out.save(buf, "PNG")
        return buf.getvalue()

    # ───────── headgear (a swappable hat layer) and mounted versions ─────────
    @staticmethod
    def _hats_cfg(p: dict) -> dict:
        h = dict((p.get("sprite2d") or {}).get("hats") or {})
        h.setdefault("items", [])            # [{id, name, size, dx, dy}]
        h.setdefault("active", None)         # None = no headgear
        h.setdefault("desc_en", "")
        return h

    def _hat_layer(self, p: dict, d: Path) -> dict | None:
        h = self._hats_cfg(p)
        item = next((i for i in h["items"] if i["id"] == h["active"]), None)
        f = d / "hats" / f"{h['active']}.png"
        if not (item and f.exists()):
            return None
        return {"id": item["id"], "image": Image.open(f).convert("RGBA"), "size": float(item.get("size", 1.0)),
                "dx": float(item.get("dx", 0)), "dy": float(item.get("dy", 0))}

    def _hats_view(self, p: dict, d: Path, slug: str) -> dict:
        h = self._hats_cfg(p)
        items = []
        for it in h["items"]:
            f = d / "hats" / f"{it['id']}.png"
            if f.exists():
                items.append({**it, "url": f"/projects/{slug}/hats/{f.name}?v={int(f.stat().st_mtime)}"})
        return {"items": items, "active": h["active"], "desc_en": h["desc_en"],
                "prompt": self._hat_prompt(h["desc_en"] or _("a hat that suits this character"))}

    @staticmethod
    def _hat_prompt(desc: str) -> str:
        return (f"Game item sprite for a 2D isometric RPG, in a {sprite2d.ART_STYLE}: headgear, {desc}. "
                f"Draw ONLY the headgear as it sits on a head, seen from the front three-quarter view (turned slightly "
                f"to the left), upright, centred, nothing cropped. No head, no hair, no face, no character, no stand. "
                f"Same art style as the attached character: clean dark outlines, flat colours. Plain pure white "
                f"background, no shadow, no text, no labels, no frame. High resolution.")

    @staticmethod
    def _trim_item(img: Image.Image) -> Image.Image:
        """The biggest blob of a cut-out item, trimmed (no straightening: hats stay as drawn)."""
        from scipy import ndimage
        a = np.asarray(img.convert("RGBA"))
        m = a[..., 3] > 64
        if not m.any():
            raise ProjectError(_("The image is empty after background removal."))
        labels, n = ndimage.label(ndimage.binary_dilation(m, iterations=2))
        sizes = ndimage.sum(m, labels, range(1, n + 1))
        keep = (labels == int(np.argmax(sizes)) + 1) & m
        arr = a.copy()
        arr[..., 3] = np.where(keep, arr[..., 3], 0)
        ys, xs = np.nonzero(keep)
        return Image.fromarray(arr, "RGBA").crop((xs.min(), ys.min(), xs.max() + 1, ys.max() + 1))

    def add_hat(self, slug: str, data: bytes, name: str = "") -> dict:
        d = self._dir(slug)
        p = self._read(d)
        img = self._trim_item(self._cutout_bytes(data))
        h = self._hats_cfg(p)
        n = 1 + max([int(i["id"][1:]) for i in h["items"] if i["id"][1:].isdigit()] or [0])
        hid = f"h{n}"
        (d / "hats").mkdir(exist_ok=True)
        img.save(d / "hats" / f"{hid}.png")
        h["items"].append({"id": hid, "name": (name or hid)[:60], "size": 1.0, "dx": 0.0, "dy": 0.0})
        h["active"] = hid
        p.setdefault("sprite2d", {})["hats"] = h
        self._write(d, p)
        return self._rebuild_quietly(slug)

    def update_hats(self, slug: str, patch: dict) -> dict:
        """patch: active (id or null), desc_en, items: {id: {name, size, dx, dy}}."""
        d = self._dir(slug)
        p = self._read(d)
        h = self._hats_cfg(p)
        if "active" in patch and (patch["active"] is None or patch["active"] in {i["id"] for i in h["items"]}):
            h["active"] = patch["active"]
        if isinstance(patch.get("desc_en"), str):
            h["desc_en"] = patch["desc_en"].strip()[:300]
        for it in h["items"]:
            ch = (patch.get("items") or {}).get(it["id"]) or {}
            if isinstance(ch.get("name"), str):
                it["name"] = ch["name"][:60]
            for k, lo, hi in (("size", 0.3, 3.0), ("dx", -0.5, 0.5), ("dy", -0.5, 0.5)):
                if isinstance(ch.get(k), (int, float)):
                    it[k] = min(hi, max(lo, float(ch[k])))
        p.setdefault("sprite2d", {})["hats"] = h
        self._write(d, p)
        return self._rebuild_quietly(slug)

    def remove_hat(self, slug: str, hid: str) -> dict:
        d = self._dir(slug)
        p = self._read(d)
        h = self._hats_cfg(p)
        h["items"] = [i for i in h["items"] if i["id"] != hid]
        (d / "hats" / f"{Path(hid).name}.png").unlink(missing_ok=True)
        if h["active"] == hid:
            h["active"] = None
        p.setdefault("sprite2d", {})["hats"] = h
        self._write(d, p)
        return self._rebuild_quietly(slug)

    def make_mounted(self, slug: str, mount: str) -> dict:
        """A new project for the same character riding a mount: same reference images and look, the
        description says what it rides. Its action sheets are drawn like any character's."""
        mount = (mount or "").strip()[:200]
        if not mount:
            raise ProjectError(_("Describe the mount first, e.g. a large brown riding bird."))
        src = self._dir(slug)
        p = self._read(src)
        base, n = f"{slug}_mounted", 2
        new = base
        while self.layout.exists(new):
            new, n = f"{base}_{n}", n + 1
        d = src.parent / new
        d.mkdir(parents=True)
        self.layout.forget()
        for name in ("concept.png", "cutout.png"):
            if (src / name).exists():
                shutil.copy2(src / name, d / name)
        for folder in ("views", "extras"):
            if (src / folder).is_dir():
                shutil.copytree(src / folder, d / folder)
        (d / "model").mkdir(exist_ok=True)
        q = json.loads(json.dumps(p))
        q["slug"], q["created"], q["mounted_from"] = new, time.strftime("%Y-%m-%dT%H:%M:%S"), slug
        prof = q["profile"]
        prof["name_th"] = f"{prof.get('name_th') or slug} · {_('mounted')}"
        desc = (prof.get("description_en") or "").rstrip(". ")
        prof["description_en"] = f"{desc}, riding {mount}" if desc else f"a character riding {mount}"
        keep = {k: v for k, v in (q.get("sprite2d") or {}).items() if k in ("body", "side", "colour_lock", "detail")}
        q["sprite2d"] = keep
        q["ai_pending"] = False
        q.pop("sheet_flips", None)
        self._write(d, q)
        return self.get(new)

    def build_2d(self, slug: str) -> dict:
        d = self._dir(slug)
        p = self._read(d)
        try:
            sprite2d.build(d, self._rdir(slug).parent, slug, float(p["profile"].get("height_m") or 1.6),
                           weapon=self._weapon_layer(p, d), variants=self._weapon_variants(p, d),
                           actions=self._actions2d(p), colour_ref=self._colour_ref(p, d),
                           frame_edits=(p.get("sprite2d") or {}).get("frame_edits"), views=self._views2d(p),
                           colour_variants=(p.get("sprite2d") or {}).get("variants"), hat=self._hat_layer(p, d),
                           detail=self._detail(p))
        except ValueError as exc:
            raise ProjectError(str(exc)) from exc
        return self.get(slug)

    # ───────── views (turnaround) ─────────
    @staticmethod
    def _save_extras(d: Path, extras) -> None:
        """Props found on a turnaround sheet (e.g. an equipment inset) - kept for weapon layers later."""
        if not extras:
            return
        out = d / "extras"
        out.mkdir(exist_ok=True)
        start = len(list(out.glob("*.png")))
        for i, im in enumerate(extras):
            im.save(out / f"extra_{start + i:02d}.png")

    def _views(self, d: Path, slug: str) -> dict:
        vd = d / "views"
        out = {}
        for v in VIEWS:
            f = vd / f"{v}.png"
            if f.exists():
                out[v] = f"/projects/{slug}/views/{v}.png?v={int(f.stat().st_mtime)}"
        return out

    def _cutout_bytes(self, data: bytes) -> Image.Image:
        s = self.settings
        return process_character(
            data, remove_bg=True, trim=True, padding=4, model=s.rembg_model, post_process=s.rembg_post_process,
            alpha_threshold=s.alpha_threshold, max_pixels=s.max_image_pixels,
        ).image

    @staticmethod
    def _not_an_action_sheet(img: Image.Image) -> None:
        n = figure_count(img)
        if n >= ACTION_SHEET_MIN:
            raise ProjectError(_("This looks like a sheet of poses ({n} figures), not a character or a turnaround. "
                                 "Drop AI-drawn poses in step 4 (AI drawings), under the prompt that asked for them.", n=n))

    def set_view(self, slug: str, view: str, data: bytes) -> dict:
        if view not in VIEWS:
            raise ProjectError(f"Unknown view '{view}'.")
        d = self._dir(slug)
        img = self._cutout_bytes(data)
        self._not_an_action_sheet(img)
        (d / "views").mkdir(exist_ok=True)
        img.save(d / "views" / f"{view}.png")
        if view == "front":
            img.save(d / "cutout.png")  # the front view doubles as the portrait
        self._auto_gen3d(slug)
        return self.get(slug)

    def remove_view(self, slug: str, view: str) -> dict:
        d = self._dir(slug)
        (d / "views" / f"{view}.png").unlink(missing_ok=True)
        return self.get(slug)

    def split_sheet(self, slug: str, data: bytes) -> dict:
        """Cut a turnaround sheet into figures; returns them for the user to assign."""
        d = self._dir(slug)
        cut = self._cutout_bytes(data)
        self._not_an_action_sheet(cut)
        pieces = d / "views" / "_pieces"
        shutil.rmtree(pieces, ignore_errors=True)
        pieces.mkdir(parents=True)
        figures, extras = split_sheet(cut)
        self._save_extras(d, extras)
        if not figures:
            raise ProjectError("No figure found in the image.")
        stamp = int(time.time())
        for i, fig in enumerate(figures):
            fig.save(pieces / f"{i}.png")
        return {"pieces": [f"/projects/{slug}/views/_pieces/{i}.png?v={stamp}" for i in range(len(figures))],
                "assignment": default_assignment(len(figures))}

    def assign_pieces(self, slug: str, assignment: dict) -> dict:
        d = self._dir(slug)
        pieces = d / "views" / "_pieces"
        for idx, view in assignment.items():
            src = pieces / f"{int(idx)}.png"
            if view in VIEWS and src.exists():
                shutil.copyfile(src, d / "views" / f"{view}.png")
                if view == "front":
                    shutil.copyfile(src, d / "cutout.png")
        shutil.rmtree(pieces, ignore_errors=True)
        self._auto_gen3d(slug)
        return self.get(slug)

    def swap_views(self, slug: str, a: str, b: str) -> dict:
        d = self._dir(slug)
        fa, fb = d / "views" / f"{a}.png", d / "views" / f"{b}.png"
        tmp = d / "views" / "_swap.png"
        if fa.exists():
            fa.replace(tmp)
        if fb.exists():
            fb.replace(fa)
        if tmp.exists():
            tmp.replace(fb)
        if "front" in (a, b) and (d / "views" / "front.png").exists():
            shutil.copyfile(d / "views" / "front.png", d / "cutout.png")
        self._auto_gen3d(slug)
        return self.get(slug)

    def _invalidate_model_check(self, d: Path) -> None:
        shutil.rmtree(d / "preview", ignore_errors=True)
        p = self._read(d)
        p.setdefault("notes", {}).pop("model_ok", None)
        self._write(d, p)

    # ───────── model check (before rigging) ─────────
    def _model_check(self, p: dict, d: Path) -> dict | None:
        rep_path = d / "preview" / "model_report.json"
        if not rep_path.exists():
            return None
        rep = json.loads(rep_path.read_text(encoding="utf-8"))
        v = int(rep_path.stat().st_mtime)
        rep["preview_urls"] = {k: f"/projects/{p['slug']}/preview/{f}?v={v}" for k, f in rep["previews"].items()}
        want_h = float(p["profile"].get("height_m") or 0)
        orig = rep.get("original", {})
        checks = []

        def add(level, text):
            checks.append({"level": level, "text": text})

        add("ok", _("{meshes} mesh · {tris} triangles (sprites ignore the poly count; it only affects render time)",
                    meshes=rep["meshes"], tris=f"{rep['triangles']:,}"))
        if rep["textures"]:
            add("ok", _("{n} texture file(s)", n=len(rep["textures"])))
        elif rep["materials"]:
            add("warn", _("Has materials but no textures: colours may look flatter than the concept"))
        else:
            add("bad", _("No textures/materials: the render will be grey. Export again with textures"))
        h = orig.get("height_m") or 0
        if want_h and h:
            ratio = h / want_h
            if 0.5 <= ratio <= 2:
                add("ok", _("Height {h} m (profile {want} m); scaled to {want} m when rendering",
                            h=f"{h:.2f}", want=f"{want_h:.2f}"))
            else:
                add("warn", _("Scale in the file is off: {h} units tall but the profile says {want} m (usually cm or no units); "
                              "scaled to {want} m for you", h=f"{h:.3g}", want=f"{want_h:.2f}"))
        off = max(abs(orig.get("feet_z") or 0), *(abs(c) for c in (orig.get("centre_offset_m") or [0])))
        if off > 0.05 * max(h, 0.01):
            add("info", _("The origin is not at the feet (off by {off} units); the feet were put on the ground automatically",
                          off=f"{off:.2f}"))
        else:
            add("ok", _("The feet are at the origin"))
        arm = rep.get("armature")
        if not arm:
            add("info", _("No bones yet: normal for an Image-to-3D model; rig it in the next step"))
        elif not rep.get("skinned_meshes"):
            add("bad", _("{n} bones, but the mesh is not bound to them: the poses won't move it", n=arm["bones"]))
        else:
            add("ok", _("{n} bones, mesh bound", n=arm["bones"]))
        if rep.get("actions"):
            add("ok", _("Animations: {names}", names=", ".join(rep["actions"])))
        add("check", _("Check the 8 directions: south (S) must show the character's face, and the shape and colours must "
                       "match the concept. If you see the back or a side, change 'Model faces'"))
        rep["checks"] = checks
        rep["confirmed"] = bool(p.get("notes", {}).get("model_ok"))
        return rep

    def _model_files(self, d: Path) -> list[dict]:
        files = []
        for f in sorted((d / "model").glob("*")):
            if f.suffix.lower() in MODEL_EXTS:
                files.append({"name": f.name, "size": f.stat().st_size, "kind": self._file_kind(f)})
        return files

    @staticmethod
    def _file_kind(f: Path) -> str:
        ext = f.suffix.lower()
        if ext == ".blend":
            return "blend"
        if ext == ".obj":
            return "static"  # OBJ can't carry a rig or animation
        return "animation" if "@" in f.stem else "model"

    def _render_info(self, slug: str) -> dict | None:
        d = self._rdir(slug)
        m = d / "render_manifest.json"
        if not m.exists():
            report = d / "sprite" / "build.json"
            if not report.exists():
                return None
            built = json.loads(report.read_text(encoding="utf-8"))
            built["sprite_id"] = self._sid(slug, f"sprite/{slug}.act")
            return {"procedural": False, "source": built.get("source", "2d"),
                    "actions": [a["action"] for a in built.get("action_map", [])],
                    "lab_config": (d / "lab_config.json").exists(), "created": None, "sprite": built}
        man = json.loads(m.read_text(encoding="utf-8"))
        built = d / "sprite" / "build.json"
        info = {
            "procedural": bool(man.get("procedural")),
            "actions": [a["name"] for a in man["actions"]],
            "lab_config": (d / "lab_config.json").exists(),
            "created": man.get("created"),
            "sprite": json.loads(built.read_text(encoding="utf-8")) if built.exists() else None,
        }
        if info["sprite"]:
            info["sprite"]["sprite_id"] = self._sid(slug, f"sprite/{slug}.act")
        return info

    def export_spr(self, slug: str) -> dict:
        rd = self._rdir(slug)
        if needs_pack(rd):
            pack(rd)
        if not (rd / "character.json").exists():
            raise ProjectError("Render the character first.")
        spr_export(rd)
        return self.get(slug)

    # ───────── step checklist ─────────
    def _steps(self, p: dict, d: Path) -> list[dict]:
        prof = p["profile"]
        files = p["model_files"]
        renders = p["renders"]
        humanoid = prof.get("body_type") == "humanoid"
        planned = [a["key"] for a in prof.get("animations", [])]
        rendered = renders["actions"] if renders else []

        def has_anim(key: str) -> bool:
            pat = re.compile(ANIM_ALIASES.get(key, re.escape(key)), re.I)
            return any(pat.search(a) for a in rendered)

        anim_status = {k: has_anim(k) for k in planned}
        mc = p.get("model_check")
        method = prof.get("_method")

        def views_text(keys) -> str:
            return ", ".join(_(VIEW_LABELS[v]) for v in keys)

        steps = [
            {"id": "upload", "title": _("Upload image + remove background"), "where": "local", "done": True},
            {"id": "classify", "title": _("Classify the character (type, element, build, required animations)"),
             "where": "ai" if p.get("ai_pending") else "local",
             "done": not p.get("ai_pending") and (method in ("claude", "claude_code") or bool(p.get("notes", {}).get("profile_confirmed"))),
             "detail": (_('Waiting for your AI assistant: type "classify character {slug}" in its chat', slug=p["slug"]) if p.get("ai_pending")
                        else _("Classified by an AI assistant (chat)") if method == "claude_code"
                        else _("Classified by AI (API)") if method == "claude"
                        else _("A guess without AI: check it and confirm"))},
            {"id": "views", "title": _("Every view (front / side / back)"), "where": "external",
             "done": all(v in p.get("views", {}) for v in REQUIRED_VIEWS),
             "detail": _("Complete: {items}", items=views_text(p.get("views", {}))) if all(v in p.get("views", {}) for v in REQUIRED_VIEWS)
             else _("Missing: {items}", items=views_text(v for v in REQUIRED_VIEWS if v not in p.get("views", {})))},
            {"id": "model3d", "title": _("Build a 3D model from the image (Image-to-3D)"),
             "where": "local" if p.get("gen3d_available") else "external",
             "done": bool(files),
             "detail": (_("Can be built locally with the button (auto-build is off)") if p.get("gen3d_available")
                        else _("Meshy / Tripo / Hunyuan3D, then upload the .fbx / .glb into this character"))},
            {"id": "modelcheck", "title": _("Check the 3D model against the concept"), "where": "blender",
             "done": bool(mc and mc["confirmed"]),
             "detail": (_("Waiting for the check…") if files and not mc else
                        _("Look at the 8 directions in the Board, then press 'Model is correct'") if mc and not mc["confirmed"] else
                        _("Confirmed") if mc else _("Upload a model first"))},
            {"id": "rig", "title": _("Rig + animations"),
             "where": "external",
             "done": bool(mc and mc.get("armature") and mc.get("actions")) or bool(rendered and not (renders or {}).get("procedural")),
             "detail": (_("Using bone-less animation for now · rig it so the legs really step: ") if renders and renders.get("procedural") else "")
                       + (_("Mixamo (humanoid)") if humanoid else _("Blender Rigify / Auto-Rig Pro (quadruped)"))},
            {"id": "render", "title": _("Render 8 directions (Blender)"), "where": "blender", "done": bool(rendered),
             "detail": _("Animations: {names}", names=", ".join(rendered) or "–")
                       + (_(" (bone-less animation: the whole body moves, like a bouncing blob)") if renders and renders.get("procedural") else "")},
            {"id": "anims", "title": _("All planned animations"), "where": "external" if not rendered else "blender",
             "done": bool(planned) and all(anim_status.values()),
             "detail": " · ".join(f"{k} {'✓' if v else '✗'}" for k, v in anim_status.items())},
            {"id": "sprite", "title": _("Convert to sprites (.spr / .act)"), "where": "local",
             "done": bool(renders and renders.get("sprite")),
             "detail": (lambda r: _("{types} actions × 8 directions · {images} images · {h}px tall · palette {colors} colours",
                                    types=r["types"], images=r["images"], h=r["height_px"], colors=r["palette_colors"])
                        if r else _("Follows the render automatically"))(renders and renders.get("sprite"))},
            {"id": "tune", "title": _("Tune speed / hit frame in Playtest"), "where": "local",
             "done": bool(renders and renders["lab_config"])},
            {"id": "vfx", "title": _("Skill effects"), "where": "local", "done": False,
             "detail": _("{n} skills planned · skill effects are not part of this version", n=len(prof.get("skills", [])))},
            {"id": "export", "title": _("Export for the game"), "where": "local", "done": bool(p.get("notes", {}).get("exported")),
             "detail": "zip: sheets + character.json + lab_config + profile"},
        ]
        if p.get("mode", "2d") == "2d":
            sheets = p.get("sheets2d", {})
            done_cells = [(a, v) for a, row in sheets.items() for v, c in row["views"].items() if c.get("url")]
            total = sum(len(row["views"]) for row in sheets.values())
            missing = [f"{row['th']}-{c['th']}" for a, row in sheets.items() for v, c in row["views"].items() if not c.get("url")]
            warn = [f"{a}_{v}" for a, v in done_cells if (sheets[a]["views"][v].get("check") or {}).get("ok") is False]
            keep = {"upload", "classify", "views", "export"}
            steps = [s for s in steps if s["id"] in keep]
            built = renders and renders.get("sprite")
            views = p.get("views", {})
            for s in steps:
                if s["id"] == "classify":
                    s["title"] = _("Character info + description")
                    s["done"] = s["done"] or (not p.get("ai_pending") and bool(prof.get("description_en")))
                if s["id"] == "views":
                    s["title"] = _("Reference images (a back view makes it more accurate · optional)")
                    s["done"] = "front" in views
                    s["detail"] = _("Have: {items}", items=views_text(views)) if views else _("No images yet")
                if s["id"] == "export":
                    s["title"] = _("Download for the game")
                    s["done"] = bool(built)
                    s["detail"] = (_("Web game (atlas + JSON) · all images (zip) · .spr / .act") if built
                                   else _("Available once there are action images"))
            insert_at = [i for i, s in enumerate(steps) if s["id"] == "views"][0] + 1
            steps[insert_at:insert_at] = [
                {"id": "sheets", "title": _("Actions drawn by AI ({done}/{total} images)", done=len(done_cells), total=total),
                 "where": "external",
                 "done": not missing,
                 "detail": (_("Complete") if not missing
                            else _("Missing: {items}", items=", ".join(missing[:4]) + (" …" if len(missing) > 4 else "")))
                           + (_(" · ⚠ redraw: {items}", items=", ".join(warn)) if warn else "")
                           + (_(" · built {types} actions × 8 directions", types=built["types"]) if built else "")},
            ]
        # The next thing to do = first step not done that can actually be done.
        for s in steps:
            if s["id"] == "rig" and renders and renders.get("procedural"):
                continue  # optional upgrade once the procedural result exists
            if not s["done"] and s["where"] != "missing":
                s["next"] = True
                break
        p["anim_status"] = anim_status
        return steps

    # ───────── Blender render job ─────────
    def render_command(self, slug: str) -> list[str]:
        d = self._dir(slug)
        p = self._read(d)
        mc = self._model_check(p, d)
        if mc is not None and not (mc.get("armature") and mc.get("actions")):
            return self._procedural_command(slug, p, d)
        blender = find_blender()
        if not blender:
            raise ProjectError("Blender not found. Install Blender 4.2+ or set BLENDER_PATH.")
        files = sorted((d / "model").glob("*"))
        blends = [f for f in files if f.suffix.lower() == ".blend"]
        script = str(Path(__file__).resolve().parent.parent / "blender" / "render_8dir.py")
        r = p.get("render", {})
        args = ["--name", slug, "--out", str(self._rdir(slug).parent), "--ppm", str(r.get("ppm", 64)),
                "--size", str(r.get("size", 256)), "--step", str(r.get("step", 2))]
        if r.get("mirror"):
            args.append("--mirror")
        args += self._common_model_args(p)
        if blends:
            return [blender, "-b", str(blends[0]), "-P", script, "--", *args]
        models = sorted([f for f in files if f.suffix.lower() in (".fbx", ".glb", ".gltf") and "@" not in f.stem],
                        key=lambda f: f.stem.endswith("_mesh"))  # a rigged upload beats the generated mesh
        anims = [f for f in files if f.suffix.lower() in (".fbx", ".glb", ".gltf") and "@" in f.stem]
        if not models:
            raise ProjectError("Upload the rigged model first (a .blend, or an .fbx/.glb without '@' in the name).")
        return [blender, "-b", "-P", script, "--", *args, "--files", str(models[0]), *map(str, anims)]

    def _common_model_args(self, p: dict) -> list[str]:
        r = p.get("render", {})
        args = [f"--facing={r.get('facing', '-Y')}"]  # "=" form: argparse reads a bare "-Y" as an option
        h = float(p["profile"].get("height_m") or 0)
        if r.get("normalize_height", True) and h > 0:
            args += ["--height", f"{h:g}"]
        return args

    def inspect_command(self, slug: str) -> list[str]:
        d = self._dir(slug)
        p = self._read(d)
        blender = find_blender()
        if not blender:
            raise ProjectError("Blender not found. Install Blender 4.2+ or set BLENDER_PATH.")
        files = sorted(f for f in (d / "model").glob("*") if f.suffix.lower() in MODEL_EXTS)
        if not files:
            raise ProjectError("Upload a model first.")
        script = str(Path(__file__).resolve().parent.parent / "blender" / "inspect_model.py")
        args = ["--out", str(d / "preview"), *self._common_model_args(p)]
        blends = [f for f in files if f.suffix.lower() == ".blend"]
        if blends:
            return [blender, "-b", str(blends[0]), "-P", script, "--", *args]
        base = sorted([f for f in files if "@" not in f.stem] or files, key=lambda f: f.stem.endswith("_mesh"))
        return [blender, "-b", "-P", script, "--", *args, "--files", str(base[0])]

    # ───────── local image-to-3D (Hunyuan3D-2mv + projected texture) ─────────
    def gen3d_available(self) -> bool:
        return Path(self.settings.hunyuan_python).exists() and bool(find_blender())

    def gen3d_commands(self, slug: str) -> list[list[str]]:
        d = self._dir(slug)
        p = self._read(d)
        if not self.gen3d_available():
            raise ProjectError("Local 3D generation is not installed (Hunyuan3D-2 env or Blender missing).")
        views = d / "views"
        if not (views / "front.png").exists():
            raise ProjectError("Need at least the front view.")
        tools = Path(__file__).resolve().parent.parent
        gen = d / "generated"
        shape = gen / "shape.glb"
        cmd1 = [str(self.settings.hunyuan_python), str(tools / "tools3d" / "gen_shape.py"),
                "--front", str(views / "front.png"), "--out", str(shape)]
        if (views / "side.png").exists():
            cmd1 += ["--left", str(views / "side.png")]
        if (views / "back.png").exists():
            cmd1 += ["--back", str(views / "back.png")]
        h = float(p["profile"].get("height_m") or 0)
        cmd2 = [find_blender(), "-b", "-P", str(tools / "blender" / "project_texture.py"), "--",
                "--mesh", str(shape), "--views", str(views), "--out", str(gen / f"{slug}_mesh")]
        if h:
            cmd2 += ["--height", f"{h:g}"]
        return [cmd1, cmd2]

    def start_gen3d(self, slug: str) -> dict:
        d = self._dir(slug)
        p = self._read(d)
        p["gen3d_views"] = sorted(v.stem for v in (d / "views").glob("*.png") if v.stem in ("front", "side", "back"))
        self._write(d, p)

        def after():
            src = d / "generated" / f"{slug}_mesh.fbx"
            if src.exists():
                shutil.copyfile(src, d / "model" / src.name)
                self._invalidate_model_check(d)
                self.start_inspect(slug)  # flow on: check the new model right away

        return self._start_job(slug, "gen3d", self.gen3d_commands(slug), after=after)

    def _auto_gen3d(self, slug: str) -> None:
        """Workflow: once front + side + back exist and there is no model yet, build one (if enabled)."""
        if not self.settings.auto_local_3d:
            return
        d = self._dir(slug)
        p = self._read(d)
        views = d / "views"
        job = self._jobs.get((slug, "gen3d"))
        files = self._model_files(d)
        only_generated = bool(files) and all(Path(f["name"]).stem.endswith("_mesh") for f in files)
        have = sorted(v for v in ("front", "side", "back") if (views / f"{v}.png").exists())
        better_views = only_generated and have != sorted(p.get("gen3d_views", []))
        if (all((views / f"{v}.png").exists() for v in REQUIRED_VIEWS) and (not files or better_views)
                and self.gen3d_available() and not (job and job["state"] == "running")):
            try:
                self.start_gen3d(slug)
            except ProjectError:
                pass

    def flip_view(self, slug: str, view: str) -> dict:
        d = self._dir(slug)
        f = d / "views" / f"{view}.png"
        if f.exists():
            ImageOps_mirror(f)
        return self.get(slug)

    def _procedural_command(self, slug: str, p: dict, d: Path) -> list[str]:
        """Model without bones: whole-body actions (squash, hop, lunge) until it is rigged."""
        blender = find_blender()
        files = sorted([f for f in (d / "model").glob("*") if f.suffix.lower() in MODEL_EXTS and "@" not in f.stem],
                       key=lambda f: f.stem.endswith("_mesh"))
        if not files:
            raise ProjectError("Upload or generate a model first.")
        r = p.get("render", {})
        planned = {a["key"] for a in p["profile"].get("animations", [])}
        extras = [e for e in ("happy", "sit", "sleep") if e in planned]
        script = str(Path(__file__).resolve().parent.parent / "blender" / "render_procedural.py")
        cmd = [blender, "-b", "-P", script, "--", "--name", slug, "--out", str(self._rdir(slug).parent),
               "--files", str(files[0]), "--size", str(r.get("size", 256)), "--ppm", str(r.get("ppm", 64)),
               *self._common_model_args(p)]
        if extras:
            cmd += ["--extras", ",".join(extras)]
        if r.get("mirror"):
            cmd.append("--mirror")
        return cmd

    def start_inspect(self, slug: str) -> dict:
        def after():
            # Workflow: an unrigged model goes straight on to a procedural render -> sprite,
            # so every upload ends with a result. A rigged model waits for the user's confirmation.
            d = self._dir(slug)
            p = self._read(d)
            mc = self._model_check(p, d)
            job = self._jobs.get((slug, "render"))
            if mc and not (mc.get("armature") and mc.get("actions")) and not (job and job["state"] == "running"):
                try:
                    self.start_render(slug)
                except ProjectError:
                    pass

        return self._start_job(slug, "inspect", self.inspect_command(slug), after=after)

    def start_render(self, slug: str) -> dict:
        def after():
            rd = self._rdir(slug)
            if needs_pack(rd):
                pack(rd)  # sheets ready for the board / Playtest / export
            spr_export(rd)  # the goal: a .spr/.act sprite, viewable in Sprite Inspector

        return self._start_job(slug, "render", self.render_command(slug), after=after)

    def _start_job(self, slug: str, kind: str, cmd, after) -> dict:
        """cmd: one command, or a list of commands run one after another (stops at the first failure)."""
        cmds = cmd if cmd and isinstance(cmd[0], list) else [cmd]
        with self._lock:
            job = self._jobs.get((slug, kind))
            if job and job["state"] == "running":
                raise ProjectError(f"A {kind} job is already running for this character.")
            log_path = self._dir(slug) / f"{kind}.log"
            job = {"state": "running", "started": time.time(), "cmd": cmds, "log": str(log_path), "returncode": None}
            self._jobs[(slug, kind)] = job

        def run():
            with open(log_path, "w", encoding="utf-8", errors="replace") as logf:
                for c in cmds:
                    logf.write(" ".join(f'"{a}"' if " " in a else a for a in c) + "\n\n")
                    logf.flush()
                    proc = subprocess.Popen(c, stdout=logf, stderr=subprocess.STDOUT)
                    job["pid"] = proc.pid
                    job["returncode"] = proc.wait()
                    if job["returncode"] != 0:
                        break
            if job["returncode"] == 0 and after:
                after()
            job["state"] = "done" if job["returncode"] == 0 else "failed"
            job["ended"] = time.time()

        threading.Thread(target=run, daemon=True).start()
        return self.job_status(slug, kind)

    def job_status(self, slug: str, kind: str = "render") -> dict | None:
        job = self._jobs.get((slug, kind))
        try:
            log_path = self._dir(slug) / f"{kind}.log"
        except ProjectError:
            return None
        tail = []
        if log_path.exists():
            lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
            tail = [ln for ln in lines if any(t in ln for t in ("[render_8dir]", "[inspect_model]", "[gen_shape]", "[project_texture]", "Error", "Traceback"))][-12:]
        if not job:
            return {"state": "idle", "tail": tail} if tail else None
        return {"state": job["state"], "returncode": job["returncode"],
                "elapsed": round((job.get("ended") or time.time()) - job["started"]), "tail": tail}

    # ───────── export ─────────
    def export_zip(self, slug: str) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            self._export_into(z, slug, f"{slug}/", strict=True)
        return buf.getvalue()

    def export_group_zip(self, gid: str) -> tuple[bytes, str]:
        """Every built character of a project in one zip: <project>/<character>/..."""
        try:
            self.layout.read_group(gid)
        except LayoutError as exc:
            raise ProjectError(str(exc)) from None
        buf = io.BytesIO()
        n = 0
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for slug in self.layout.characters(gid):
                n += self._export_into(z, slug, f"{gid}/{slug}/", strict=False)
        if not n:
            raise ProjectError(_("No character in this project is built yet."))
        return buf.getvalue(), f"{gid}.zip"

    def _export_into(self, z: zipfile.ZipFile, slug: str, prefix: str, strict: bool) -> int:
        d = self._dir(slug)
        p = self._read(d)
        rd = self._rdir(slug)
        if needs_pack(rd):
            pack(rd)
        if not (rd / "character.json").exists() and not (rd / "sprite" / f"{slug}.act").exists():
            if strict:
                raise ProjectError("Nothing built yet - add action sheets (2D) or render the model (3D) first.")
            return 0
        z.write(d / "concept.png", f"{prefix}concept.png")
        z.write(d / "cutout.png", f"{prefix}portrait.png")
        profile = {k: v for k, v in p["profile"].items() if not k.startswith("_")}
        z.writestr(f"{prefix}profile.json", json.dumps(profile, indent=2, ensure_ascii=False))
        if (rd / "character.json").exists():
            z.write(rd / "character.json", f"{prefix}character.json")
        if (rd / "lab_config.json").exists():
            z.write(rd / "lab_config.json", f"{prefix}lab_config.json")
        for sheet in sorted((rd / "sheets").glob("*.png")) if (rd / "sheets").exists() else []:
            z.write(sheet, f"{prefix}sheets/{sheet.name}")
        for f in sorted((rd / "sprite").glob("*")) if (rd / "sprite").exists() else []:
            if f.is_file():
                z.write(f, f"{prefix}sprite/{f.name}")
        act_file = rd / "sprite" / f"{slug}.act"
        if act_file.exists():  # ready-to-use files for the web game (PixiJS / Phaser)
            from .spr_act import read_act, read_spr
            from .sprite_images import type_names_for
            from .web_bundle import build as web_build

            act = read_act(act_file.read_bytes())
            spr = read_spr(act_file.with_suffix(".spr").read_bytes())
            for fname, data in web_build(slug, act, spr, type_names_for(act_file)).items():
                z.writestr(f"{prefix}web/{fname}", data)
        z.writestr(f"{prefix}README.txt", EXPORT_README.format(slug=slug))
        p.setdefault("notes", {})["exported"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        self._write(d, p)
        return 1

    # ───────── projects (groups of characters) ─────────
    def _group_settings(self, d: Path) -> dict:
        try:
            return self.layout.read_group(d.parent.name)
        except LayoutError:
            return {}

    def list_groups(self) -> list[dict]:
        out = []
        for gid in self.layout.groups():
            g = self.layout.read_group(gid)
            g["characters"] = len(self.layout.characters(gid))
            g["style_image"] = self._style_url(gid)
            out.append(g)
        # the default project first, then by name
        return sorted(out, key=lambda g: (g["id"] != DEFAULT_GROUP, (g.get("name") or g["id"]).lower()))

    def get_group(self, gid: str) -> dict:
        try:
            g = self.layout.read_group(gid)
        except LayoutError as exc:
            raise ProjectError(str(exc)) from None
        g["style_image"] = self._style_url(gid)
        g["body_options"] = {k: _(v["th"]) for k, v in sprite2d.BODY.items()}
        g["detail_options"] = {k: _(v) for k, v in sprite2d.DETAIL.items()}
        g["characters"] = [c for c in self.list() if c["group"] == gid]
        return g

    def create_group(self, name: str) -> dict:
        name = (name or "").strip()
        if not name:
            raise ProjectError(_("Give the project a name."))
        return self.get_group(self.layout.new_group(name)["id"])

    def update_group(self, gid: str, patch: dict) -> dict:
        try:
            g = self.layout.read_group(gid)
        except LayoutError as exc:
            raise ProjectError(str(exc)) from None
        for k in GROUP_KEYS:
            if k in patch:
                v = patch[k]
                if k == "body" and v not in (None, "", *sprite2d.BODY):
                    raise ProjectError(_("Bad value for {key}.", key=k))
                if k == "detail":
                    v = int(v) if str(v or "").isdigit() and int(v) in sprite2d.DETAIL else None
                g[k] = (str(v).strip()[:600] if isinstance(v, str) else v)
        self.layout.write_group(gid, g)
        return self.get_group(gid)

    def delete_group(self, gid: str) -> dict:
        if gid == DEFAULT_GROUP:
            raise ProjectError(_("The default project can't be deleted."))
        if self.layout.characters(gid):
            raise ProjectError(_("Move or delete its characters first."))
        shutil.rmtree(self.layout.group_dir(gid), ignore_errors=True)
        shutil.rmtree(self.renders_dir / gid, ignore_errors=True)
        return {"deleted": gid}

    def _style_url(self, gid: str) -> str | None:
        f = self.layout.group_dir(gid) / "style.png"
        return f"/groups/{gid}/style.png?v={int(f.stat().st_mtime)}" if f.exists() else None

    def set_group_style_image(self, gid: str, data: bytes | None) -> dict:
        d = self.layout.group_dir(gid)
        if not (d / "group.json").exists():
            raise ProjectError(f"No project '{gid}'.")
        if data is None:
            (d / "style.png").unlink(missing_ok=True)
        else:
            img = load_image(data, self.settings.max_image_pixels)
            img.thumbnail((1536, 1536))
            img.convert("RGB").save(d / "style.png")
        return self.get_group(gid)

    def move_character(self, slug: str, gid: str) -> dict:
        self._dir(slug)
        try:
            self.layout.move(slug, gid)
        except LayoutError as exc:
            raise ProjectError(str(exc)) from None
        return self.get(slug)

    def replace_concept(self, slug: str, data: bytes, reclassify: bool = False) -> dict:
        """A new concept image for an existing character: the cut-out and front view follow, drawn
        action sheets stay. With reclassify the profile is guessed again from the new image."""
        d = self._dir(slug)
        p = self._read(d)
        img = self._cutout_bytes(data)
        self._not_an_action_sheet(img)
        Image.open(io.BytesIO(data)).save(d / "concept.png")
        img.save(d / "cutout.png")
        (d / "views").mkdir(exist_ok=True)
        img.save(d / "views" / "front.png")
        if reclassify:
            prof = classify(img, p["profile"].get("name_th", ""), "heuristic")
            for k in ("name_th", "description_en", "height_m"):     # keep what the user typed
                if p["profile"].get(k):
                    prof[k] = p["profile"][k]
            p["profile"] = prof
        p["source_file"] = p.get("source_file")
        self._write(d, p)
        return self.get(slug)


EXPORT_README = """{slug} - game asset package

sheets/<action>.png   rows = directions S, SW, W, NW, N, NE, E, SE (top to bottom); columns = frames
character.json        frame_width/height, pivot (feet, in frame pixels), pixels_per_meter,
                      per action: frames, delay_ms, loop, events (step_l / step_r), root_motion
lab_config.json       tuned in Playtest: move_speed (m/s), hit_frame, attack_range (m), roles
profile.json          name, category, body type, element, rank, features, planned animations, skills
portrait.png          background-removed concept art (UI portrait / bestiary)

Drawing a frame:  src = (frame * frame_width, row * frame_height)
                  dst = (screenX - pivot.x, screenY - pivot.y)   then Y-sort entities by screenY.
"""
