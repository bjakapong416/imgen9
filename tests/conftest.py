"""Shared fixtures: synthetic sprite figures and sheets drawn with Pillow (no AI, no rembg)."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SAMPLE_ACT = ROOT / "renders" / "samples" / "sample_blockman" / "sprite" / "sample_blockman.act"

# Background removal downloads a large model on first use: tests must never reach it. With this,
# any `import rembg` (core.image_processing imports it lazily) fails at once instead.
sys.modules["rembg"] = None

# Distinct, saturated colours so a test can tell which row / layer a pixel came from.
ROW_COLOURS = [
    (220, 40, 40), (40, 160, 40), (40, 60, 220), (220, 160, 20), (160, 40, 200),
    (20, 180, 180), (240, 100, 160), (120, 80, 30), (90, 90, 90), (200, 220, 60),
    (255, 120, 0), (0, 120, 255),
]
PROP_COLOUR = (255, 255, 0)

# Walk stances: (left foot x offset, right foot x offset) from the hips; all different so the legs
# visibly alternate and no two neighbouring frames share a pose.
WALK_STANCES = [(-14, 14), (-6, 4), (-2, 10), (14, -14), (4, -6), (10, -2)]
IDLE_STANCE = (-6, 6)


def draw_figure(h: int = 80, colour=(220, 40, 40), stance=IDLE_STANCE, width: int = 48) -> Image.Image:
    """A solid little humanoid (head, torso, legs, arms) on a transparent canvas, feet on the bottom row.

    Kept chunky on purpose: views.split_sheet drops blobs that fill under 20 % of their box."""
    im = Image.new("RGBA", (width, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    c = width / 2
    head = h * 0.24
    d.ellipse([c - head / 2, 0, c + head / 2, head], fill=colour + (255,))
    hip_y = h * 0.6
    d.rectangle([c - h * 0.13, head - 1, c + h * 0.13, hip_y], fill=colour + (255,))
    for sx, off in ((-4, stance[0]), (4, stance[1])):
        d.line([(c + sx, hip_y), (c + off, h - 3)], fill=colour + (255,), width=7)
        d.ellipse([c + off - 4, h - 7, c + off + 4, h - 1], fill=colour + (255,))
    for sx in (-1, 1):
        d.line([(c + sx * h * 0.13, head + 2), (c + sx * h * 0.2, hip_y - 4)], fill=colour + (255,), width=5)
    return im


def make_strip(n: int = 6, colour=(220, 40, 40), stances=None, h: int = 80, gap: int = 14,
               heights: list[int] | None = None) -> Image.Image:
    """One row of n figures, feet on a shared baseline."""
    heights = heights or [h] * n
    stances = stances or [IDLE_STANCE] * n
    fw = 48
    H = max(heights) + 10
    strip = Image.new("RGBA", (n * (fw + gap) + gap, H), (0, 0, 0, 0))
    for i in range(n):
        fig = draw_figure(heights[i], colour, stances[i % len(stances)], fw)
        strip.alpha_composite(fig, (gap + i * (fw + gap), H - 5 - heights[i]))
    return strip


def make_grid(rows: int, cols: int = 6, cell=(64, 100), fig_h: int = 80, prop_row: int | None = None,
              prop_len: int = 40) -> Image.Image:
    """An even grid of rows x cols figures, row r coloured ROW_COLOURS[r].

    prop_row: the first figure of that row holds a tall staff (PROP_COLOUR) that reaches prop_len px
    above its own cell, into the row above."""
    cw, ch = cell
    sheet = Image.new("RGBA", (cw * cols, ch * rows), (0, 0, 0, 0))
    for r in range(rows):
        for c in range(cols):
            st = WALK_STANCES[c % len(WALK_STANCES)]
            fig = draw_figure(fig_h, ROW_COLOURS[r % len(ROW_COLOURS)], st, 48)
            x = c * cw + (cw - 48) // 2
            y = r * ch + ch - fig_h - 2
            sheet.alpha_composite(fig, (x, y))
            if prop_row == r and c == 0:
                d = ImageDraw.Draw(sheet)
                # held at the right hand, standing just clear of the figure above (touching it would
                # make one two-row blob, which split_grid cuts at the border on purpose)
                hand = (x + 48 / 2 + fig_h * 0.2, y + fig_h * 0.55)
                sx = x + 48 / 2 + 26
                d.line([hand, (sx, hand[1])], fill=PROP_COLOUR + (255,), width=4)
                d.line([(sx, hand[1]), (sx, y - prop_len)], fill=PROP_COLOUR + (255,), width=4)
    return sheet


def colour_count(img: Image.Image, colour, tol: int = 30) -> int:
    a = np.asarray(img.convert("RGBA")).astype(int)
    mask = (a[..., 3] > 128) & (np.abs(a[..., :3] - np.array(colour)).max(axis=-1) <= tol)
    return int(mask.sum())


def dominant_colour(img: Image.Image) -> tuple[int, int, int]:
    a = np.asarray(img.convert("RGBA"))
    px = a[a[..., 3] > 128][:, :3]
    vals, counts = np.unique(px.reshape(-1, 3), axis=0, return_counts=True)
    return tuple(int(v) for v in vals[np.argmax(counts)])


@pytest.fixture
def figure():
    return draw_figure


@pytest.fixture
def strip():
    return make_strip


@pytest.fixture
def grid():
    return make_grid


@pytest.fixture(scope="session")
def sample_pair():
    from core.spr_act import load_pair

    if not SAMPLE_ACT.exists():
        pytest.skip("renders/sample_blockman is missing")
    return load_pair(SAMPLE_ACT)


# Guard list for the tests only: prompts describe a look in plain words, so none of these well-known
# game / studio names may appear in them. Not used by the app.
GAME_NAMES = ("maplestory", "final fantasy", "pokemon", "diablo", "world of warcraft", "studio ghibli")


def names_no_game(text: str) -> bool:
    low = text.lower()
    return not any(n in low for n in GAME_NAMES)
