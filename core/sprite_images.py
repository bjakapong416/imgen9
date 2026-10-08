"""
Turn an .spr/.act pair back into ordinary images people can use: single frames (PNG),
one action in one direction (PNG strip / animated GIF), or everything as a zip.

Every image of a sprite shares one canvas size and origin, so frames line up when swapped
in a game engine: the character's feet (the ACT origin) are always at the same pixel,
recorded in info.json as "origin".
"""

from __future__ import annotations

import io
import json
import zipfile

from PIL import Image, ImageOps

from .spr_act import MONSTER_ACTIONS, PLAYER_ACTIONS, ACT_DIRECTIONS, ActFile, SprFile

PAD = 2


def _layer_box(spr: SprFile, layer) -> tuple[int, int, int, int] | None:
    im = spr.image(layer.sprite_type, layer.sprite_index)
    if im is None:
        return None
    w, h = round(im.width * abs(layer.scale_x)), round(im.height * abs(layer.scale_y))
    x0, y0 = layer.x - w // 2, layer.y - h // 2
    return x0, y0, x0 + w, y0 + h


def canvas_for(act: ActFile, spr: SprFile) -> tuple[int, int, int, int]:
    """(width, height, origin_x, origin_y) covering every frame of every action."""
    x0 = y0 = 10 ** 9
    x1 = y1 = -10 ** 9
    for a in act.actions:
        for f in a.frames:
            for L in f.layers:
                b = _layer_box(spr, L)
                if b:
                    x0, y0, x1, y1 = min(x0, b[0]), min(y0, b[1]), max(x1, b[2]), max(y1, b[3])
    if x0 > x1:
        return 1, 1, 0, 0
    return x1 - x0 + 2 * PAD, y1 - y0 + 2 * PAD, -x0 + PAD, -y0 + PAD


def render_frame(act: ActFile, spr: SprFile, frame, canvas) -> Image.Image:
    w, h, ox, oy = canvas
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    for L in frame.layers:
        im = spr.image(L.sprite_type, L.sprite_index)
        if im is None:
            continue
        if L.mirror:
            im = ImageOps.mirror(im)
        sw, sh = round(im.width * abs(L.scale_x)), round(im.height * abs(L.scale_y))
        if (sw, sh) != im.size:
            im = im.resize((max(1, sw), max(1, sh)), Image.NEAREST)
        if L.rotation:
            im = im.rotate(-L.rotation, expand=True, resample=Image.NEAREST)
        if L.color[3] < 255:
            a = im.getchannel("A").point(lambda v: v * L.color[3] // 255)
            im.putalpha(a)
        out.alpha_composite(im, (ox + L.x - im.width // 2, oy + L.y - im.height // 2))
    return out


def action_label(act: ActFile, index: int, type_names: list[str] | None = None) -> tuple[str, str]:
    """(action type, direction). type_names (from our build.json) beats the generic .act slot names."""
    t, d = divmod(index, 8)
    if type_names and t < len(type_names):
        return type_names[t], ACT_DIRECTIONS[d]
    n_types = len(act.actions) // 8
    names = PLAYER_ACTIONS if n_types > 9 else MONSTER_ACTIONS
    return (names[t] if t < len(names) else f"action{t}"), ACT_DIRECTIONS[d]


def type_names_for(act_path) -> list[str] | None:
    """Real action names of one of our exports: standard slots keep their names, extras keep theirs (happy, sit...)."""
    from pathlib import Path

    rep = Path(act_path).with_name("build.json")
    if not rep.exists():
        return None
    amap = json.loads(rep.read_text(encoding="utf-8")).get("action_map", [])
    return [a["slot"] if a["slot"] != "extra" else a["action"] for a in amap]


def strip_png(frames: list[Image.Image]) -> bytes:
    w, h = frames[0].size
    sheet = Image.new("RGBA", (w * len(frames), h), (0, 0, 0, 0))
    for i, f in enumerate(frames):
        sheet.alpha_composite(f, (i * w, 0))
    buf = io.BytesIO()
    sheet.save(buf, "PNG")
    return buf.getvalue()


def gif_bytes(frames: list[Image.Image], delay_ms: float) -> bytes:
    """Animated GIF with real transparency (palette index 0) and the action's frame delay."""
    pal_frames = []
    for f in frames:
        alpha = f.getchannel("A")
        p = f.convert("RGB").quantize(colors=255, method=Image.Quantize.FASTOCTREE, dither=Image.Dither.NONE)
        # shift indices by one so 0 can be transparent
        idx = p.point(lambda v: v + 1)
        pal = p.getpalette()[: 255 * 3]
        idx.putpalette([255, 0, 255] + pal)
        mask = alpha.point(lambda v: 255 if v < 128 else 0)
        idx.paste(0, mask=mask)
        pal_frames.append(idx)
    buf = io.BytesIO()
    pal_frames[0].save(buf, "GIF", save_all=True, append_images=pal_frames[1:], duration=max(20, round(delay_ms)),
                       loop=0, transparency=0, disposal=2)
    return buf.getvalue()


def png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def export_zip(name: str, act: ActFile, spr: SprFile, act_bytes: bytes, spr_bytes: bytes,
               type_names: list[str] | None = None) -> bytes:
    """Everything: frames per action/direction, strips, GIFs, one sheet per action type, info.json."""
    canvas = canvas_for(act, spr)
    info = {"name": name, "canvas": {"width": canvas[0], "height": canvas[1]},
            "origin": {"x": canvas[2], "y": canvas[3]}, "directions": ACT_DIRECTIONS, "actions": {}}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{name}/{name}.spr", spr_bytes)
        z.writestr(f"{name}/{name}.act", act_bytes)
        sheets: dict[str, list[list[Image.Image]]] = {}
        for ai, a in enumerate(act.actions):
            if not a.frames:
                continue
            t, d = action_label(act, ai, type_names)
            frames = [render_frame(act, spr, f, canvas) for f in a.frames]
            base = f"{name}/{t}/{d}"
            for i, f in enumerate(frames):
                z.writestr(f"{base}/{i:02d}.png", png_bytes(f))
            z.writestr(f"{base}/strip.png", strip_png(frames))
            z.writestr(f"{base}/anim.gif", gif_bytes(frames, a.delay_ms))
            sheets.setdefault(t, []).append(frames)
            events = {i: act.events[f.event_index] for i, f in enumerate(a.frames)
                      if 0 <= f.event_index < len(act.events)}
            info["actions"].setdefault(t, {"frames": len(frames), "delay_ms": round(a.delay_ms, 3),
                                           "events": events})
        for t, rows in sheets.items():  # rows = directions in .act order, columns = frames
            w, h = canvas[0], canvas[1]
            cols = max(len(r) for r in rows)
            sheet = Image.new("RGBA", (w * cols, h * len(rows)), (0, 0, 0, 0))
            for ri, row in enumerate(rows):
                for ci, f in enumerate(row):
                    sheet.alpha_composite(f, (ci * w, ri * h))
            z.writestr(f"{name}/{t}/sheet.png", png_bytes(sheet))
        z.writestr(f"{name}/info.json", json.dumps(info, indent=2))
        from .web_bundle import build as web_build  # local import: web_bundle imports this module

        for fname, data in web_build(name, act, spr, type_names).items():
            z.writestr(f"{name}/web/{fname}", data)
    return buf.getvalue()
