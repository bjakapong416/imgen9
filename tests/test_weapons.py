"""core/weapons.py: placing the weapon at the hand, cleaning weapon pictures, per-frame corrections."""

from __future__ import annotations

from tests.conftest import names_no_game
import numpy as np
import pytest
from PIL import Image, ImageDraw

from core import weapons

GRIP_COLOUR = (255, 0, 0)


def _weapon_with_grip_marker(grip=(0.5, 0.7), w=40, h=200):
    """Upright grey weapon with a red square centred on the grip point."""
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rectangle([w * 0.3, 0, w * 0.7, h - 1], fill=(120, 120, 140, 255))
    gx, gy = grip[0] * w, grip[1] * h
    d.rectangle([gx - 6, gy - 6, gx + 6, gy + 6], fill=GRIP_COLOUR + (255,))
    return im


def _marker_centre(img):
    a = np.asarray(img.convert("RGBA")).astype(int)
    m = (a[..., 3] > 128) & (a[..., 0] > 200) & (a[..., 1] < 60) & (a[..., 2] < 60)
    ys, xs = np.nonzero(m)
    assert len(xs), "grip marker lost"
    return xs.mean() + 0.5, ys.mean() + 0.5


@pytest.mark.parametrize("angle", [0, 90, 180, -45, 30])
@pytest.mark.parametrize("hand", [(0.0, 0.0), (12.0, -40.0), (-25.5, -60.0)])
def test_place_puts_grip_on_hand(angle, hand):
    grip = (0.5, 0.7)
    weapon = _weapon_with_grip_marker(grip)
    img, px, py = weapons.place(weapon, grip, size=1.0, height_px=100, hand=hand, angle=angle)
    gx, gy = _marker_centre(img)
    # The pivot (the feet) is at (px, py); the hand is `hand` px from it.
    assert abs(gx - (px + hand[0])) <= 2, (gx, px + hand[0])
    assert abs(gy - (py + hand[1])) <= 2, (gy, py + hand[1])


def test_place_scales_to_size():
    weapon = _weapon_with_grip_marker()
    img, _, _ = weapons.place(weapon, (0.5, 0.7), size=0.5, height_px=100, hand=(0, 0), angle=0)
    assert abs(img.height - 50) <= 1


def test_place_rotation_direction_is_clockwise():
    """angle 90 = the tip points right (clockwise from straight up, y down)."""
    weapon = _weapon_with_grip_marker()
    img, _, _ = weapons.place(weapon, (0.5, 0.7), size=1.0, height_px=200, hand=(0, 0), angle=90)
    gx, _ = _marker_centre(img)
    assert img.width > img.height
    assert gx < img.width / 2            # the grip (near the bottom end) is now on the left


def test_place_squash_narrows():
    weapon = _weapon_with_grip_marker()
    full, _, _ = weapons.place(weapon, (0.5, 0.7), 1.0, 200, (0, 0), 0)
    half, _, _ = weapons.place(weapon, (0.5, 0.7), 1.0, 200, (0, 0), 0, squash=0.5)
    assert half.width < full.width


def test_prepare_keeps_biggest_blob_and_drops_frame():
    im = Image.new("RGBA", (300, 400), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rectangle([2, 2, 297, 397], outline=(0, 0, 0, 255), width=2)          # thin inset frame
    d.rectangle([140, 50, 159, 249], fill=(150, 100, 50, 255))               # the weapon: 20 x 200
    d.rectangle([100, 330, 200, 345], fill=(0, 0, 0, 255))                   # a caption
    out = weapons.prepare(im)
    assert out.size == (20, 200)
    assert np.asarray(out)[..., 3].min() == 255


def _slanted(width):
    im = Image.new("RGBA", (400, 400), (0, 0, 0, 0))
    ImageDraw.Draw(im).line([(100, 330), (300, 70)], fill=(150, 100, 50, 255), width=width)   # leaning ~38 deg
    return im


def test_prepare_straightens_a_slanted_weapon():
    out = weapons.prepare(_slanted(40))
    assert out.height > 3 * out.width
    assert (np.asarray(out)[..., 3] > 64).sum() > 10000


def test_prepare_keeps_a_thin_slanted_weapon():
    # A 14 px staff at ~38 degrees fills only ~8 % of its upright box; the fill is judged along the
    # blob's own axis, so it is kept (and straightened) instead of being dropped.
    out = weapons.prepare(_slanted(14))
    assert (np.asarray(out)[..., 3] > 64).sum() > 3000
    assert out.height > 3 * out.width


def test_prepare_empty_raises():
    with pytest.raises(ValueError):
        weapons.prepare(Image.new("RGBA", (50, 50), (0, 0, 0, 0)))


@pytest.mark.parametrize("wtype", list(weapons.TYPES))
def test_sample_pictures_are_upright(wtype):
    img = weapons.sample(wtype)
    assert img.mode == "RGBA"
    assert img.height == 512 or img.height > img.width


def test_anchors_apply_overrides():
    base = weapons.anchors("attack", "front", 6, "slender")
    key = weapons.anchor_key("attack", "front", 2)
    ov = {key: {"dx": 0.1, "dy": -0.05, "da": 15, "behind": not base[2]["behind"]}}
    out = weapons.anchors("attack", "front", 6, "slender", ov)
    assert out[2]["x"] == pytest.approx(base[2]["x"] + 0.1)
    assert out[2]["y"] == pytest.approx(base[2]["y"] - 0.05)
    assert out[2]["angle"] == pytest.approx(base[2]["angle"] + 15)
    assert out[2]["behind"] is (not base[2]["behind"])
    for i in (0, 1, 3, 4, 5):
        assert out[i]["x"] == base[i]["x"] and out[i]["override"] == {}


def test_anchors_override_keys_are_per_type():
    assert weapons.anchor_key("walk", "back", 1) == "walk_back_1"
    assert weapons.anchor_key("walk", "back", 1, "bow") == "bow:walk_back_1"
    ov = {"walk_back_1": {"dx": 0.5}}
    staff = weapons.anchors("walk", "back", 6, "slender", ov)
    bow = weapons.anchors("walk", "back", 6, "slender", ov, "bow")
    bow_plain = weapons.anchors("walk", "back", 6, "slender", None, "bow")
    assert staff[1]["override"] == {"dx": 0.5}
    assert bow[1]["x"] == bow_plain[1]["x"]          # a staff correction doesn't move the bow


def test_anchors_resample_to_frame_count():
    for n in (1, 4, 6, 7, 9):
        assert len(weapons.anchors("idle", "front", n, "slender")) == n


def test_weapon_prompt_names_no_other_game():
    for t in weapons.TYPES:
        p = weapons.prompt("a short oak staff", t)
        assert names_no_game(p) and "a short oak staff" in p
