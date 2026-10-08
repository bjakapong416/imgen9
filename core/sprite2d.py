"""
2D route: character sprites drawn by an image AI (Gemini / ChatGPT), assembled the way classic 2D
online games do it.

Most classic 2D game sprites use only TWO drawings for all 8 directions - a front three-quarter
view and a back three-quarter view - and mirror them:
    S, SW, W  <- front 3/4 drawing        SE, E    <- front 3/4, mirrored
    NW        <- back 3/4 drawing         N, NE    <- back 3/4, mirrored
So each action needs two AI-drawn strips ("sheets"): frames in one row, facing bottom-left
(front 3/4) and top-left (back 3/4).

This module turns those sheets into one sprite (.spr + .act) in renders/<project>/<slug>/sprite/, which the
frame-by-frame view, Playtest, image downloads and the web-game bundle all use unchanged.
"""

from __future__ import annotations

import hashlib
import json
import math
import shutil
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

from .i18n import N_, _, tr
from .spr_act import ACT_DIRECTIONS, write_act, write_spr
from .views import split_sheet

CLASSIC_PX_PER_M = 82 / 1.7   # classic 2D game size: ~82 px above the feet for a 1.7 m character
# Sprite detail: pixels per metre as a multiple of the classic size. Painted, detailed art turns to
# mush at ~80 px tall; x2 / x3 keep it (bigger files). English labels: translate with _() at use.
DETAIL = {1: "Classic (~80 px tall)", 2: "HD x2 (~160 px)", 3: "HD x3 (~240 px)"}
TRANSPARENT = (255, 0, 255)
FRAME_GROW = 2

# What we ask the image AI for, per action. Image AIs draw even grids far more reliably than rows
# of different lengths, so every action has GRID_COLS frames; each action's total time stays close
# to the usual length of that action in classic 2D games (ms x frames).
GRID_COLS = 6
ACTIONS = {
    "idle":   {"frames": 6, "ms": 120, "loop": True,  "th": "Idle",   "motion": "idle breathing loop: a subtle up-and-down breathing motion, standing in place, loops seamlessly"},
    "walk":   {"frames": 6, "ms": 100, "loop": True,  "th": "Walk",   "motion": "walk cycle in place with big, clearly readable steps (exaggerate the leg poses so they read at small size). "
                                                                                 "Frame 1: LEFT foot forward on its heel, right foot back on its toes, both feet on the ground. Frame 2: left foot planted with the knee slightly bent, "
                                                                                 "right foot off the ground swinging forward past the left leg, right knee bent. Frame 3: left leg straight under the body, "
                                                                                 "right leg reaching forward, its foot about to land. Frame 4: RIGHT foot forward, left foot back, both feet on the ground "
                                                                                 "(the mirror of frame 1). Frame 5: right foot planted, left foot swinging forward past the right leg. Frame 6: right leg straight, left leg reaching forward. "
                                                                                 "Arms swing opposite to the legs; a held weapon stays in the hand; long hair, capes and skirts sway a little with each step. Frames 1 and 4 must clearly show different legs in front"},
    "attack": {"frames": 6, "ms": 130, "loop": False, "th": "Attack", "motion": "basic attack: frames 1-2 wind-up, frames 3-4 strike (the hit), frames 5-6 recover to the ready pose"},
    "cast":   {"frames": 6, "ms": 120, "loop": False, "th": "Magic attack", "motion": "magic attack (casting a spell): frames 1-2 "
                                                                                 "gather power with the hand raised, frame 3 hand at its highest, frame 4 thrust the hand forward at "
                                                                                 "the target to release the spell (the release), frames 5-6 recover to the ready pose. Draw NO spell "
                                                                                 "effect, glow, light or projectile: effects are added separately"},
    "damage": {"frames": 6, "ms": 80,  "loop": False, "th": "Hurt",   "motion": "getting hit: frames 1-3 flinch backward from a blow, frames 4-6 recover to standing"},
    "die":    {"frames": 6, "ms": 240, "loop": False, "th": "Die",    "motion": "death: staggers and falls to the ground; the last frame lies on the ground"},
    # Optional extras, after the five every character has (the order of the sprite's action types).
    "run":    {"frames": 6, "ms": 70,  "loop": True,  "th": "Run",    "motion": "run cycle in place: a full running stride, body leaning forward, knees lifted high, "
                                                                                 "elbows bent pumping opposite the legs; frames 1-3 one step, 4-6 the other leg (the mirror), loops seamlessly"},
    "sit":    {"frames": 6, "ms": 200, "loop": True,  "th": "Sit",    "motion": "sitting on the ground resting, knees up, hands on the knees, a slow breathing loop "
                                                                                 "(only the chest and shoulders move a little); the same seated pose in every frame"},
    "sleep":  {"frames": 6, "ms": 260, "loop": True,  "th": "Sleep",  "motion": "asleep, lying curled on the side on the ground with eyes closed, a slow breathing loop "
                                                                                 "(the body rises and falls a little); the same lying pose in every frame"},
    "happy":  {"frames": 6, "ms": 110, "loop": True,  "th": "Happy",  "motion": "cheering happily: frame 1 crouch, frame 2 spring up with both arms raised, frame 3 at the top "
                                                                                 "with legs tucked, frame 4 coming down, frame 5 land with fists pumped, frame 6 a wave; loops"},
}
ACTION_ORDER = ["idle", "walk", "attack", "cast", "damage", "die", "run", "sit", "sleep", "happy"]
EXTRA_ACTIONS = ["run", "sit", "sleep", "happy"]   # optional, chosen per project
BASE_ACTIONS = ["idle", "walk", "attack", "damage", "die"]   # every character; "cast" only for magic users
ATTACKS = ("attack", "cast")                                   # physical and magic: both carry the "atk" event


def actions_for(magic: bool, extras: list[str] | tuple = ()) -> list[str]:
    return [a for a in ACTION_ORDER if a in BASE_ACTIONS or (magic and a == "cast") or a in extras]
VIEWS2D = {
    "front": {"th": "Front 3/4", "facing": "facing toward the bottom-left of the image (front three-quarter view, we see the face)"},
    "side":  {"th": "Side",      "facing": "facing LEFT in a pure side view (profile: we see the left side of the face and body)"},
    "back":  {"th": "Back 3/4",  "facing": "facing toward the top-left of the image (back three-quarter view, we see the back of the head)"},
}
BASE_VIEWS = ["front", "back"]          # the two drawings; "side" is an optional third for W/E


def views_for(side: bool = False) -> list[str]:
    return ["front", "side", "back"] if side else list(BASE_VIEWS)


# Direction mapping: which drawing, mirrored or not.
DIR_SOURCE = {"S": ("front", False), "SW": ("front", False), "W": ("front", False), "NW": ("back", False),
              "N": ("back", True), "NE": ("back", True), "E": ("front", True), "SE": ("front", True)}


def dir_source(views: list[str] | None = None) -> dict[str, tuple[str, bool]]:
    """Which drawing each of the 8 directions uses. With a side view, W is the side drawing and E
    its mirror, so walking left/right shows a true profile instead of the front 3/4 drawing."""
    out = dict(DIR_SOURCE)
    if views and "side" in views:
        out["W"], out["E"] = ("side", False), ("side", True)
    return out


def sheet_path(project_dir: Path, action: str, view: str) -> Path:
    return project_dir / "sheets" / f"{action}_{view}.png"


def feet_anchor(img: Image.Image) -> tuple[float, float, int]:
    """(x, y, height): middle of the lowest opaque band (the feet) and the visible height."""
    a = np.asarray(img.getchannel("A")) > 128
    ys = np.flatnonzero(a.any(axis=1))
    if not len(ys):
        return img.width / 2, img.height, 0
    top, bottom = ys[0], ys[-1]
    band = max(2, int((bottom - top + 1) * 0.05))
    xs = np.flatnonzero(a[bottom - band + 1:bottom + 1].any(axis=0))
    return (xs[0] + xs[-1] + 1) / 2, bottom + 1, int(bottom - top + 1)


IN_PLACE = ("idle", "walk")    # loops where the feet move but the body must not drift


def strip_anchors(frames: list[Image.Image], action: str) -> list[tuple[float, float, int]]:
    """feet_anchor for every frame of one strip, steadied for in-place loops.

    The lowest pixels of a walk frame are both feet at contact but only the planted foot when the
    other is lifted, so the feet middle jumps sideways from frame to frame and the body slides
    (up to 20 % of its height on real AI strips). For idle and walk, x follows the head and
    shoulders instead (top quarter of the figure), shifted by the strip's median head-to-feet
    offset so the pivot still sits between the feet. y stays on the ground."""
    feet = [feet_anchor(f) for f in frames]
    if action not in IN_PLACE or len(frames) < 2:
        return feet
    body = []
    for f in frames:
        a = np.asarray(f.getchannel("A")) > 128
        ys = np.flatnonzero(a.any(axis=1))
        if not len(ys):
            body.append(f.width / 2)
            continue
        top = a[ys[0]:ys[0] + max(1, (ys[-1] - ys[0] + 1) // 4)]
        body.append(float(np.nonzero(top)[1].mean()) + 0.5)
    shift = float(np.median([fx - bx for (fx, _, _), bx in zip(feet, body)]))
    return [(bx + shift, fy, h) for (_, fy, h), bx in zip(feet, body)]


FLIP_MARGIN = 1.8


def _facing_feat(f: Image.Image) -> np.ndarray | None:
    a = np.asarray(f.getchannel("A")) > 128
    ys, xs = np.nonzero(a)
    if not len(ys):
        return None
    c = np.asarray(f.crop((xs.min(), ys.min(), xs.max() + 1, ys.max() + 1)).resize((48, 48), Image.BILINEAR), dtype=float) / 255
    return c[..., :3] * c[..., 3:4]


def facing_flips(frames: list[Image.Image], reference: list[Image.Image] | None = None) -> list[int]:
    """Frames (0-based) drawn facing the other way from the rest of the strip.

    AIs sometimes turn a frame around mid-strip (a walk: frames 1-2 facing left, 3-6 right), and
    once the strip is mirrored for the other directions, the character walking right turns left
    every cycle. Each frame is compared with every other, as drawn and mirrored; the orientation
    set that makes the strip most alike wins, the larger group is kept as drawn. A frame is only
    reported when mirroring makes it clearly closer to the others (x1.8; real turned frames score
    1.9-14, a staff drawn in some frames and not others 1.7).

    Which group is the turned one: with `reference` (the same view's idle strip, which AIs draw
    facing one way), the group that matches it as drawn; without, the larger group. A real mage
    walk had 4 of 6 frames turned, so the majority alone gets it backwards. Whether the whole
    strip faces the right way is the user's call (mirror_frames on every frame)."""
    n = len(frames)
    if not 3 <= n <= 10:
        return []
    feats = [_facing_feat(f) for f in frames]
    if any(f is None for f in feats):
        return []
    pair = lambda x, y: float(np.abs(x - y).mean())
    same = np.array([[pair(x, y) for y in feats] for x in feats])
    cross = np.array([[pair(x[:, ::-1], y) for y in feats] for x in feats])   # x mirrored vs y as drawn
    best = None
    for bits in range(1 << (n - 1)):
        s = [0] + [(bits >> k) & 1 for k in range(n - 1)]
        cost = sum(same[i, j] if s[i] == s[j] else cross[i, j] for i in range(n) for j in range(i + 1, n))
        if best is None or cost < best[0]:
            best = (cost, s)
    s = best[1]
    ref = [r for r in (_facing_feat(f) for f in reference or []) if r is not None]
    if ref and 0 < sum(s) < n:
        keep0 = np.mean([pair(feats[i][:, ::-1] if s[i] else feats[i], r) for i in range(n) for r in ref])
        keep1 = np.mean([pair(feats[i] if s[i] else feats[i][:, ::-1], r) for i in range(n) for r in ref])
        if keep1 < keep0:
            s = [1 - b for b in s]
    elif sum(s) * 2 > n or (sum(s) * 2 == n and s[0]):
        s = [1 - b for b in s]
    out = []
    for i in (i for i in range(n) if s[i]):
        others = [j for j in range(n) if j != i]
        # frame i as drawn / mirrored, against every other frame as it ends up
        as_drawn = np.mean([cross[i, j] if s[j] else same[i, j] for j in others])
        mirrored = np.mean([same[i, j] if s[j] else cross[i, j] for j in others])
        if as_drawn >= FLIP_MARGIN * mirrored:
            out.append(i)
    return out


def mirror_frames(strip: Image.Image, which: list[int] | None = None) -> Image.Image:
    """The strip with these frames (all when None) mirrored in place, frame order kept."""
    boxes: list = []
    frames = frames_of(strip, boxes)
    out = Image.new("RGBA", strip.size, (0, 0, 0, 0))
    for i, (f, b) in enumerate(zip(frames, boxes)):
        flip = which is None or i in which
        out.alpha_composite(f.transpose(Image.Transpose.FLIP_LEFT_RIGHT) if flip else f, (int(b[0]), int(b[1])))
    return out


def frames_of(sheet: Image.Image, boxes: list | None = None) -> list[Image.Image]:
    # Small grow: AI strips put frames close together (a dagger nearly touches the next frame), and
    # the turnaround default of 0.6 % would merge them. Lying-down frames are short: be lenient.
    figs, _ = split_sheet(sheet, alpha_threshold=64, min_height_frac=0.3, grow=FRAME_GROW, boxes=boxes)
    return figs


def all_slots(actions: list[str] | None = None, views: list[str] | None = None) -> list[tuple[str, str]]:
    """Row order of the all-actions grid: each action's front 3/4 row, then its back 3/4 row."""
    return [(a, v) for a in (actions or BASE_ACTIONS) for v in (views or BASE_VIEWS)]


def _row_cuts(profile: np.ndarray, n_rows: int) -> list[int]:
    """The n_rows - 1 row borders: the emptiest set of lines that keeps every row 0.5-1.6 x the
    average height. Chosen together (dynamic programming), because AI rows drift: on real sheets the
    top rows are taller, and by the fifth row the gap is half a row away from the even division."""
    h = len(profile)
    ch = h / n_rows
    lo, hi = max(1, int(0.5 * ch)), int(1.6 * ch)
    pos = np.arange(h)
    inf = np.inf
    # cost of a cut at line c; a small pull toward the even division only breaks ties between gaps
    best = np.where((pos >= lo) & (pos <= hi), profile + 2 * np.abs(pos - ch) / ch, inf)
    back = []
    for k in range(2, n_rows):
        prev, arg = np.full(h, inf), np.zeros(h, dtype=np.int64)
        for w in range(lo, hi + 1):
            cand = np.full(h, inf)
            cand[w:] = best[:h - w]
            better = cand < prev
            prev[better], arg[better] = cand[better], (pos - w)[better]
        best = prev + profile + 2 * np.abs(pos - k * ch) / ch
        back.append(arg)
    best = np.where((h - pos >= lo) & (h - pos <= hi), best, inf)
    if not np.isfinite(best).any():
        return [round(i * ch) for i in range(1, n_rows)]
    cuts = [int(np.argmin(best))]
    for arg in reversed(back):
        cuts.append(int(arg[cuts[-1]]))
    cuts.reverse()
    out = []
    for c in cuts:                     # middle of the emptiest run around each cut
        lo_c = hi_c = c
        while lo_c > 0 and profile[lo_c - 1] <= profile[c]:
            lo_c -= 1
        while hi_c < h - 1 and profile[hi_c + 1] <= profile[c]:
            hi_c += 1
        out.append((lo_c + hi_c) // 2)
    return out


def split_grid(sheet: Image.Image, n_rows: int, alpha_threshold: int = 16) -> list[Image.Image]:
    """Cut a sheet of `n_rows` rows of frames into one strip per row, top to bottom.

    Only the row count is trusted: AIs often draw 7 frames when asked for 6, so columns are left to
    frames_of(). Row borders are the emptiest pixel lines near each even division, so a border runs
    through the gap between rows rather than through feet or hair. Each blob goes whole to the row
    holding its centre (a staff tip reaching up stays with its figure); only a blob taller than
    ~1.5 rows (figures the AI drew overlapping across rows) is cut at the border."""
    from scipy import ndimage

    rgba = np.asarray(sheet.convert("RGBA"))
    a = rgba[..., 3] > alpha_threshold
    if not a.any():
        raise ValueError("The image is empty after background removal.")
    h = a.shape[0]
    ch = h / n_rows
    profile = np.convolve(a.sum(axis=1), np.ones(5) / 5, mode="same")
    cuts = [0] + _row_cuts(profile, n_rows) + [h]
    row_at = np.searchsorted(cuts, np.arange(h), side="right") - 1

    labels, _ = ndimage.label(ndimage.binary_dilation(a, iterations=FRAME_GROW))
    labels = np.where(a, labels, 0)
    owner = np.full(a.shape, -1, dtype=np.int16)
    for i, sl in enumerate(ndimage.find_objects(labels), start=1):
        if sl is None:
            continue
        m = labels[sl] == i
        if sl[0].stop - sl[0].start > 1.5 * ch:
            owner[sl][m] = np.broadcast_to(row_at[sl[0]][:, None], m.shape)[m]
        else:
            owner[sl][m] = row_at[(sl[0].start + sl[0].stop) // 2]
    out = []
    for r in range(n_rows):
        keep = owner == r
        if not keep.any():
            raise ValueError(f"Expected {n_rows} rows of frames, but row {r + 1} is empty.")
        ys, xs = np.nonzero(keep)
        y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
        arr = rgba[y0:y1, x0:x1].copy()
        arr[..., 3] = np.where(keep[y0:y1, x0:x1], arr[..., 3], 0)
        out.append(Image.fromarray(arr, "RGBA"))
    return out


def split_all(sheet: Image.Image, actions: list[str] | None = None, views: list[str] | None = None) -> dict[tuple[str, str], Image.Image]:
    """Cut the all-actions grid (see all_slots) into one strip per slot."""
    slots = all_slots(actions, views)
    return dict(zip(slots, split_grid(sheet, len(slots))))


def feet_stride_var(frames: list[Image.Image]) -> float:
    """How much the width of the feet changes over a walk, relative to the median width.

    A walk opens the feet wide at each contact pose and closes them when the legs pass, so the
    width swings a lot (Blender walks: 1.4-2.6). An AI strip that repeats one stepping pose barely
    changes (0.2-0.5). Width only, so it can't tell left from right, just whether the legs move."""
    widths = []
    for f in frames:
        a = np.asarray(f.getchannel("A")) > 128
        ys = np.flatnonzero(a.any(axis=1))
        if not len(ys):
            continue
        top, bottom = ys[0], ys[-1]
        feet = a[bottom - int((bottom - top + 1) * 0.08):bottom + 1]
        xs = np.flatnonzero(feet.any(axis=0))
        widths.append((xs[-1] - xs[0] + 1) / (bottom - top + 1))
    if len(widths) < 3:
        return 0.0
    w = np.array(widths)
    return float((w.max() - w.min()) / (np.median(w) or 1))


WALK_MIN_STRIDE_VAR = 0.6


def repeated_leg_frames(frames: list[Image.Image], action: str = "walk") -> list[int]:
    """Frames (1-based) whose legs look the same as the next frame's.

    The feet-width test misses a strip that changes pose once and then holds it: a real AI walk had
    frames 2-5 all on the same step, with only frames 1 and 6 different. Here each frame is scaled
    to its height around the steadied anchor, and the legs (lower 37 %) are compared with the next
    frame's. A pair counts as the same pose when it differs by under 30 % of the strip's biggest
    change and under 0.05 (mean alpha difference); walks that read well stay above ~0.1."""
    if len(frames) < 3:
        return []
    legs = []
    for f, (fx, fy, h) in zip(frames, strip_anchors(frames, action)):
        w = h * 0.8
        c = f.getchannel("A").crop((int(fx - w / 2), int(fy - h), int(fx + w / 2), int(fy))).resize((64, 96), Image.BILINEAR)
        legs.append(np.asarray(c, dtype=float)[60:] / 255)
    n = len(legs)
    step = [float(np.abs(legs[i] - legs[(i + 1) % n]).mean()) for i in range(n)]
    limit = min(0.05, 0.3 * max(step))
    same = [i for i in range(n - 1) if step[i] < limit]   # the wrap-around pair is allowed to match
    return [i + 1 for i in same] if len(same) >= 2 else []


# "Still the same character" check. Calibrated on real AI rows: a frame drawn facing the wrong way
# or in another outfit differs by >= 0.15 in colour mix; normal frames stay under 0.10. In idle and
# walk the silhouette barely changes, so a part appearing or vanishing (a dropped staff: +14 %)
# shows in the covered area; attacks change area on purpose and are not checked for it.
ODD_COLOUR = 0.12
ODD_AREA = 0.09


def odd_frames(frames: list[Image.Image], action: str) -> dict:
    """Frames that don't look like the rest of their row: {"colour": [i...], "shape": [i...]}."""
    hists, areas = [], []
    for f in frames:
        a = np.asarray(f.convert("RGBA"))
        m = a[..., 3] > 128
        ys = np.flatnonzero(m.any(axis=1))
        if not len(ys):
            hists.append(np.zeros(64)); areas.append(0.0)
            continue
        h = ys[-1] - ys[0] + 1
        q = (a[m][:, :3] // 64).astype(int)
        hist = np.bincount(q[:, 0] * 16 + q[:, 1] * 4 + q[:, 2], minlength=64).astype(float)
        hists.append(hist / hist.sum())
        areas.append(m.sum() / (h * h))
    if len(frames) < 3:
        return {"colour": [], "shape": []}
    hs = np.array(hists)
    med = np.median(hs, axis=0)
    med /= med.sum() or 1
    # A fall shows other sides of the body on purpose (lying on the back shows the face).
    colour = [] if action == "die" else [i for i, h in enumerate(hs) if np.abs(h - med).sum() / 2 >= ODD_COLOUR]
    shape = []
    if action in ("idle", "walk"):
        ref = float(np.median(areas)) or 1.0
        shape = [i for i, a in enumerate(areas) if abs(a / ref - 1) >= ODD_AREA]
    return {"colour": colour, "shape": shape}


def jitter_report(frames: list[Image.Image], action: str) -> dict:
    """How usable the AI drawing is: frame count, height spread (%), and for walks whether the legs
    actually alternate. `issues` says what to fix in the build request's language; `issue_keys` keeps
    the English text + values so the sheets card shows them in the viewer's language (i18n.tr)."""
    if not frames:
        return {"frames": 0}
    hs = [feet_anchor(f)[2] for f in frames]
    ref = np.median(hs) or 1
    standing = hs[:2] if action == "die" else hs[:1] if action == "happy" else hs   # falls and jumps change height on purpose
    spread = (max(standing) - min(standing)) / ref * 100
    want = ACTIONS[action]["frames"]
    keys = []

    def issue(text: str, **kw) -> None:
        keys.append([text, kw])

    if not want - 1 <= len(frames) <= want + 2:            # AIs often draw a frame more or less; that plays fine
        issue(N_("Got {n} frames (asked for {want})"), n=len(frames), want=want)
    if spread >= 12:
        issue(N_("Body size wobbles {spread}%"), spread=f"{spread:.0f}")
    out = {"frames": len(frames), "expected": want, "height_spread_pct": round(float(spread), 1)}
    if action in ("walk", "run"):
        var = feet_stride_var(frames)
        out["stride_var"] = round(var, 2)
        held = repeated_leg_frames(frames)
        out["held_frames"] = held
        if var < WALK_MIN_STRIDE_VAR:
            issue(N_("Legs barely alternate (the same step repeats)"))
        elif held:
            issue(N_("Frames {a}–{b} have the same leg pose (no step)"), a=held[0], b=held[-1] + 1)
    odd = odd_frames(frames, action)
    nums = lambda ix: ", ".join(str(i + 1) for i in ix)
    if odd["colour"]:
        issue(N_("Frames {list} have different colours from the rest (wrong view or outfit?)"), list=nums(odd["colour"]))
    if odd["shape"]:
        issue(N_("Frames {list} differ in shape: a part appears or disappears (e.g. the weapon)"), list=nums(odd["shape"]))
    out["odd_frames"] = sorted(set(odd["colour"]) | set(odd["shape"]))
    out["issue_keys"] = keys
    out["issues"] = [tr(k) for k in keys]
    out["ok"] = not keys
    return out


# Per-frame corrections, kept apart from the AI drawing so they can always be undone. One list per
# strip ("<action>_<view>"), in play order: {"src": i (frame of the drawing) | "file": name (a
# replacement frame in sheets/frames/), "flip": bool, "dx": px, "dy": px (move the figure relative
# to its feet, in drawing pixels), "hold": n (show this frame n times: a pause)}. No list = as drawn.
MAX_HOLD = 4


def frame_edit_dir(project_dir: Path) -> Path:
    return project_dir / "sheets" / "frames"


def apply_edits(frames: list[Image.Image], edits: list[dict] | None, project_dir: Path) -> tuple[list[Image.Image], list[dict]]:
    """The strip as edited: (frames in play order, per frame {"dx", "dy", "hold"})."""
    if not edits:
        return frames, [{"dx": 0, "dy": 0, "hold": 1} for _ in frames]
    out, meta = [], []
    for e in edits:
        if "file" in e:
            f = frame_edit_dir(project_dir) / Path(str(e["file"])).name
            if not f.exists():
                continue
            im = Image.open(f).convert("RGBA")
        else:
            i = int(e.get("src", -1))
            if not 0 <= i < len(frames):
                continue
            im = frames[i]
        if e.get("flip"):
            im = im.transpose(Image.FLIP_LEFT_RIGHT)
        out.append(im)
        meta.append({"dx": float(e.get("dx", 0)), "dy": float(e.get("dy", 0)),
                     "hold": max(1, min(MAX_HOLD, int(e.get("hold", 1))))})
    return (out, meta) if out else (frames, [{"dx": 0, "dy": 0, "hold": 1} for _ in frames])


def play_order(n: int, holds: list[int], action: str) -> list[tuple[int, int]]:
    """(frame index, event) per played frame: holds repeat a frame; the event sits on its first copy.
    Events: "atk" (0) at the usual hit point of attacks (59 % in), "step" (1) twice per walk cycle."""
    out = []
    for i in range(n):
        event = -1
        if action in ATTACKS and i == round((n - 1) * 0.59):
            event = 0
        if action == "walk" and i in (0, n // 2):
            event = 1
        for rep in range(holds[i] if i < len(holds) else 1):
            out.append((i, event if rep == 0 else -1))
    return out


def defringe(arr: np.ndarray) -> None:
    """Outline pixels still mixed with the drawing's light background (a pale halo after the cut)
    take the colour of the nearest pixel inside the figure. Works in place on hard-alpha RGBA."""
    from scipy import ndimage

    solid = arr[..., 3] > 0
    inner = ndimage.binary_erosion(solid, iterations=2)
    if not inner.any():
        return
    edge = solid & ~inner
    _, (iy, ix) = ndimage.distance_transform_edt(~inner, return_indices=True)
    rgb = arr[..., :3].astype(np.int16)
    near = rgb[iy, ix]
    lum = rgb.mean(axis=2)
    pale = edge & (lum > near.mean(axis=2) + 35) & (rgb.max(axis=2) - rgb.min(axis=2) < 60)
    arr[..., :3][pale] = near[pale].astype(np.uint8)


def game_points(prepared: dict, target_h: float, body: str, weapon_hand: str = "r",
                dsrc: dict | None = None, ppm: float = CLASSIC_PX_PER_M) -> dict:
    """Numbers a game needs besides the frames, in sprite pixels relative to the feet (y down) and
    in metres: a hitbox from the standing frame, a ground shadow, and per direction the point where
    an attack or spell leaves the hand at the hit frame (from the pose guide the AI drew from)."""
    from . import pose_guide
    dsrc = dsrc or DIR_SOURCE
    key = ("idle", "front") if ("idle", "front") in prepared else next(iter(prepared))
    im, fx, fy = prepared[key][0]
    a = np.asarray(im)[..., 3] > 0
    ys, xs = np.nonzero(a)
    x0, x1, y0, y1 = xs.min() - fx, xs.max() + 1 - fx, ys.min() - fy, ys.max() + 1 - fy
    feet = a[max(0, int(fy) - 4):int(fy) + 1]
    fxs = np.flatnonzero(feet.any(axis=0)) if feet.size else np.array([])
    foot_w = (fxs.max() - fxs.min() + 1) if len(fxs) else (x1 - x0) * 0.5
    out = {
        "pixels_per_metre": round(ppm, 2),
        "hitbox": {"x": round(float(x0)), "y": round(float(y0)), "w": round(float(x1 - x0)), "h": round(float(y1 - y0))},
        "shadow": {"rx": round(float(max(foot_w, (x1 - x0) * 0.5) * 0.6), 1), "ry": round(float(max(foot_w, (x1 - x0) * 0.5) * 0.2), 1)},
        "height_m": round(float(y1 - y0) / ppm, 2),
        "cast_point": {},
    }
    act = "cast" if any(k[0] == "cast" for k in prepared) else "attack"
    for view in ("front", "side", "back"):
        dirs = [d for d, (v, _m) in dsrc.items() if v == view]
        if not dirs:
            continue
        frames = pose_guide.hand_frames(act if act in pose_guide.POSES else "attack", view, body, hand=weapon_hand)
        if not frames:
            continue
        hit = frames[round((len(frames) - 1) * 0.59)]
        x, y = hit["x"] * target_h, hit["y"] * target_h
        for d in dirs:
            mirror = dsrc[d][1]
            out["cast_point"][d] = [round(-x if mirror else x), round(y)]
    return out


# Headgear: a hat/helmet laid on the head of every frame, found from the drawing itself (the top of
# the figure), so it follows bobbing and leaning. Hidden while lying down, where there is no top of
# the head to sit on. Sizes and offsets are fractions of the standing height (survive re-scaling).
HAT_HIDDEN = ("die", "sleep")
HAT_SINK = 0.45            # share of the hat that goes down over the hair


def head_point(im: Image.Image, fx: float, fy: float) -> tuple[float, float, float]:
    """(x, y, width) of the top of the head relative to the feet, from the frame's opaque pixels."""
    a = np.asarray(im)[..., 3] > 0
    ys = np.flatnonzero(a.any(axis=1))
    if not len(ys):
        return 0.0, 0.0, 1.0
    top, bottom = ys[0], ys[-1]
    band = a[top:top + max(2, int((bottom - top + 1) * 0.15))]
    xs = np.flatnonzero(band.any(axis=0))
    return (xs[0] + xs[-1] + 1) / 2 - fx, top - fy, float(xs[-1] - xs[0] + 1)


def place_hat(hat: Image.Image, size: float, target_h: float, head: tuple[float, float, float],
              dx: float = 0.0, dy: float = 0.0) -> tuple[Image.Image, float, float]:
    """The hat scaled to `size` x the standing height (width) and the sprite pivot in its pixels."""
    w = max(1, round(size * target_h * 0.32))
    h = max(1, round(hat.height * w / hat.width))
    im = hat.convert("RGBa").resize((w, h), Image.LANCZOS).convert("RGBA")
    arr = np.asarray(im).copy()
    arr[..., 3] = np.where(arr[..., 3] >= 128, 255, 0)
    hx, hy = head[0] + dx * target_h, head[1] + dy * target_h
    # The hat's bottom-centre, sunk into the hair, sits on the top of the head.
    return Image.fromarray(arr, "RGBA"), w / 2 - hx, h * (1 - HAT_SINK) - hy


def build(project_dir: Path, renders_dir: Path, slug: str, height_m: float = 1.6, weapon: dict | None = None,
          variants: list[dict] | None = None, actions: list[str] | None = None,
          colour_ref: list[Image.Image] | None = None, frame_edits: dict | None = None,
          views: list[str] | None = None, colour_variants: list[dict] | None = None,
          hat: dict | None = None, detail: int = 1) -> dict:
    """Assemble every available sheet into renders/<slug>/sprite/<slug>.spr/.act (+ build.json).

    weapon: {"id", "name", "type", "image" (upright, see weapons.prepare), "grip", "size", "body",
    "overrides", "actions" (None = every action)} lays the weapon over every frame at the hand (weapons.anchors). Then
    <slug>.spr/.act shows both, and <slug>_body / <slug>_weapon hold each layer alone, for a game
    that swaps weapons. variants: the project's other weapons (same keys), each written as a full
    sprite sprite/weapons/<id>.spr/.act so Playtest can swap weapons the way the game would.
    colour_ref: reference images (concept, back view); each strip's colours are pulled back to
    them first (core/colour_lock.py) so separately drawn rows don't flicker.
    frame_edits: {"<action>_<view>": [...]} per-frame corrections, see apply_edits.
    views: the drawings this character has (views_for); a side view takes over W and E.
    colour_variants: palette-swap rules (core/variants.py), each written as sprite/variants/<id>.spr/.act.
    hat: {"id", "image", "size", "dx", "dy"} headgear on every frame (head_point); then <slug>_hat holds it alone.
    detail: a DETAIL key, the sprite's pixels per metre as a multiple of the classic size."""
    dsrc = dir_source(views)
    ppm = CLASSIC_PX_PER_M * (detail if detail in DETAIL else 1)
    target_h = ppm * max(0.3, height_m)
    have = {}
    for action in actions or ACTION_ORDER:
        for view in (views or BASE_VIEWS):
            p = sheet_path(project_dir, action, view)
            if p.exists():
                have[(action, view)] = frames_of(Image.open(p).convert("RGBA"))
    if not have:
        raise ValueError("No action sheets yet.")
    edit_meta = {}
    for key in list(have):
        have[key], edit_meta[key] = apply_edits(have[key], (frame_edits or {}).get(f"{key[0]}_{key[1]}"), project_dir)
    colour_stats = {}
    if colour_ref:
        from . import colour_lock
        ref = colour_lock.reference_colours(colour_ref)
        for key in list(have):
            have[key], colour_stats[f"{key[0]}_{key[1]}"] = colour_lock.lock(have[key], ref)

    # Scale: every sheet is normalised so its first (standing) frame is target_h tall.
    prepared: dict[tuple[str, str], list[tuple[Image.Image, float, float]]] = {}
    for key, frames in have.items():
        if not frames:
            continue
        anchors = strip_anchors(frames, key[0])
        ref_h = anchors[0][2] or 1
        k = target_h / ref_h
        out = []
        for f, (fx, fy, _), m in zip(frames, anchors, edit_meta[key]):
            fx, fy = fx - m["dx"], fy - m["dy"]          # a nudge moves the figure, not its feet
            size = (max(1, round(f.width * k)), max(1, round(f.height * k)))
            small = f.convert("RGBa").resize(size, Image.LANCZOS).convert("RGBA")
            if k < 0.6:     # a big shrink softens painted detail: sharpen the colour back a little
                rgb = small.convert("RGB").filter(ImageFilter.UnsharpMask(radius=0.8, percent=60, threshold=2))
                small = Image.merge("RGBA", (*rgb.split(), small.getchannel("A")))
            arr = np.asarray(small).copy()
            arr[..., 3] = np.where(arr[..., 3] >= 128, 255, 0)    # hard edges, like palette sprites
            defringe(arr)
            out.append((Image.fromarray(arr, "RGBA"), fx * k, fy * k))
        prepared[key] = out

    # Fallbacks so every action slot has something: a missing back view uses the front drawing, etc.
    def frames_for(action: str, view: str):
        return prepared.get((action, view)) or prepared.get((action, "front")) or prepared.get((action, "back"))

    def view_used(action: str, view: str) -> str:
        return next(v for v in (view, "front", "back") if (action, v) in prepared)

    actions_present = [a for a in ACTION_ORDER if frames_for(a, "front")]

    # Weapon layer per drawn frame: (image, pivot x, pivot y, behind the body?)
    def lay(wp: dict) -> dict[tuple[str, str, int], tuple[Image.Image, float, float, bool]]:
        from . import weapons as wpn
        out = {}
        for (action, view), frames in prepared.items():
            if wp.get("actions") and action not in wp["actions"]:
                continue                                  # e.g. drawn only while attacking
            marks = wpn.anchors(action, view, len(frames), wp.get("body", DEFAULT_BODY), wp.get("overrides"),
                                wp.get("type", wpn.DEFAULT_TYPE))
            for i, m in enumerate(marks):
                im, px, py = wpn.place(wp["image"], wp.get("grip", wpn.DEFAULT_GRIP),
                                       wp.get("size", wpn.DEFAULT_SIZE), target_h,
                                       (m["x"] * target_h, m["y"] * target_h), m["angle"],
                                       wpn.squash_of(wp.get("type", wpn.DEFAULT_TYPE)))
                arr = np.asarray(im).copy()
                arr[..., 3] = np.where(arr[..., 3] >= 128, 255, 0)
                if arr[..., 3].any():
                    out[(action, view, i)] = (Image.fromarray(arr, "RGBA"), px, py, m["behind"])
        return out

    held = lay(weapon) if weapon else {}
    worn = {}
    if hat:
        for (action, view), frames in prepared.items():
            if action in HAT_HIDDEN:
                continue
            for i, (im, fx, fy) in enumerate(frames):
                worn[(action, view, i)] = place_hat(hat["image"], float(hat.get("size", 1.0)), target_h,
                                                    head_point(im, fx, fy), float(hat.get("dx", 0)), float(hat.get("dy", 0)))
    others = [(v, lay(v)) for v in (variants or []) if weapon]

    # One shared palette (index 0 transparent), the other weapons' colours included.
    every = ([im for fr in prepared.values() for im, _, _ in fr] + [h[0] for h in held.values()] + [h[0] for h in worn.values()]
             + [h[0] for _, hv in others for h in hv.values()])
    pixels = np.concatenate([np.asarray(im)[np.asarray(im)[..., 3] > 0][:, :3]
                             for im in every if np.asarray(im)[..., 3].any()])
    if len(pixels) > 400_000:
        pixels = pixels[np.random.default_rng(0).choice(len(pixels), 400_000, replace=False)]
    pal_img = Image.fromarray(pixels.reshape(1, -1, 3), "RGB").quantize(colors=255, method=Image.Quantize.MEDIANCUT,
                                                                         dither=Image.Dither.NONE)
    pal = pal_img.getpalette()[:255 * 3]
    palette = [TRANSPARENT] + [tuple(pal[i:i + 3]) for i in range(0, len(pal), 3)]
    flat = [v for c in palette for v in c]

    def emit(kinds: tuple[str, ...], held=held):
        """(images, acts) for the chosen layers: ("body", "weapon"), ("body",) or ("weapon",)."""
        images, ids, cache = [], {}, {}

        def image_id(im: Image.Image) -> tuple[int, int, int, int, int]:
            a = np.asarray(im)
            ys, xs = np.nonzero(a[..., 3] > 0)
            y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
            crop = a[y0:y1, x0:x1]
            idx = np.asarray(Image.fromarray(np.ascontiguousarray(crop[..., :3]), "RGB")
                             .quantize(palette=pal_img, dither=Image.Dither.NONE), dtype=np.uint8) + 1
            idx[crop[..., 3] == 0] = 0
            key = hashlib.sha1(idx.tobytes() + bytes(idx.shape)).hexdigest()
            if key not in ids:
                p = Image.fromarray(idx, "P")
                p.putpalette(flat)
                ids[key] = len(images)
                images.append(p)
            return ids[key], int(x0), int(y0), int(x1 - x0), int(y1 - y0)

        def layer(ck, im, fx, fy, mirror):
            if ck not in cache:
                cache[ck] = image_id(im)
            idx, x0, y0, w, h = cache[ck]
            cx = x0 + w / 2 - fx          # sprite centre relative to the feet
            cy = y0 + h / 2 - fy
            if mirror:
                cx = -cx                   # mirrored drawing: centre flips around the feet
            return (round(cx), round(cy), idx, mirror, w, h)

        acts = []
        for action in actions_present:
            for d in ACT_DIRECTIONS:
                view, mirror = dsrc[d]
                view = view_used(action, view)
                frames = prepared[(action, view)]
                seq = []
                holds = [m["hold"] for m in edit_meta[(action, view)]]
                for i, event in play_order(len(frames), holds, action):
                    im, fx, fy = frames[i]
                    body = layer(("b", action, view, i), im, fx, fy, mirror) if "body" in kinds else None
                    w = held.get((action, view, i)) if "weapon" in kinds else None
                    wl = layer(("w", action, view, i), w[0], w[1], w[2], mirror) if w else None
                    hh = worn.get((action, view, i)) if "hat" in kinds else None
                    hl = layer(("h", action, view, i), hh[0], hh[1], hh[2], mirror) if hh else None
                    layers = [x for x in ((wl, body) if w and w[3] else (body, wl)) if x] + ([hl] if hl else [])
                    seq.append({"layers": layers, "event": event})
                acts.append({"delay_ms": ACTIONS[action]["ms"], "frames": seq})
        return images, acts

    action_map = []
    for action in actions_present:
        spec = ACTIONS[action]
        v = view_used(action, "front")
        order = play_order(len(prepared[(action, v)]), [m["hold"] for m in edit_meta[(action, v)]], action)
        action_map.append({"action": action, "slot": action, "frames": f"{len(order)}", "delay_ms": spec["ms"],
                           "events": {str(j): ("atk" if e == 0 else "step") for j, (_, e) in enumerate(order) if e >= 0},
                           "views": [v for v in VIEWS2D if (action, v) in prepared]})

    out = renders_dir / slug / "sprite"
    out.mkdir(parents=True, exist_ok=True)
    outputs = [(slug, ("body", "weapon", "hat"))]
    if held or worn:
        outputs += [(f"{slug}_body", ("body",))]
    if held:
        outputs += [(f"{slug}_weapon", ("weapon",))]
    if worn:
        outputs += [(f"{slug}_hat", ("hat",))]
    for stale in out.glob(f"{slug}_*.*"):           # weapon turned off: drop the split files
        if stale.suffix in (".spr", ".act") and stale.stem not in dict(outputs):
            stale.unlink()
    files, sizes, main = [], {}, None
    for name, kinds in outputs:
        images, acts = emit(kinds)
        spr_b, act_b = write_spr(images, palette), write_act(acts, ["atk", "step"])
        (out / f"{name}.spr").write_bytes(spr_b)
        (out / f"{name}.act").write_bytes(act_b)
        files += [f"sprite/{name}.spr", f"sprite/{name}.act"]
        sizes[name] = {"spr": len(spr_b), "act": len(act_b), "images": len(images)}
        main = main or (images, acts, spr_b, act_b)     # the first output is <slug> itself
    images, acts, spr_b, act_b = main

    # Colour variants: the same images and timing with a recoloured palette.
    from . import variants as var
    vdir = out / "variants"
    keep_v = set()
    for rule in colour_variants or []:
        rule = var.clean(rule)
        if not rule["id"]:
            continue
        vdir.mkdir(exist_ok=True)
        (vdir / f"{rule['id']}.spr").write_bytes(write_spr(images, var.palette(palette, rule)))
        (vdir / f"{rule['id']}.act").write_bytes(act_b)
        keep_v.add(rule["id"])
    if vdir.is_dir():
        for stale in vdir.glob("*.*"):
            if stale.stem not in keep_v:
                stale.unlink()

    # Every weapon as a full sprite for Playtest's weapon picker (the active one included).
    wdir = out / "weapons"
    shutil.rmtree(wdir, ignore_errors=True)
    variant_list = []
    if held:
        wdir.mkdir()
        for v, hv in [(weapon, held)] + others:
            if not hv:
                continue
            vi, va = emit(("body", "weapon"), held=hv)
            (wdir / f"{v['id']}.spr").write_bytes(write_spr(vi, palette))
            (wdir / f"{v['id']}.act").write_bytes(write_act(va, ["atk", "step"]))
            variant_list.append({"id": v["id"], "name": v.get("name", v["id"]), "type": v.get("type"),
                                 "file": f"sprite/weapons/{v['id']}.act"})
    report = {
        "source": "2d",
        "files": files,
        "weapon": weapon.get("id") if weapon and held else None,
        "weapons": variant_list,
        "layers": sizes,
        "actions": len(acts), "types": len(acts) // 8, "images": len(images),
        "frames": sum(len(a["frames"]) for a in acts), "palette_colors": len(palette) - 1,
        "height_px": round(target_h), "bytes": {"spr": len(spr_b), "act": len(act_b)},
        "action_map": action_map,
        "colour_lock": colour_stats,
        "swatches": var.hue_swatches(palette),
        "variants": [var.clean(r)["id"] for r in colour_variants or [] if r.get("id")],
        "variant_names": {var.clean(r)["id"]: var.clean(r)["name"] for r in colour_variants or [] if r.get("id")},
        "detail": detail if detail in DETAIL else 1,
        "game": game_points(prepared, target_h, (weapon or {}).get("body", DEFAULT_BODY), dsrc=dsrc, ppm=ppm),
        "jitter": {f"{a}_{v}": jitter_report(have[(a, v)], a) for (a, v) in prepared},
    }
    (out / "build.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (out / "game.json").write_text(json.dumps({"name": slug, **report["game"],
                                               "directions": list(ACT_DIRECTIONS),
                                               "note": "pixels relative to the feet (y down); mirror x for E/SE/NE is already applied"},
                                              indent=2), encoding="utf-8")
    return report


_FACING = {"front": "front three-quarter view, facing bottom-left, we see the face",
           "side": "pure side view (profile), facing left",
           "back": "back three-quarter view, facing top-left, we see the back of the head"}

# Prompts describe the look in plain words and never name another game: sprites made from them
# must not be steered toward someone else's art (and the drawings are meant to be sold).
ART_STYLE = "classic 2.5D isometric MMORPG sprite style: a hand-drawn anime look, clean dark outlines, soft cel shading, readable at a small size"

# Body proportions, chosen per project (projects.json "sprite2d.body"). Kept out of description_en
# so one description works for every choice. Slender is the default: classic isometric MMORPG
# sprites are not chibi.
BODY = {
    "slender":   {"th": "Slender (~5 heads tall)", "en": "Body proportions of classic isometric MMORPG character sprites: a slender, "
                  "lean build about 5 heads tall, a slightly large head on a slim body with long thin arms and legs. "
                  "NOT chibi, NOT super-deformed, no oversized head, even if the reference is drawn that way."},
    "chibi":     {"th": "Chibi", "en": "Chibi / super-deformed proportions: about 2.5 heads tall, big head, short body and limbs."},
    "reference": {"th": "As in the reference", "en": "Keep exactly the body proportions of the attached reference."},
}
DEFAULT_BODY = "slender"


def body_sentence(body: str) -> str:
    return BODY.get(body, BODY[DEFAULT_BODY])["en"]


EMPTY_HANDS = ("Draw the character EMPTY-HANDED in every frame: no weapon, no staff, nothing in the hands, "
               "even if the reference shows one. The right hand is a closed fist, posed exactly as if gripping a "
               "weapon's handle (the weapon is added later as a separate layer).")
EMPTY_HANDS_BOW = ("Draw the character EMPTY-HANDED in every frame: no bow, no arrows, nothing in the hands, even if "
                   "the reference shows them. The LEFT hand is a closed fist posed exactly as if holding a bow's grip; "
                   "the right hand pinches as if drawing a bowstring (the bow is added later as a separate layer).")


EMPTY_HANDS_ATTACK_ONLY = ("Draw the character EMPTY-HANDED in every frame: no weapon, nothing in the hands, even if "
                           "the reference shows one (the weapon is added later as a separate layer, only while "
                           "attacking). In the ATTACK and MAGIC ATTACK frames the {hand} is a closed fist posed exactly as if {grip}; "
                           "in every other action the hands are relaxed and natural.")


def empty_hands(hand: str = "r", attack_only: bool = False) -> str:
    if attack_only:
        return EMPTY_HANDS_ATTACK_ONLY.format(
            hand="LEFT hand" if hand == "l" else "right hand",
            grip="holding a bow's grip, the right hand drawing the string" if hand == "l" else "gripping a weapon's handle")
    return EMPTY_HANDS_BOW if hand == "l" else EMPTY_HANDS


# The attack row per weapon style (weapons.TYPES / pose_guide.ATTACKS); "swing" is ACTIONS["attack"].
ATTACK_MOTION = {
    "thrust": "basic attack, a stab: frame 1 ready with the weapon pointing forward, frame 2 pull the weapon back, "
              "frames 3-4 lunge forward and thrust it straight at the target (frame 4 is the hit, arm fully "
              "extended), frames 5-6 draw back to the ready pose",
    "bow": "shoot an arrow: frame 1 bow lowered in the LEFT hand, frame 2 raise the bow toward the target, frame 3 "
           "left arm straight out holding the bow, right hand pulling the string back to the chin, frame 4 release "
           "(right hand flicks back), frames 5-6 lower the bow",
    "gun": "shoot a one-handed pistol: frame 1 pistol pointing down in the right hand, frame 2 raise it, frame 3 "
           "right arm straight out aiming at the target, frame 4 fire with a small recoil kick, frames 5-6 lower it",
}


def motion(action: str, style: str | None = None) -> str:
    """What the action row shows; the attack depends on the weapon style."""
    if action == "attack" and style in ATTACK_MOTION:
        return ATTACK_MOTION[style]
    return ACTIONS[action]["motion"]


POSE_GUIDE_NOTE = ("If an image with coloured stick figures is attached (on its own, or below the character on a "
                   "reference board whose top part shows the character), the stick figures are a POSE GUIDE with this same "
                   "layout: draw the character in exactly the pose of the stick figure in the matching cell (blue limbs = "
                   "the character's LEFT arm and leg, red = RIGHT; a grey head = we see the back of the head). Copy only "
                   "the poses: never draw the stick figures, their lines or colours.")


def _intro(title: str, description: str, body: str) -> str:
    return (f"{title} for a 2D isometric game, in a {ART_STYLE}. "
            f"The character: {description.rstrip('. ')}. Draw this exact character (same face, hair, outfit and colours as the "
            f"attached reference). {body_sentence(body)}")


def _grid_prompt(title: str, description: str, slots: list[tuple[str, str]], body: str, extra_style: str,
                 style: str | None = None) -> str:
    rows = [f"- Row {r}: {a.upper()}, {_FACING[v]}. {motion(a, style)}."
            for r, (a, v) in enumerate(slots, start=1)]
    n = len(slots)
    return (
        _intro(title, description, body) + "\n\n"
        f"Layout: an even grid of {n} rows x {GRID_COLS} columns ({n * GRID_COLS} figures). Every cell is the same "
        f"size and holds exactly ONE full-body figure, centred in its cell. Frames go left to right in time order. "
        f"Rows from top to bottom:\n" + "\n".join(rows) + "\n\n"
        f"{POSE_GUIDE_NOTE}\n\n"
        f"Every figure in a row faces the same way as the row says; never mix front and back views in one row. "
        f"Whatever the character holds (weapon, staff) stays in the hand in every frame unless the row says it is dropped. "
        f"Each figure stays inside its own cell: figures, weapons and effects never touch or overlap a "
        f"neighbouring figure, lying-down frames included. Leave clear empty space between rows. "
        f"Camera: 3/4 top-down game camera (about 30 degrees above). The SAME scale in every cell, feet on the same "
        f"baseline within a row, nothing cropped. No text, no numbers, no labels, no arrows, no grid lines or "
        f"borders. Plain pure white background, flat lighting, no ground shadow. Clean game-art style with dark "
        f"outlines{(', ' + extra_style) if extra_style else ''}. Highest resolution available."
    )


def prompt_all(description: str, body: str = DEFAULT_BODY, extra_style: str = "", style: str | None = None,
               actions: list[str] | None = None, views: list[str] | None = None) -> str:
    """One prompt for the whole character: every action, both drawings, one grid image (see
    all_slots). The per-action prompts stay for redrawing an action that came out wrong."""
    return _grid_prompt("Complete sprite sheet", description, all_slots(actions, views), body, extra_style, style)


def prompt_both(action: str, description: str, body: str = DEFAULT_BODY, extra_style: str = "",
                style: str | None = None, views: list[str] | None = None) -> str:
    """Both drawings of one action: a 2-row grid (front and back come from the same generation,
    so outfit and colours match)."""
    return _grid_prompt("Sprite animation sheet", description, [(action, v) for v in (views or BASE_VIEWS)], body, extra_style, style)


def prompt(action: str, view: str, description: str, body: str = DEFAULT_BODY, extra_style: str = "",
           style: str | None = None) -> str:
    """English prompt for one action strip (used by every image AI; per-AI wrappers live in the UI)."""
    spec, v = ACTIONS[action], VIEWS2D[view]
    return (
        _intro("Sprite animation strip", description, body) + "\n\n"
        f"Action: {motion(action, style)}.\n"
        f"Exactly {spec['frames']} frames in ONE horizontal row, left to right in time order. "
        f"{POSE_GUIDE_NOTE} "
        f"Camera: 3/4 top-down game camera (about 30 degrees above), the character {v['facing']} in EVERY frame. "
        f"Full body in every frame, same scale, feet on the same baseline, clear empty space between frames "
        f"(frames must not touch or overlap), nothing cropped. No text, no numbers, no labels, no arrows, no frames "
        f"or grid lines. Plain pure white background, flat lighting, no ground shadow. "
        f"Clean game-art style with dark outlines{(', ' + extra_style) if extra_style else ''}. "
        f"Wide image, at least 2048 px wide."
    )
