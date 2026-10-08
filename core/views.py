"""
Character views (turnaround): front / front 3/4 / side / back cut-outs per project.

A turnaround sheet (several views of the same character side by side) is split into one image
per figure by looking for empty columns between them. Every later step benefits:
the 3D generator gets real side/back views instead of guessing, and the model check can compare
each view with the matching render direction (front <-> S, side <-> W/E, back <-> N).
"""

from __future__ import annotations

import numpy as np
from PIL import Image

VIEWS = ["front", "front_3q", "side", "back"]
VIEW_LABELS = {"front": "Front", "front_3q": "Three-quarter", "side": "Side", "back": "Back"}  # English: translate with _() at use
REQUIRED_VIEWS = ["front", "side", "back"]
# Which render directions show the same side of the character as each drawn view.
VIEW_TO_DIRS = {"front": ["S"], "front_3q": ["SW", "SE"], "side": ["W", "E"], "back": ["N"]}
# Typical left-to-right order of views on a turnaround sheet, by figure count.
DEFAULT_ORDER = {
    1: ["front"],
    2: ["front", "back"],
    3: ["front", "side", "back"],
    4: ["front", "front_3q", "side", "back"],
}


def split_sheet(img: Image.Image, alpha_threshold: int = 16, min_height_frac: float = 0.45,
                grow: int | None = None, boxes: list | None = None) -> tuple[list[Image.Image], list[Image.Image]]:
    """Find the character views on a turnaround sheet, in any layout (one row, 2x2 grid, ...).

    Returns (figures in reading order: left->right, top->bottom; extras such as an equipment inset).
    Blobs are found in 2D after a small dilation (so a hair strand or a hand stays with its body).
    Captions and titles are dropped for being short; frames for being mostly empty; long thin props
    (a staff in an "equipment" box) are returned as extras rather than views.
    """
    from scipy import ndimage

    a = np.asarray(img.getchannel("A")) > alpha_threshold
    if not a.any():
        return [], []
    h, w = a.shape
    separate = grow is None             # turnaround mode; animation frames pass their own small grow
    if grow is None:
        grow = max(2, int(min(h, w) * 0.006))   # turnaround views are far apart; animation frames are not
    labels, _ = ndimage.label(ndimage.binary_dilation(a, iterations=grow) if grow else a)
    labels = np.where(a, labels, 0)                  # measure on the real pixels only
    blobs = [(sl, labels[sl] == i) for i, sl in enumerate(ndimage.find_objects(labels), start=1) if sl is not None]
    if not blobs:
        return [], []
    tallest = max(sl[0].stop - sl[0].start for sl, _ in blobs)
    parts = []
    for sl, m in blobs:
        for oy, ox, pm in (_separate_touching(m, tallest * min_height_frac) if separate else [(0, 0, m)]):
            ys, xs = np.nonzero(pm)
            y0, x0 = sl[0].start + oy + ys.min(), sl[1].start + ox + xs.min()
            pm = pm[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
            bh, bw = pm.shape
            parts.append({"box": (x0, y0, x0 + bw, y0 + bh), "w": bw, "h": bh,
                          "fill": float(pm.sum()) / max(1, bw * bh), "mask": pm})
    figures, extras = [], []
    for p in parts:
        if p["h"] < tallest * min_height_frac:
            continue                                 # captions, titles, sparks
        thin_prop = p["w"] / p["h"] < 0.22 and p["h"] < tallest * 0.85   # a side view is thin but full height
        if p["fill"] < 0.2 or thin_prop:
            extras.append(p)                         # frames, props, equipment insets
        else:
            figures.append(p)

    # Reading order: group into rows by vertical centre, then left to right.
    figures.sort(key=lambda p: (p["box"][1] + p["box"][3]) / 2)
    rows: list[list[dict]] = []
    for p in figures:
        cy = (p["box"][1] + p["box"][3]) / 2
        if rows and abs(cy - rows[-1][0]["cy"]) < 0.5 * tallest:
            rows[-1].append(p)
        else:
            p["cy"] = cy
            rows.append([p])
        p.setdefault("cy", rows[-1][0]["cy"])
    ordered = [p for row in rows for p in sorted(row, key=lambda q: q["box"][0])]

    def box(p, pad=4):
        x0, y0, x1, y1 = p["box"]
        return (max(0, x0 - pad), max(0, y0 - pad), min(img.width, x1 + pad), min(img.height, y1 + pad))

    def crop(p):
        """The figure's own pixels only: a neighbour's horn or tail inside the box is cleared."""
        b = box(p)
        piece = np.asarray(img.convert("RGBA").crop(b)).copy()
        keep = np.zeros(piece.shape[:2], dtype=bool)
        x0, y0 = p["box"][0] - b[0], p["box"][1] - b[1]
        keep[y0:y0 + p["h"], x0:x0 + p["w"]] = p["mask"]
        keep = ndimage.binary_dilation(keep, iterations=2)     # keep the soft edge
        piece[..., 3] = np.where(keep, piece[..., 3], 0)
        return Image.fromarray(piece, "RGBA")

    if boxes is not None:               # caller wants where each figure sits on the sheet
        boxes.extend(box(p) for p in ordered)
    return [crop(p) for p in ordered], [crop(p) for p in extras]


ACTION_SHEET_MIN = 10   # a turnaround has 1-6 figures; an action grid has dozens


def figure_count(img: Image.Image, alpha_threshold: int = 16) -> int:
    """Separate full-height figures on a cut-out image, without the dilation split_sheet uses.

    Lets step 1 spot an action sheet (a grid of poses) dropped where a character or a turnaround
    belongs: its frames sit so close that split_sheet would merge them into a few tall pieces."""
    from scipy import ndimage

    a = np.asarray(img.getchannel("A")) > alpha_threshold
    if not a.any():
        return 0
    lab, _ = ndimage.label(a)
    found = [(sl, i) for i, sl in enumerate(ndimage.find_objects(lab), start=1) if sl is not None]
    tallest = max(sl[0].stop - sl[0].start for sl, _ in found)
    n = 0
    for sl, i in found:
        h, w = sl[0].stop - sl[0].start, sl[1].stop - sl[1].start
        if h >= 0.5 * tallest and (lab[sl] == i).sum() >= 0.2 * h * w:
            n += 1
    return n


def _separate_touching(m: np.ndarray, min_h: float) -> list[tuple[int, int, np.ndarray]]:
    """Split one blob into the figures it holds, as (y offset, x offset, mask) pieces.

    The dilation that keeps a hair strand with its body also joins views drawn close together (a
    real sheet: a 3/4 view's tail 14 px from the front view's leg). Without the dilation, a blob
    that falls into two or more full-height parts, each a fair share of the area, is several
    figures; every other pixel goes to the nearest of them."""
    from scipy import ndimage

    sub, n = ndimage.label(m)
    if n < 2:
        return [(0, 0, m)]
    total = m.sum()
    big = [i for i, sl in enumerate(ndimage.find_objects(sub), start=1)
           if sl is not None and sl[0].stop - sl[0].start >= min_h and (sub[sl] == i).sum() >= 0.15 * total]
    if len(big) < 2:
        return [(0, 0, m)]
    seed = np.where(np.isin(sub, big), sub, 0)
    _, (iy, ix) = ndimage.distance_transform_edt(seed == 0, return_indices=True)
    owner = np.where(m, seed[iy, ix], 0)
    return [(0, 0, owner == i) for i in big]


def split_figures(img: Image.Image, alpha_threshold: int = 16, min_height_frac: float = 0.45) -> list[Image.Image]:
    """The character views only (see split_sheet)."""
    return split_sheet(img, alpha_threshold, min_height_frac)[0]


def default_assignment(n: int) -> list[str]:
    order = DEFAULT_ORDER.get(n) or (DEFAULT_ORDER[4] + [f"extra_{i}" for i in range(n - 4)])
    return order[:n]


def turnaround_prompt(profile: dict) -> str:
    """Template fallback; Claude Code writes a better English prompt during classification."""
    body = profile.get("body_type", "humanoid")
    if body == "humanoid":
        pose = "A-pose with arms 30 degrees away from the body, feet shoulder-width apart"
    elif body in ("quadruped", "winged_quadruped"):
        pose = "standing on all four legs with the legs clearly separated, tail extended" + (", wings half open" if body == "winged_quadruped" else "")
    else:
        pose = "neutral standing pose with limbs clearly separated from the body"
    feats = "; ".join(profile.get("features") or [])
    return (
        "Character turnaround reference sheet for 3D modeling: the same character in front view, "
        "front three-quarter view, side view and back view, side by side in one row, full body, "
        f"{pose}, neutral expression. {profile.get('name_en', 'character').replace('_', ' ')}"
        f"{' - ' + feats if feats else ''}. No weapon in hand (weapons as a separate image). "
        "Flat even lighting, no cast shadows, plain white background, consistent proportions and "
        "colours in every view, high resolution."
    )
