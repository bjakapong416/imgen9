"""
Weapon layers for the 2D route, the way classic 2D online games do it: the character is drawn empty-handed
and the weapon is a separate image laid over each frame at the hand, so weapons of the same kind can
be swapped in the game.

Where the hand is in each frame comes from the stick-figure pose guide the AI drew from
(pose_guide.hand_frames); the user can correct any frame (offset, angle, in front of / behind the
body). Offsets are in units of the character's standing height, so they survive re-scaling.
"""

from __future__ import annotations

import math

import numpy as np
from PIL import Image

from . import pose_guide, sprite2d

DEFAULT_GRIP = (0.5, 0.7)   # where the hand holds it: x, y as fractions of the upright image (tip at y = 0)
DEFAULT_SIZE = 1.05         # weapon length / character standing height (a mage's staff)

# Weapon types. Weapons that attack the same way share the body's attack drawing and swap
# freely; a different `style` needs its own attack drawing (pose_guide.ATTACK_STYLES). `hand`: which
# hand holds it (a bow is held in the left). `rest`: extra angle outside the attack (a gun points
# down-forward instead of straight up). size / grip: defaults for a new weapon of this type.
# squash: width kept on screen. A bow is drawn from the side, but aimed at a target in a 3/4 view it
# turns half toward the camera, so its curve looks about half as deep.
TYPES = {
    "staff":  {"th": "Staff",  "en": "a wooden staff",            "style": "swing",  "hand": "r", "size": 1.05, "grip": (0.5, 0.70), "rest": 0},
    "dagger": {"th": "Dagger", "en": "a dagger",                  "style": "thrust", "hand": "r", "size": 0.32, "grip": (0.5, 0.80), "rest": 0},
    "sword":  {"th": "Sword",  "en": "a one-handed sword",        "style": "swing",  "hand": "r", "size": 0.72, "grip": (0.5, 0.88), "rest": 0},
    "spear":  {"th": "Spear",  "en": "a long spear",              "style": "thrust", "hand": "r", "size": 1.30, "grip": (0.5, 0.62), "rest": 0},
    "bow":    {"th": "Bow",    "en": "a recurve bow",             "style": "bow",    "hand": "l", "size": 0.85, "grip": (0.15, 0.50), "rest": 0,
               "squash": 0.5},
    "gun":    {"th": "Gun",    "en": "a one-handed pistol",       "style": "gun",    "hand": "r", "size": 0.30, "grip": (0.5, 0.78), "rest": 115},
    "axe":    {"th": "Axe",    "en": "a one-handed battle axe",   "style": "swing",  "hand": "r", "size": 0.75, "grip": (0.5, 0.85), "rest": 0},
    "mace":   {"th": "Mace",   "en": "a spiked mace",             "style": "swing",  "hand": "r", "size": 0.65, "grip": (0.5, 0.85), "rest": 0},
}
DEFAULT_TYPE = "staff"
# "th" labels above and STYLE_TH are English UI labels (keys kept for the API); translate with _() at use.
STYLE_TH = {"swing": "Swing / slash", "thrust": "Thrust", "bow": "Draw and shoot", "gun": "Aim and fire"}


def type_of(item: dict | None) -> str:
    t = (item or {}).get("type")
    return t if t in TYPES else DEFAULT_TYPE


def style_of(weapon_type: str) -> str:
    return TYPES.get(weapon_type, TYPES[DEFAULT_TYPE])["style"]


def sample(weapon_type: str) -> Image.Image:
    """A plain stand-in picture of one weapon type (upright, handle down) for trying a type before
    an AI draws the real one. Drawn 4x and scaled down for clean edges."""
    from PIL import ImageDraw

    S = 4
    W, H = 96 * S, 512 * S
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    ol, wood, dark_wood, steel, edge, gold, gem = ((40, 30, 30), (139, 94, 52), (98, 64, 36), (196, 204, 214),
                                                   (236, 240, 246), (214, 168, 60), (70, 150, 230))
    lw = 3 * S
    cx = W // 2

    def shaft(top, bottom, half, col=wood):
        d.rounded_rectangle([cx - half, top, cx + half, bottom], radius=half, fill=col, outline=ol, width=lw)

    def poly(pts, fill):
        d.polygon([(cx + x * S, y * S) for x, y in pts], fill=fill, outline=ol, width=lw)

    if weapon_type == "staff":
        shaft(70 * S, 508 * S, 7 * S)
        d.arc([cx - 30 * S, 8 * S, cx + 26 * S, 90 * S], 120, 420, fill=ol, width=17 * S)
        d.arc([cx - 30 * S, 8 * S, cx + 26 * S, 90 * S], 120, 420, fill=wood, width=10 * S)
        d.ellipse([cx - 16 * S, 30 * S, cx + 14 * S, 60 * S], fill=gem, outline=ol, width=lw)
    elif weapon_type == "dagger":
        poly([(0, 6), (12, 60), (12, 300), (-12, 300), (-12, 60)], steel)
        poly([(0, 14), (5, 60), (5, 296), (0, 296)], edge)
        poly([(-34, 300), (34, 300), (34, 322), (-34, 322)], gold)
        shaft(322 * S, 480 * S, 11 * S, dark_wood)
        d.ellipse([cx - 16 * S, 470 * S, cx + 16 * S, 506 * S], fill=gold, outline=ol, width=lw)
    elif weapon_type == "sword":
        poly([(0, 4), (14, 40), (14, 400), (-14, 400), (-14, 40)], steel)
        poly([(0, 12), (6, 40), (6, 396), (0, 396)], edge)
        poly([(-44, 400), (44, 400), (44, 420), (-44, 420)], gold)
        shaft(420 * S, 488 * S, 9 * S, dark_wood)
        d.ellipse([cx - 14 * S, 480 * S, cx + 14 * S, 508 * S], fill=gold, outline=ol, width=lw)
    elif weapon_type == "spear":
        shaft(100 * S, 508 * S, 7 * S)
        poly([(0, 4), (20, 60), (8, 104), (-8, 104), (-20, 60)], steel)
        poly([(0, 12), (8, 60), (3, 100), (0, 100)], edge)
        poly([(-12, 104), (12, 104), (12, 116), (-12, 116)], gold)
    elif weapon_type == "bow":
        box = [cx - 40 * S, 14 * S, cx + 120 * S, 498 * S]          # the bow is the left side of this ellipse
        d.line([(cx + 19 * S, 22 * S), (cx + 19 * S, 490 * S)], fill=(120, 110, 100), width=2 * S)   # string
        d.arc(box, 105, 255, fill=ol, width=15 * S)
        d.arc(box, 105, 255, fill=wood, width=9 * S)
        d.rounded_rectangle([cx - 46 * S, 226 * S, cx - 30 * S, 286 * S], radius=6 * S, fill=dark_wood, outline=ol, width=lw)
    elif weapon_type == "gun":
        d.rounded_rectangle([cx - 13 * S, 150 * S, cx + 13 * S, 330 * S], radius=6 * S, fill=(90, 96, 108), outline=ol, width=lw)
        d.rounded_rectangle([cx - 26 * S, 270 * S, cx + 26 * S, 360 * S], radius=8 * S, fill=(70, 74, 84), outline=ol, width=lw)
        poly([(-24, 350), (22, 350), (40, 470), (0, 480)], dark_wood)
        d.arc([cx - 4 * S, 345 * S, cx + 40 * S, 405 * S], 270, 450, fill=ol, width=4 * S)   # trigger guard
    elif weapon_type == "axe":
        shaft(30 * S, 508 * S, 8 * S)
        poly([(6, 40), (46, 14), (46, 150), (6, 124)], steel)
        poly([(40, 18), (46, 14), (46, 150), (40, 146)], edge)
        poly([(-6, 60), (-24, 70), (-24, 104), (-6, 112)], steel)
    elif weapon_type == "mace":
        shaft(110 * S, 508 * S, 8 * S, dark_wood)
        for ang in range(0, 360, 45):
            a = math.radians(ang)
            tip = (cx + math.sin(a) * 52 * S, 62 * S - math.cos(a) * 52 * S)
            base = [(cx + math.sin(a + s) * 30 * S, 62 * S - math.cos(a + s) * 30 * S) for s in (-0.3, 0.3)]
            d.polygon([tip, *base], fill=steel, outline=ol)
        d.ellipse([cx - 36 * S, 26 * S, cx + 36 * S, 98 * S], fill=(150, 156, 168), outline=ol, width=lw)
    else:
        raise ValueError(f"Unknown weapon type: {weapon_type}")
    out = im.resize((W // S, H // S), Image.LANCZOS)
    return out.crop(out.getchannel("A").getbbox())


def prepare(img: Image.Image) -> Image.Image:
    """A cut-out weapon picture -> the weapon alone, trimmed and standing upright.

    Keeps the biggest solid blob (drops an inset's frame and caption), then straightens it if it was
    drawn at a slant, keeping the end that was higher on top. Draw weapons upright, handle down."""
    from scipy import ndimage

    rgba = np.asarray(img.convert("RGBA"))
    a = rgba[..., 3] > 64
    if not a.any():
        raise ValueError("The weapon image is empty after background removal.")
    labels, _ = ndimage.label(ndimage.binary_dilation(a, iterations=2))
    labels = np.where(a, labels, 0)
    def oriented_fill(mask: np.ndarray) -> float:
        """Share of the blob's box measured along its own long axis: a thin staff lying diagonally
        fills its rotated box well, while a frame border stays hollow at any angle."""
        ys, xs = np.nonzero(mask)
        if len(xs) < 3:
            return 1.0
        pts = np.stack([xs - xs.mean(), ys - ys.mean()])
        _, vecs = np.linalg.eigh(np.cov(pts))
        u, v = vecs.T @ pts
        return len(xs) / max(1.0, (np.ptp(u) + 1) * (np.ptp(v) + 1))

    best, best_n, biggest, biggest_n = 0, 0, 0, 0
    for i, sl in enumerate(ndimage.find_objects(labels), start=1):
        if sl is None:
            continue
        m = labels[sl] == i
        n = int(m.sum())
        if n > biggest_n:
            biggest, biggest_n = i, n
        if n > best_n and oriented_fill(m) > 0.12:     # a thin frame border has a very low fill
            best, best_n = i, n
    keep = labels == (best or biggest)                 # never fall back to the background
    arr = rgba.copy()
    arr[..., 3] = np.where(keep, arr[..., 3], 0)
    ys, xs = np.nonzero(keep)
    out = Image.fromarray(arr, "RGBA").crop((xs.min(), ys.min(), xs.max() + 1, ys.max() + 1))

    # Long axis by PCA; straighten if it leans more than 12 degrees.
    pts = np.stack([xs - xs.mean(), ys - ys.mean()])
    vals, vecs = np.linalg.eigh(np.cov(pts))
    vx, vy = vecs[:, int(np.argmax(vals))]
    if vy > 0:
        vx, vy = -vx, -vy                             # point at the end that is higher on the page
    lean = math.degrees(math.atan2(vx, -vy))          # clockwise from straight up
    if abs(lean) > 12:
        out = out.rotate(lean, resample=Image.BICUBIC, expand=True)
        bbox = out.getchannel("A").point(lambda v: 255 if v > 64 else 0).getbbox()
        out = out.crop(bbox)
    return out


def squash_of(weapon_type: str) -> float:
    return TYPES.get(weapon_type, TYPES[DEFAULT_TYPE]).get("squash", 1.0)


def place(weapon: Image.Image, grip: tuple[float, float], size: float, height_px: float,
          hand: tuple[float, float], angle: float, squash: float = 1.0) -> tuple[Image.Image, float, float]:
    """Scale and rotate the upright weapon so its grip sits on `hand`. squash: width factor (see TYPES).

    hand: pixels from the sprite pivot (feet), y down. angle: degrees clockwise from straight up.
    Returns (image, pivot x, pivot y): where the sprite pivot lies in the returned image's pixels,
    the same convention the body frames use."""
    k = size * height_px / weapon.height
    w = weapon.convert("RGBa").resize((max(1, round(weapon.width * k * squash)), max(1, round(weapon.height * k))),
                                      Image.LANCZOS)
    vx, vy = grip[0] * w.width - w.width / 2, grip[1] * w.height - w.height / 2   # grip from the centre
    a = math.radians(angle)
    rx, ry = vx * math.cos(a) - vy * math.sin(a), vx * math.sin(a) + vy * math.cos(a)
    rot = w.rotate(-angle, resample=Image.BICUBIC, expand=True).convert("RGBA")
    # centre of the rotated image = hand - rotated grip offset; pivot is `hand` px away from the grip
    cx, cy = rot.width / 2, rot.height / 2
    return rot, cx + rx - hand[0], cy + ry - hand[1]


def anchor_key(action: str, view: str, i: int, weapon_type: str = DEFAULT_TYPE) -> str:
    """Key of one frame's hand correction. Kept per weapon type (a bow sits in the other hand, a spear
    points another way); the staff keeps the plain keys corrections were saved under before types."""
    base = f"{action}_{view}_{i}"
    return base if weapon_type == DEFAULT_TYPE else f"{weapon_type}:{base}"


def anchors(action: str, view: str, n_frames: int, body: str, overrides: dict | None = None,
            weapon_type: str = DEFAULT_TYPE) -> list[dict]:
    """Hand position / weapon angle / draw order for each of the n drawn frames: the pose guide's
    frame at the same point in time for this weapon type, plus the user's correction for that frame."""
    t = TYPES.get(weapon_type, TYPES[DEFAULT_TYPE])
    guide = pose_guide.hand_frames(action, view, body, style=t["style"], hand=t["hand"], rest=t["rest"])
    overrides = overrides or {}
    out = []
    for i in range(n_frames):
        g = guide[round(i * (len(guide) - 1) / max(1, n_frames - 1))]
        o = overrides.get(anchor_key(action, view, i, weapon_type)) or {}
        out.append({"x": g["x"] + o.get("dx", 0.0), "y": g["y"] + o.get("dy", 0.0),
                    "angle": g["angle"] + o.get("da", 0.0),
                    "behind": o["behind"] if "behind" in o else g["behind"],
                    "auto": g, "override": o})
    return out


_UPRIGHT = {
    "staff": "handle or grip at the bottom and the tip or head at the top",
    "bow": "the bow seen from the side, limbs up and down, string on the right, grip in the middle",
    "gun": "barrel pointing straight up, grip at the bottom",
}


def prompt(description: str, weapon_type: str = DEFAULT_TYPE) -> str:
    """Prompt for drawing one weapon on its own, ready for prepare(). A bow stands with its string
    on the right; a gun with the barrel pointing up."""
    return (
        f"Game item sprite for a 2D isometric RPG, in a {sprite2d.ART_STYLE}: {description}. "
        f"Draw ONLY the weapon: no hands, no character, no stand. One single weapon, standing perfectly upright "
        f"(vertical), {_UPRIGHT.get(weapon_type, _UPRIGHT['staff'])}, centred, the full length "
        f"visible, nothing cropped. Same art style as the attached character: clean dark outlines, flat colours. "
        f"Plain pure white background, no shadow, no text, no labels, no frame or border. High resolution."
    )
