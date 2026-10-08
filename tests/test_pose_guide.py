"""core/pose_guide.py: guide grid images and hand positions for weapon layers."""

from __future__ import annotations

import io
import math

import pytest
from PIL import Image

from core import pose_guide, sprite2d

ALL_SLOTS = sprite2d.all_slots(sprite2d.ACTION_ORDER)


@pytest.mark.parametrize("body", list(pose_guide.PROPORTIONS))
def test_render_size_all_slots(body):
    img = pose_guide.render(ALL_SLOTS, body)
    assert img.size == (210 * sprite2d.GRID_COLS, 260 * len(ALL_SLOTS))
    assert img.getextrema() != ((255, 255),) * 3          # something was drawn


@pytest.mark.parametrize("style,hand", [("swing", "r"), ("thrust", "r"), ("bow", "l"), ("gun", "r"), (None, None)])
def test_render_each_style(style, hand):
    slots = [("attack", "front"), ("walk", "back")]
    img = pose_guide.render(slots, style=style, hand=hand, cell=(100, 120))
    assert img.size == (100 * sprite2d.GRID_COLS, 120 * 2)


def test_every_action_has_grid_cols_poses():
    for action in sprite2d.ACTION_ORDER:
        assert len(pose_guide.poses(action)) == sprite2d.GRID_COLS
    for style in pose_guide.ATTACKS:
        assert len(pose_guide.poses("attack", style)) == sprite2d.GRID_COLS


def test_walk_arm_swings_less_with_a_weapon():
    free = pose_guide.poses("walk")
    armed = pose_guide.poses("walk", hand="r")
    assert max(abs(p["ra"]) for p in armed) < max(abs(p["ra"]) for p in free)
    assert [p["la"] for p in armed] == [p["la"] for p in free]


@pytest.mark.parametrize("action", sprite2d.ACTION_ORDER)
@pytest.mark.parametrize("view", list(sprite2d.VIEWS2D))
@pytest.mark.parametrize("wtype", ["staff", "spear", "bow", "gun"])
def test_hand_frames_finite(action, view, wtype):
    from core import weapons

    t = weapons.TYPES[wtype]
    frames = pose_guide.hand_frames(action, view, style=t["style"], hand=t["hand"], rest=t["rest"])
    assert len(frames) == len(pose_guide.poses(action, t["style"], t["hand"]))
    for f in frames:
        assert set(f) == {"x", "y", "angle", "behind"}
        assert all(math.isfinite(f[k]) for k in ("x", "y", "angle"))
        assert isinstance(f["behind"], bool)
        assert -1.5 < f["x"] < 1.5 and -1.5 < f["y"] <= 0.05   # near the body, above the feet
        assert -180 <= f["angle"] <= 180


def test_png_is_valid():
    data = pose_guide.png(sprite2d.all_slots())
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    img = Image.open(io.BytesIO(data))
    img.verify()
    assert Image.open(io.BytesIO(data)).size == (210 * 6, 260 * 10)
