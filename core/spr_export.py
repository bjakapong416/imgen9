"""
Rendered character (renders/<name>/) -> classic 2D game sprite: <name>.spr + <name>.act.

What "classic" means here (typical numbers for 2D online-game monster sprites):
  * size      - a 1.7 m character stands ~82 px above its origin, drawn small and crisp
  * colour    - one shared palette of at most 255 colours + index 0 transparent (SPR palette images)
  * edges     - hard alpha (no semi-transparent fringe) and a 1 px dark outline
  * actions   - idle, walk, attack, damage, die, then extras; 8 directions each
                (S, SW, W, NW, N, NE, E, SE - the .act order)
  * timing    - typical frame counts (walk 75 ms/frame, idle 100 ms, ...) while keeping each
                action's real duration; the hit frame becomes an "atk" event, foot contacts "step"
  * reuse     - identical frame images are stored once

    python -m core.spr_export renders/<name>
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sys
from pathlib import Path

import numpy as np
from PIL import Image

from .spr_act import ACT_DIRECTIONS, read_act, read_spr, write_act, write_spr

CLASSIC_PX_PER_M = 82 / 1.7          # typical classic sprite height in px for a ~1.7 m humanoid
TRANSPARENT = (255, 0, 255)            # .spr convention for palette index 0
ROLE_ORDER = ["idle", "walk", "attack", "damage", "die"]
ROLE_ALIASES = {
    "idle": r"idle|stand|breath|wait",
    "walk": r"walk|move",
    "attack": r"attack|atk|bite|slash|claw|punch|strike",
    "damage": r"hurt|damage|hit",
    "die": r"die|death|dead",
}
# Target delay per frame (ms) by role - typical classic 2D game values.
TARGET_DELAY = {"idle": 100, "walk": 75, "attack": 75, "damage": 75, "die": 100}
DEFAULT_DELAY = 100
LOOPING = re.compile(r"idle|walk|run|move|stand|loop|breath|sit|sleep", re.I)


def _role_of(name: str) -> str | None:
    for role, pat in ROLE_ALIASES.items():
        if re.search(pat, name, re.I):
            return role
    return None


def _premult_resize(im: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Resize without dark fringes: premultiply alpha first."""
    return im.convert("RGBa").resize(size, Image.LANCZOS).convert("RGBA")


def _hard_alpha_outline(arr: np.ndarray, outline: bool) -> np.ndarray:
    """arr: HxWx4 uint8. Binary alpha, then a 1 px outline in a darkened neighbour colour."""
    solid = arr[..., 3] >= 128
    out = arr.copy()
    out[..., 3] = np.where(solid, 255, 0)
    if not outline:
        return out
    h, w = solid.shape
    edge_col = np.zeros((h, w, 3), dtype=np.float32)
    edge_cnt = np.zeros((h, w), dtype=np.float32)
    for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        src = np.zeros_like(solid)
        col = np.zeros((h, w, 3), dtype=np.float32)
        ys, yd = (slice(max(0, -dy), h - max(0, dy)), slice(max(0, dy), h - max(0, -dy)))
        xs, xd = (slice(max(0, -dx), w - max(0, dx)), slice(max(0, dx), w - max(0, -dx)))
        src[yd, xd] = solid[ys, xs]
        col[yd, xd] = arr[ys, xs, :3]
        edge_col += col * src[..., None]
        edge_cnt += src
    ring = (~solid) & (edge_cnt > 0)
    dark = (edge_col / np.maximum(edge_cnt, 1)[..., None]) * 0.3
    out[ring, :3] = dark[ring].astype(np.uint8)
    out[ring, 3] = 255
    return out


def export(char_dir: Path, scale: float | None = None, outline: bool = True, colors: int = 255) -> dict:
    c = json.loads((char_dir / "character.json").read_text(encoding="utf-8"))
    lab_path = char_dir / "lab_config.json"
    lab = json.loads(lab_path.read_text(encoding="utf-8")) if lab_path.exists() else {}
    fw, fh = c["frame_width"], c["frame_height"]
    px, py = c["pivot"]["x"], c["pivot"]["y"]
    ppm = c["pixels_per_meter"]
    k = scale or CLASSIC_PX_PER_M / ppm
    dirs = c["directions"]
    if dirs != ACT_DIRECTIONS:
        raise ValueError(f"Unexpected direction order {dirs}")

    # Action order: the standard slots first, then everything else (sit, sleep, happy, ...).
    names = list(c["animations"])
    by_role = {}
    for n in names:
        r = _role_of(n)
        if r and r not in by_role:
            by_role[r] = n
    ordered = [by_role[r] for r in ROLE_ORDER if r in by_role] + [n for n in names if n not in by_role.values()]

    hit_frame_cfg = lab.get("hit_frame")
    frames_rgba: list[np.ndarray] = []      # every output frame, before palette
    plan = []                               # per action: frames as (frame_index_in_frames_rgba, event)
    events = ["atk", "step"]
    summary = []
    for name in ordered:
        a = c["animations"][name]
        role = _role_of(name)
        n_src = a["frames"]
        dur = n_src * a["delay_ms"]
        target = TARGET_DELAY.get(role, DEFAULT_DELAY)
        n_out = max(1, min(n_src, round(dur / target)))
        delay = dur / n_out
        src_idx = [min(n_src - 1, math.floor(i * n_src / n_out)) for i in range(n_out)]
        # Events: hit frame (attack) and foot contacts (walk), mapped to the resampled frames.
        ev = {}
        if role == "attack":
            hf = hit_frame_cfg if hit_frame_cfg is not None else round((n_src - 1) * 0.59)
            ev[min(n_out - 1, round(hf * n_out / n_src))] = 0
        for f_str, name_ev in (a.get("events") or {}).items():
            if name_ev.startswith("step"):
                ev.setdefault(min(n_out - 1, round(int(f_str) * n_out / n_src)), 1)

        sheet = Image.open(char_dir / a["sheet"]).convert("RGBA")
        out_w, out_h = max(1, round(fw * k)), max(1, round(fh * k))
        for row, d in enumerate(dirs):
            seq = []
            for oi, si in enumerate(src_idx):
                cell = sheet.crop((si * fw, row * fh, (si + 1) * fw, (row + 1) * fh))
                small = np.asarray(_premult_resize(cell, (out_w, out_h)))
                frames_rgba.append(_hard_alpha_outline(small, outline))
                seq.append((len(frames_rgba) - 1, ev.get(oi, -1)))
            plan.append({"name": name, "dir": d, "delay_ms": delay, "frames": seq})
        summary.append({"action": name, "slot": role or "extra", "frames": f"{n_src}->{n_out}",
                        "delay_ms": round(delay, 1), "events": {str(i): events[e] for i, e in ev.items()}})

    # One palette for the whole character (index 0 reserved for transparency).
    opaque = np.concatenate([f[f[..., 3] > 0][:, :3] for f in frames_rgba if (f[..., 3] > 0).any()])
    if len(opaque) > 400_000:  # sample for speed; the palette barely changes
        opaque = opaque[np.random.default_rng(0).choice(len(opaque), 400_000, replace=False)]
    strip = Image.fromarray(opaque.reshape(1, -1, 3), "RGB")
    pal_img = strip.quantize(colors=min(colors, 255), method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    pal_rgb = pal_img.getpalette()[: 3 * min(colors, 255)]
    palette = [TRANSPARENT] + [tuple(pal_rgb[i:i + 3]) for i in range(0, len(pal_rgb), 3)]
    flat = [v for rgb in palette for v in rgb]

    images, img_ids, layers = [], {}, []
    opx, opy = px * k, py * k
    for f in frames_rgba:
        mask = f[..., 3] > 0
        if not mask.any():
            layers.append(None)
            continue
        ys, xs = np.nonzero(mask)
        y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
        crop = f[y0:y1, x0:x1]
        rgb = Image.fromarray(np.ascontiguousarray(crop[..., :3]), "RGB")
        idx = np.asarray(rgb.quantize(palette=pal_img, dither=Image.Dither.NONE), dtype=np.uint8) + 1
        idx[crop[..., 3] == 0] = 0
        key = hashlib.sha1(idx.tobytes() + bytes(idx.shape)).hexdigest()
        if key not in img_ids:  # identical frames share one image
            im = Image.fromarray(idx, "P")
            im.putpalette(flat)
            img_ids[key] = len(images)
            images.append(im)
        w, h = x1 - x0, y1 - y0
        cx, cy = x0 + w / 2 - opx, y0 + h / 2 - opy   # sprite centre relative to the origin (feet)
        layers.append((round(cx), round(cy), img_ids[key], 0, w, h))

    acts = []
    for a in plan:
        acts.append({"delay_ms": a["delay_ms"], "frames": [
            {"layers": [layers[fi]] if layers[fi] else [], "event": e} for fi, e in a["frames"]]})

    out_dir = char_dir / "sprite"
    out_dir.mkdir(exist_ok=True)
    name = char_dir.name
    spr_bytes = write_spr(images, palette)
    act_bytes = write_act(acts, events)
    (out_dir / f"{name}.spr").write_bytes(spr_bytes)
    (out_dir / f"{name}.act").write_bytes(act_bytes)

    # Self-check with the same readers the Sprite Inspector uses.
    act = read_act(act_bytes)
    spr = read_spr(spr_bytes)
    assert act.trailing_bytes == 0 and len(spr.palette_images) == len(images)
    # Height above the origin in the first action (idle), direction S - how Sprite Inspector measures it;
    # attack frames with a raised weapon would overstate the body height.
    idle_s = [layers[fi] for fi, _ in plan[0]["frames"]] if plan else []
    heights = [-(l[1] - l[5] / 2) for l in idle_s if l]
    report = {
        "files": [f"sprite/{name}.spr", f"sprite/{name}.act"],
        "actions": len(acts), "types": len(acts) // 8,
        "images": len(images), "frames": len(layers), "palette_colors": len(palette) - 1,
        "height_px": round(max(heights)) if heights else 0, "scale": round(k, 4),
        "bytes": {"spr": len(spr_bytes), "act": len(act_bytes)},
        "action_map": summary,
    }
    (out_dir / "build.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("usage: python -m core.spr_export renders/<name> [--no-outline]")
    rep = export(Path(sys.argv[1]), outline="--no-outline" not in sys.argv)
    print(json.dumps({k: v for k, v in rep.items() if k != "action_map"}, indent=2))
    for a in rep["action_map"]:
        print(a)
