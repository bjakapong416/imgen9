"""
Colour lock: pull the colours of AI-drawn frames back to the character's reference image.

Every AI generation comes out a little lighter, darker or tinted, so rows drawn separately (or even
rows of one grid) don't quite match, and the sprite flickers between actions. Here each strip's
colours are grouped into a few dozen clusters (cloth, skin, hair and their shades); every cluster
that sits close to a reference colour is the same material drifted, so it is moved toward that
colour as a whole (shading inside the cluster is kept). Clusters far from anything in the
reference (effects, details the reference doesn't show) are left alone. Pure numpy/Pillow.
"""

from __future__ import annotations

import numpy as np
from PIL import Image

CLUSTERS = 48          # colour groups per strip
REF_COLOURS = 64       # colours taken from the reference
MAX_SHIFT = 56.0       # RGB distance: closer than this = same material, drifted
STRENGTH = 0.85        # how far a matched cluster moves toward the reference colour


def reference_colours(images: list[Image.Image], n: int = REF_COLOURS) -> np.ndarray:
    """Main colours (n x 3, float) of the opaque pixels of the reference images."""
    px = [np.asarray(im.convert("RGBA")) for im in images if im is not None]
    px = [a[a[..., 3] > 128][:, :3] for a in px]
    px = np.concatenate([p for p in px if len(p)]) if any(len(p) for p in px) else np.zeros((0, 3), np.uint8)
    if len(px) == 0:
        return np.zeros((0, 3))
    if len(px) > 200_000:
        px = px[np.random.default_rng(0).choice(len(px), 200_000, replace=False)]
    q = Image.fromarray(px.reshape(1, -1, 3), "RGB").quantize(colors=n, method=Image.Quantize.MEDIANCUT,
                                                              dither=Image.Dither.NONE)
    used = sorted(set(np.asarray(q).ravel().tolist()))
    pal = np.array(q.getpalette()[: 3 * max(used, default=0) + 3], dtype=float).reshape(-1, 3)
    return pal[used]


def lock(frames: list[Image.Image], ref: np.ndarray, strength: float = STRENGTH,
         max_shift: float = MAX_SHIFT) -> tuple[list[Image.Image], dict]:
    """Recolour one strip's frames toward `ref` colours. Returns (frames, stats)."""
    if len(ref) == 0 or not frames:
        return frames, {"moved": 0, "clusters": 0, "mean_shift": 0.0}
    arrs = [np.asarray(f.convert("RGBA")).copy() for f in frames]
    masks = [a[..., 3] > 0 for a in arrs]
    px = np.concatenate([a[m][:, :3] for a, m in zip(arrs, masks)])
    if len(px) == 0:
        return frames, {"moved": 0, "clusters": 0, "mean_shift": 0.0}
    sample = px if len(px) <= 200_000 else px[np.random.default_rng(1).choice(len(px), 200_000, replace=False)]
    q = Image.fromarray(sample.reshape(1, -1, 3), "RGB").quantize(colors=CLUSTERS, method=Image.Quantize.MEDIANCUT,
                                                                  dither=Image.Dither.NONE)
    centres = np.array(q.getpalette()[: 3 * CLUSTERS], dtype=float).reshape(-1, 3)
    # Nearest reference colour per cluster, and the shift for clusters that are the same material.
    d = np.linalg.norm(centres[:, None, :] - ref[None, :, :], axis=2)
    near = d.argmin(axis=1)
    dist = d[np.arange(len(centres)), near]
    shift = np.where((dist < max_shift)[:, None], (ref[near] - centres) * strength, 0.0)
    out, moved = [], 0
    for a, m in zip(arrs, masks):
        if not m.any():
            out.append(Image.fromarray(a, "RGBA"))
            continue
        rgb = a[..., :3][m].astype(float)
        # Assign each pixel to its nearest cluster centre (chunked to bound memory).
        lab = np.empty(len(rgb), dtype=np.int32)
        for s in range(0, len(rgb), 65536):
            chunk = rgb[s:s + 65536]
            lab[s:s + 65536] = np.linalg.norm(chunk[:, None, :] - centres[None, :, :], axis=2).argmin(axis=1)
        new = np.clip(rgb + shift[lab], 0, 255)
        moved += int((np.abs(shift[lab]).sum(axis=1) > 1).sum())
        a[..., :3][m] = new.round().astype(np.uint8)
        out.append(Image.fromarray(a, "RGBA"))
    used = dist < max_shift
    return out, {"moved": moved, "clusters": int(used.sum()),
                 "mean_shift": round(float(np.linalg.norm(shift[used], axis=1).mean()) if used.any() else 0.0, 1)}
