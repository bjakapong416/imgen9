"""
Web game bundle from one of our .spr/.act sprites, for PixiJS / Phaser:

  <name>.png / .webp   texture atlas: frames trimmed, duplicates stored once
  <name>.json          TexturePacker "JSON Hash" atlas (PixiJS Assets.load, Phaser load.atlas);
                       every frame has the same sourceSize and anchor/pivot = the feet, plus an
                       "animations" map <action>_<DIR> -> frame names
  <name>.anim.json     per action: frame_ms, loop, events ("atk" = apply damage, "step" = footstep)
  demo.html            PixiJS v8 page playing all 8 directions
"""

from __future__ import annotations

import hashlib
import io
import json
from dataclasses import dataclass

from PIL import Image

from .spr_act import ACT_DIRECTIONS, ActFile, SprFile
from .sprite_images import action_label, canvas_for, render_frame

ATLAS_MAX = 2048
LOOPING = {"idle", "walk", "run", "sit", "sleep", "happy"}


def _shelf_pack(sizes: list[tuple[int, int]], pad: int = 1):
    """Pack rectangles (tallest first) into pages of at most ATLAS_MAX px."""
    order = sorted(range(len(sizes)), key=lambda i: -sizes[i][1])
    pos: list = [None] * len(sizes)
    pages = [[0, 0]]
    x = y = shelf = 0
    for i in order:
        w, h = sizes[i]
        if x + w > ATLAS_MAX:
            x, y, shelf = 0, y + shelf + pad, 0
        if y + h > ATLAS_MAX:
            pages.append([0, 0])
            x = y = shelf = 0
        p = len(pages) - 1
        pos[i] = (p, x, y)
        pages[p][0] = max(pages[p][0], x + w)
        pages[p][1] = max(pages[p][1], y + h)
        x += w + pad
        shelf = max(shelf, h)
    return pos, pages


@dataclass
class Atlas:
    """The packed atlas every export shares (web bundle, Godot, Aseprite)."""
    name: str
    canvas: tuple[int, int, int, int]            # (w, h, origin_x, origin_y): origin = the feet
    anchor: dict                                 # origin / canvas size, rounded
    pages: list[Image.Image]                     # atlas page images (RGBA)
    bases: list[str]                             # file base name per page: <name> or <name>-<p>
    frame_ref: dict[str, int]                    # frame name <action>_<DIR>_<NN> -> unique image index
    pos: list[tuple[int, int, int]]              # per unique image: (page, x, y) in the atlas
    trims: list[tuple[int, int, int, int]]       # per unique image: bbox (x0, y0, x1, y1) in the canvas
    anim_frames: dict[str, list[str]]            # <action>_<DIR> -> frame names, in play order
    anim_ms: dict[str, float]                    # <action>_<DIR> -> ms per frame
    anim: dict                                   # the <name>.anim.json content (without "atlas")

    def loops(self, key: str) -> bool:
        return key.rsplit("_", 1)[0] in LOOPING


def layout(name: str, act: ActFile, spr: SprFile, type_names: list[str] | None = None) -> Atlas:
    """Render every frame, trim, drop duplicates and shelf-pack them into atlas pages."""
    canvas = canvas_for(act, spr)
    cw, ch, ox, oy = canvas
    anchor = {"x": round(ox / cw, 5), "y": round(oy / ch, 5)}   # the feet; identical for every frame

    uniques: list[Image.Image] = []
    trims: list[tuple[int, int, int, int]] = []
    by_hash: dict[str, int] = {}
    frame_ref: dict[str, int] = {}
    anim_frames: dict[str, list[str]] = {}
    anim_ms: dict[str, float] = {}
    anim: dict = {"name": name, "canvas": {"w": cw, "h": ch}, "origin": {"x": ox, "y": oy}, "anchor": anchor,
                  "directions": ACT_DIRECTIONS, "actions": {}}
    for ai, a in enumerate(act.actions):
        if not a.frames:
            continue
        t, d = action_label(act, ai, type_names)
        names = []
        for fi, f in enumerate(a.frames):
            img = render_frame(act, spr, f, canvas)
            bbox = img.getbbox() or (0, 0, 1, 1)
            crop = img.crop(bbox)
            key = hashlib.sha1(crop.tobytes() + str(bbox).encode()).hexdigest()
            if key not in by_hash:
                by_hash[key] = len(uniques)
                uniques.append(crop)
                trims.append(bbox)
            fname = f"{t}_{d}_{fi:02d}"
            frame_ref[fname] = by_hash[key]
            names.append(fname)
        anim_frames[f"{t}_{d}"] = names
        anim_ms[f"{t}_{d}"] = a.delay_ms
        entry = anim["actions"].setdefault(t, {
            "frame_ms": round(a.delay_ms, 3), "frames": len(a.frames),
            "loop": t in LOOPING, "events": {}, "directions": {},
        })
        entry["directions"][d] = f"{t}_{d}"
        for fi, f in enumerate(a.frames):
            if 0 <= f.event_index < len(act.events):
                entry["events"][str(fi)] = act.events[f.event_index]

    pos, pages = _shelf_pack([u.size for u in uniques])
    page_imgs = [Image.new("RGBA", (max(1, w), max(1, h)), (0, 0, 0, 0)) for w, h in pages]
    for u, (p, x, y) in zip(uniques, pos):
        page_imgs[p].alpha_composite(u, (x, y))
    bases = [name if len(page_imgs) == 1 else f"{name}-{p}" for p in range(len(page_imgs))]
    return Atlas(name, canvas, anchor, page_imgs, bases, frame_ref, pos, trims, anim_frames, anim_ms, anim)


def page_images(atlas: Atlas) -> dict[str, bytes]:
    """<base>.png and <base>.webp for every atlas page."""
    files: dict[str, bytes] = {}
    for base, img in zip(atlas.bases, atlas.pages):
        buf = io.BytesIO()
        if img.getcolors(256) is not None:
            # .spr sprites use one <=255-colour palette: an 8-bit PNG is lossless here and far smaller.
            img.quantize(colors=256, method=Image.Quantize.FASTOCTREE, dither=Image.Dither.NONE).save(buf, "PNG", optimize=True)
        else:
            img.save(buf, "PNG", optimize=True)
        files[f"{base}.png"] = buf.getvalue()
        buf = io.BytesIO()
        img.save(buf, "WEBP", lossless=True, method=6)
        files[f"{base}.webp"] = buf.getvalue()
    return files


def build(name: str, act: ActFile, spr: SprFile, type_names: list[str] | None = None,
          atlas: Atlas | None = None) -> dict[str, bytes]:
    atlas = atlas or layout(name, act, spr, type_names)
    cw, ch, _, _ = atlas.canvas
    anchor, pos, trims = atlas.anchor, atlas.pos, atlas.trims
    images = page_images(atlas)

    files: dict[str, bytes] = {}
    atlases = []
    for p, (base, img) in enumerate(zip(atlas.bases, atlas.pages)):
        frames = {}
        for fname, ui in atlas.frame_ref.items():
            if pos[ui][0] != p:
                continue
            _, x, y = pos[ui]
            x0, y0, x1, y1 = trims[ui]
            frames[fname] = {
                "frame": {"x": x, "y": y, "w": x1 - x0, "h": y1 - y0},
                "rotated": False, "trimmed": True,
                "spriteSourceSize": {"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0},
                "sourceSize": {"w": cw, "h": ch},
                "anchor": anchor,   # PixiJS reads this per frame
                "pivot": anchor,    # TexturePacker / Phaser name for the same thing
            }
        sheet = {
            "frames": frames,
            "animations": {k: v for k, v in atlas.anim_frames.items() if all(n in frames for n in v)},
            "meta": {"app": "ImGen9", "image": f"{base}.png", "format": "RGBA8888",
                     "size": {"w": img.width, "h": img.height}, "scale": "1"},
        }
        files[f"{base}.json"] = json.dumps(sheet, indent=1).encode()
        files[f"{base}.png"] = images[f"{base}.png"]
        files[f"{base}.webp"] = images[f"{base}.webp"]
        atlases.append(f"{base}.json")
    anim = dict(atlas.anim, atlas=atlases)
    files[f"{name}.anim.json"] = json.dumps(anim, indent=1).encode()
    demo = (DEMO_HTML_PATH.read_text(encoding="utf-8").replace("__NAME__", name).replace("__ATLAS__", atlases[0]))
    files["demo.html"] = demo.encode()
    files["README.txt"] = README.replace("__NAME__", name).encode()
    return files


from pathlib import Path  # noqa: E402

DEMO_HTML_PATH = Path(__file__).with_name("web_demo.html")

README = """__NAME__ - web game bundle

__NAME__.png / .webp   texture atlas (trimmed, duplicates removed). WebP is smaller; PNG for compatibility.
__NAME__.json          TexturePacker "JSON Hash" atlas - loads directly in PixiJS (Assets.load) and
                       Phaser (this.load.atlas). Frame names: <action>_<DIR>_<NN>, e.g. walk_S_03.
                       Every frame has the same sourceSize and anchor/pivot = the character's feet,
                       so animations never jitter. "animations" lists frames per <action>_<DIR>.
__NAME__.anim.json     timing per action (frame_ms, loop) and events per frame
                       ("atk" = apply damage, "step" = footstep sound); directions S SW W NW N NE E SE.
demo.html              PixiJS demo. Browsers block local files, so serve this folder, e.g.
                       python -m http.server 8080   then open http://localhost:8080/demo.html
game.json              (2D characters) hitbox and ground shadow, and per direction the cast_point
                       where an attack or spell leaves the hand at the hit frame - pixels from the
                       feet (y down), "pixels_per_metre" to convert.

Direction from movement (screen space, y down): idx = round(atan2(-dx, dy) / 45 deg) mod 8
-> S, SW, W, NW, N, NE, E, SE. Y-sort sprites by their foot position.
"""
