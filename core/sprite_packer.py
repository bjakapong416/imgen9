"""
Packs the frames written by blender/render_8dir.py into one sprite sheet per action.

    python -m core.sprite_packer renders/orc

Sheet layout (same idea as an .act file: action x direction x frame):
    rows    = directions in the order S, SW, W, NW, N, NE, E, SE
    columns = frames
Writes renders/<name>/sheets/<action>.png and renders/<name>/character.json.
Directions rendered with --mirror are produced here by flipping their source direction.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from PIL import Image, ImageOps

MAX_SHEET_SIDE = 16384  # browsers / GPUs refuse textures larger than this


class PackError(ValueError):
    pass


def needs_pack(char_dir: Path) -> bool:
    manifest = char_dir / "render_manifest.json"
    character = char_dir / "character.json"
    if not manifest.exists():
        return False
    return not character.exists() or character.stat().st_mtime < manifest.stat().st_mtime


def pack(char_dir: Path) -> dict:
    manifest_path = char_dir / "render_manifest.json"
    if not manifest_path.exists():
        raise PackError(f"{manifest_path} not found - render the character first.")
    m = json.loads(manifest_path.read_text(encoding="utf-8"))
    fw, fh = m["frame_width"], m["frame_height"]
    dirs = m["directions"]
    mirrored = m.get("mirrored_directions", {})
    pattern = m["frame_pattern"]
    sheets_dir = char_dir / "sheets"
    sheets_dir.mkdir(exist_ok=True)

    animations = {}
    warnings = []
    for a in m["actions"]:
        name, n = a["name"], a["frames"]
        width, height = fw * max(1, n), fh * len(dirs)
        if max(width, height) > MAX_SHEET_SIDE:
            warnings.append(f"{name}: sheet is {width}x{height}px, larger than {MAX_SHEET_SIDE}px (use --step or a smaller --size)")
        sheet = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        missing = 0
        # Visible-pixel bounds relative to the pivot, over all frames (.act stores per-image offsets
        # for the same reason: the tool needs the real size, not the padded frame).
        bx0 = by0 = float("inf")
        bx1 = by1 = float("-inf")
        for row, d in enumerate(dirs):
            src = mirrored.get(d, d)
            for i in range(n):
                path = char_dir / pattern.format(action=name, dir=src, index=i)
                if not path.exists():
                    missing += 1
                    continue
                im = Image.open(path).convert("RGBA")
                if d in mirrored:
                    im = ImageOps.mirror(im)  # pivot is centred horizontally, so it stays put
                bbox = im.getchannel("A").point(lambda a: 255 if a > 8 else 0).getbbox()
                if bbox:
                    px, py = m["pivot"]["x"], m["pivot"]["y"]
                    bx0, by0 = min(bx0, bbox[0] - px), min(by0, bbox[1] - py)
                    bx1, by1 = max(bx1, bbox[2] - px), max(by1, bbox[3] - py)
                sheet.paste(im, (i * fw, row * fh))
        if missing:
            warnings.append(f"{name}: {missing} frame image(s) missing")
        sheet.save(sheets_dir / f"{name}.png", optimize=False)
        animations[name] = {
            "sheet": f"sheets/{name}.png",
            "frames": n,
            "fps": a["fps"],
            "delay_ms": a["delay_ms"],
            "loop": a["loop"],
            "root_motion": a.get("root_motion"),
            "events": a.get("events", {}),
            "bounds": [bx0, by0, bx1, by1] if bx1 > bx0 else None,
        }

    character = {
        "format": "character/1",
        "name": m["name"],
        "layout": "rows=directions, columns=frames",
        "frame_width": fw,
        "frame_height": fh,
        "pivot": m["pivot"],
        "pixels_per_meter": m["pixels_per_meter"],
        "camera": m["camera"],
        "directions": dirs,
        "mirrored_directions": mirrored,
        "animations": animations,
        "warnings": warnings,
    }
    (char_dir / "character.json").write_text(json.dumps(character, indent=2), encoding="utf-8")
    return character


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: python -m core.sprite_packer renders/<name>")
    result = pack(Path(sys.argv[1]))
    print(f"packed {len(result['animations'])} action(s) for {result['name']}")
    for w in result["warnings"]:
        print("warning:", w)
