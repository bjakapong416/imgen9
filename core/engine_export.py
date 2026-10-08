"""
Game-engine exports built from the same packed atlas as the web bundle (web_bundle.layout):

  Godot 4    <name>.tres                SpriteFrames: one AtlasTexture per frame (region = the frame in
                                        the atlas, margin = the trimmed-away border, so every frame is
                                        the full canvas and the feet never move), one animation per
                                        <action>_<DIR> with speed = 1000 / frame_ms and loop per action
             <name>_godot_README.txt    how to use it (AnimatedSprite2D offset, directions)
  Aseprite   <name>.aseprite.json       the sprite-sheet JSON Aseprite itself exports ("frames" as an
                                        array, meta.frameTags per <action>_<DIR>, per-frame duration)
             <name>_aseprite_README.txt
"""

from __future__ import annotations

import json

from .web_bundle import Atlas, page_images


def _num(v: float) -> str:
    """Godot float literal: 5.0, 6.66667."""
    s = f"{v:.5f}".rstrip("0")
    return s + "0" if s.endswith(".") else s


def godot_offset(atlas: Atlas) -> dict:
    """AnimatedSprite2D offsets that put the feet on the node's position."""
    cw, ch, ox, oy = atlas.canvas
    return {"uncentered": (-ox, -oy), "centered": (cw / 2 - ox, ch / 2 - oy)}


def godot_tres(atlas: Atlas, folder: str) -> str:
    """SpriteFrames resource. Textures are referenced as res://<folder>/<base>.png."""
    cw, ch, _, _ = atlas.canvas
    ext_ids = {p: f"{p + 1}_atlas" for p in range(len(atlas.pages))}
    subs: dict[int, str] = {}                      # unique image -> sub-resource id (shared by duplicates)
    sub_text: list[str] = []
    for fname in atlas.frame_ref:
        ui = atlas.frame_ref[fname]
        if ui in subs:
            continue
        sid = f"AtlasTexture_{len(subs)}"
        subs[ui] = sid
        p, x, y = atlas.pos[ui]
        x0, y0, x1, y1 = atlas.trims[ui]
        w, h = x1 - x0, y1 - y0
        sub_text.append(
            f'[sub_resource type="AtlasTexture" id="{sid}"]\n'
            f'atlas = ExtResource("{ext_ids[p]}")\n'
            f"region = Rect2({x}, {y}, {w}, {h})\n"
            # margin: position = trimmed-off left/top, size = how much the full canvas is larger
            f"margin = Rect2({x0}, {y0}, {cw - w}, {ch - h})\n"
            "filter_clip = true\n")

    anims = []
    for key, names in atlas.anim_frames.items():
        ms = atlas.anim_ms[key] or 100.0
        frames = ",\n".join(
            '{\n"duration": 1.0,\n"texture": SubResource("%s")\n}' % subs[atlas.frame_ref[n]] for n in names)
        anims.append('{\n"frames": [%s],\n"loop": %s,\n"name": &"%s",\n"speed": %s\n}'
                     % (frames, "true" if atlas.loops(key) else "false", key, _num(round(1000.0 / ms, 3))))

    head = f'[gd_resource type="SpriteFrames" load_steps={len(ext_ids) + len(subs) + 1} format=3]\n\n'
    ext = "".join(f'[ext_resource type="Texture2D" path="res://{folder}/{atlas.bases[p]}.png" id="{i}"]\n'
                  for p, i in ext_ids.items())
    return head + ext + "\n" + "\n".join(sub_text) + "\n[resource]\nanimations = [" + ", ".join(anims) + "]\n"


def godot_files(atlas: Atlas, folder: str | None = None) -> dict[str, bytes]:
    """<name>.tres + README. `folder` = the folder name the files sit in inside the Godot project."""
    name = atlas.name
    folder = folder or f"{name}_godot"
    cw, ch, ox, oy = atlas.canvas
    off = godot_offset(atlas)
    events = []
    for t, e in atlas.anim["actions"].items():
        ev = ", ".join(f"frame {fi} = {v}" for fi, v in e["events"].items()) or "-"
        events.append(f"  {t:<10} {e['frames']} frames x {e['frame_ms']:g} ms, "
                      f"{'loops' if e['loop'] else 'plays once'}; events: {ev}")
    readme = GODOT_README.format(
        name=name, tres=f"{name}.tres", folder=folder, cw=cw, ch=ch, ox=ox, oy=oy,
        ux=off["uncentered"][0], uy=off["uncentered"][1],
        cx=_num(off["centered"][0]), cy=_num(off["centered"][1]),
        first=next(iter(atlas.anim_frames), "idle_S"),
        pages=", ".join(f"{b}.png" for b in atlas.bases), events="\n".join(events))
    return {f"{name}.tres": godot_tres(atlas, folder).encode(), f"{name}_godot_README.txt": readme.encode()}


def aseprite_sheets(atlas: Atlas) -> dict[str, dict]:
    """Aseprite "array" sprite-sheet JSON per atlas page: <base>.aseprite.json -> data."""
    cw, ch, ox, oy = atlas.canvas
    out = {}
    for p, (base, img) in enumerate(zip(atlas.bases, atlas.pages)):
        frames, tags = [], []
        for key, names in atlas.anim_frames.items():
            if any(atlas.pos[atlas.frame_ref[n]][0] != p for n in names):
                continue                                   # an animation lives on one page
            start = len(frames)
            for n in names:
                ui = atlas.frame_ref[n]
                _, x, y = atlas.pos[ui]
                x0, y0, x1, y1 = atlas.trims[ui]
                frames.append({
                    "filename": n,
                    "frame": {"x": x, "y": y, "w": x1 - x0, "h": y1 - y0},
                    "rotated": False, "trimmed": True,
                    "spriteSourceSize": {"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0},
                    "sourceSize": {"w": cw, "h": ch},
                    "duration": round(atlas.anim_ms[key]),
                })
            tag = {"name": key, "from": start, "to": len(frames) - 1, "direction": "forward", "color": "#000000ff"}
            if not atlas.loops(key):
                tag["repeat"] = "1"                        # Aseprite 1.3: play once
            tags.append(tag)
        out[f"{base}.aseprite.json"] = {
            "frames": frames,
            "meta": {
                "app": "https://www.aseprite.org/", "version": "1.3", "image": f"{base}.png",
                "format": "RGBA8888", "size": {"w": img.width, "h": img.height}, "scale": "1",
                "frameTags": tags,
                "layers": [{"name": "sprite", "opacity": 255, "blendMode": "normal"}],
                # the feet: a full-canvas slice whose pivot is the origin every frame shares
                "slices": [{"name": "feet", "color": "#0000ffff",
                            "keys": [{"frame": 0, "bounds": {"x": 0, "y": 0, "w": cw, "h": ch},
                                      "pivot": {"x": ox, "y": oy}}]}],
            },
        }
    return out


def aseprite_files(atlas: Atlas) -> dict[str, bytes]:
    cw, ch, ox, oy = atlas.canvas
    files = {k: json.dumps(v, indent=1).encode() for k, v in aseprite_sheets(atlas).items()}
    files[f"{atlas.name}_aseprite_README.txt"] = ASEPRITE_README.format(
        name=atlas.name, json=f"{atlas.name}.aseprite.json", png=f"{atlas.name}.png", cw=cw, ch=ch, ox=ox, oy=oy, ax=atlas.anchor["x"], ay=atlas.anchor["y"]).encode()
    return files


def godot_bundle(atlas: Atlas) -> dict[str, bytes]:
    """kind=godot: atlas PNGs + .tres + README."""
    images = page_images(atlas)
    return {**{f"{b}.png": images[f"{b}.png"] for b in atlas.bases}, **godot_files(atlas)}


def aseprite_bundle(atlas: Atlas) -> dict[str, bytes]:
    """kind=aseprite: atlas PNGs + Aseprite JSON + README."""
    images = page_images(atlas)
    return {**{f"{b}.png": images[f"{b}.png"] for b in atlas.bases}, **aseprite_files(atlas)}


GODOT_README = """{name} - Godot 4 SpriteFrames

{tres:<22} SpriteFrames resource: one animation per <action>_<DIR> (e.g. {first}),
                       speed = 1000 / frame_ms (frames per second), loop set per action.
{pages:<22} the texture atlas the .tres points at.

1. Copy this folder into the ROOT of your Godot project as-is, named {folder}/
   (the .tres refers to res://{folder}/...). If you put it somewhere else, move it inside the
   Godot editor's FileSystem dock (Godot updates the paths), or edit the ext_resource path lines.
2. Select the atlas PNG > Import dock: for pixel art, set the project's
   Rendering > Textures > Default Texture Filter to Nearest (or the node's Texture > Filter).
3. Add an AnimatedSprite2D and set Sprite Frames = {name}.tres.
4. Put the feet on the node's position (every frame is the full {cw} x {ch} canvas, feet at
   ({ox}, {oy}) in it, so these values hold for every animation):
     centered = false,  offset = Vector2({ux}, {uy})        <- pixel-exact, recommended
   or
     centered = true,   offset = Vector2({cx}, {cy})
   Then Y-sort by the node position (CanvasItem > Ordering > Y Sort Enabled on the parent).

Directions S SW W NW N NE E SE. Pick one from the movement vector (y down):
    var dirs = ["S", "SW", "W", "NW", "N", "NE", "E", "SE"]
    var idx = posmod(roundi(atan2(-v.x, v.y) / (PI / 4)), 8)
    $AnimatedSprite2D.play("walk_" + dirs[idx])

Timing and events (Godot SpriteFrames have no events: react in the frame_changed signal,
e.g. apply damage when the attack reaches the "atk" frame):
{events}
"""

ASEPRITE_README = """{name} - Aseprite sprite-sheet JSON

{json:<22} The JSON Aseprite itself writes with File > Export Sprite Sheet
                       (JSON Data: Array, Meta: Tags + Slices). Many engines and tools read it:
                       Phaser (this.load.aseprite + this.anims.createFromAseprite), Unity and Godot
                       Aseprite importers, LibreSprite / TexturePacker-style tools.
{png:<22} the atlas the JSON points at (meta.image).

frames      one entry per animation frame in play order (duplicate frames point at the same
            atlas rect), each with "duration" in ms.
frameTags   one tag per <action>_<DIR> (from / to = indexes into frames, direction "forward").
            Tags that should play once (attack, hurt, die...) carry "repeat": "1".
slices      "feet": the full {cw} x {ch} canvas with pivot ({ox}, {oy}) = the character's feet.
            Every frame has sourceSize {cw} x {ch}, so with the pivot / origin at
            ({ax}, {ay}) of the source size the feet never move.

Directions S SW W NW N NE E SE: idx = round(atan2(-dx, dy) / 45 deg) mod 8 (screen space, y down).
A very large character is split over several atlases (<name>-0.png ...), one JSON each; a tag is
written to the JSON of the atlas that holds all its frames.
This is the exported JSON, not a .aseprite file: Aseprite itself can't open it for editing.
"""
