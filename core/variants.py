"""
Colour variants (palette swaps): the same sprite in other colours without redrawing anything.

A built sprite uses one shared palette, so a variant is that palette recoloured: colours whose hue is
inside a chosen range (e.g. only the red robe) get their hue turned, and their saturation and
brightness scaled. Greys and near-blacks (outlines) are never touched. Each variant is written as a
full sprite, renders/<slug>/sprite/variants/<id>.spr/.act, with the same .act as the base.
"""

from __future__ import annotations

import colorsys

import numpy as np

MIN_SAT = 0.12        # below this a colour counts as grey: left alone (outlines, metal, white)
MIN_VAL = 0.10        # near-black outlines: left alone


def _in_range(h: float, lo: float, hi: float) -> bool:
    """Hue (degrees) inside [lo, hi], wrapping past 360 (e.g. 330..30 = reds)."""
    h, lo, hi = h % 360, lo % 360, hi % 360
    return lo <= h <= hi if lo <= hi else (h >= lo or h <= hi)


def clean(rule: dict) -> dict:
    """A variant rule with safe values: name, hue_from/hue_to (deg), shift (deg), sat, light (x)."""
    def num(k, d, lo, hi):
        try:
            return max(lo, min(hi, float(rule.get(k, d))))
        except (TypeError, ValueError):
            return d
    return {"id": str(rule.get("id", ""))[:12], "name": str(rule.get("name", "") or "variant")[:40],
            "hue_from": num("hue_from", 0, 0, 360), "hue_to": num("hue_to", 360, 0, 360),
            "shift": num("shift", 0, -360, 360), "sat": num("sat", 1, 0, 3), "light": num("light", 1, 0.2, 2)}


def recolour(rgb: tuple[int, int, int], rule: dict) -> tuple[int, int, int]:
    r, g, b = (c / 255 for c in rgb)
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    if s < MIN_SAT or v < MIN_VAL or not _in_range(h * 360, rule["hue_from"], rule["hue_to"]):
        return tuple(int(c) for c in rgb)
    h = ((h * 360 + rule["shift"]) % 360) / 360
    s = min(1.0, s * rule["sat"])
    v = min(1.0, v * rule["light"])
    return tuple(int(round(c * 255)) for c in colorsys.hsv_to_rgb(h, s, v))


def palette(base: list[tuple[int, int, int]], rule: dict) -> list[tuple[int, int, int]]:
    """The base palette recoloured; index 0 (transparent) stays as it is."""
    return [base[0]] + [recolour(c, rule) for c in base[1:]]


def recolour_image(rgba: np.ndarray, rule: dict) -> np.ndarray:
    """An RGBA image recoloured the same way (for previews), via its unique colours."""
    out = rgba.copy()
    m = out[..., 3] > 0
    px = out[m][:, :3]
    if not len(px):
        return out
    uniq, inv = np.unique(px, axis=0, return_inverse=True)
    mapped = np.array([recolour(tuple(int(x) for x in c), rule) for c in uniq], dtype=np.uint8)
    out[..., :3][m] = mapped[inv.ravel()]
    return out


def hue_swatches(base: list[tuple[int, int, int]], n: int = 12) -> list[dict]:
    """The sprite's main saturated colours with their hue, so the user can pick a range by eye."""
    out = []
    for c in base[1:]:
        r, g, b = (x / 255 for x in c)
        h, s, v = colorsys.rgb_to_hsv(r, g, b)
        if s >= MIN_SAT and v >= MIN_VAL:
            out.append({"rgb": "#%02x%02x%02x" % tuple(c), "hue": round(h * 360), "weight": s * v})
    out.sort(key=lambda d: -d["weight"])
    picked = []
    for d in out:                                  # keep distinct hues
        if all(min(abs(d["hue"] - p["hue"]), 360 - abs(d["hue"] - p["hue"])) > 12 for p in picked):
            picked.append(d)
        if len(picked) >= n:
            break
    return sorted(({"rgb": p["rgb"], "hue": p["hue"]} for p in picked), key=lambda d: d["hue"])
