"""core/spr_act.py: write_spr / write_act round trip and reading the tracked sample sprite."""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from core import spr_act
from core.spr_act import SprActError, read_act, read_spr, write_act, write_spr

PALETTE = [(255, 0, 255), (200, 30, 30), (30, 200, 30), (30, 30, 200)] + [(i, i, i) for i in range(4, 256)]


def _p_image(idx: np.ndarray) -> Image.Image:
    im = Image.fromarray(idx.astype(np.uint8), "P")
    im.putpalette([v for c in PALETTE for v in c])
    return im


def test_spr_round_trip_pixels_and_palette():
    rng = np.random.default_rng(1)
    a = rng.integers(0, 4, size=(17, 23))
    b = np.zeros((40, 300), dtype=np.uint8)       # long runs of 0 (> 255) exercise the RLE
    b[20, 290:] = 3
    c = np.full((5, 5), 255)                      # top palette index
    spr = read_spr(write_spr([_p_image(a), _p_image(b), _p_image(c)], PALETTE))
    assert spr.version == pytest.approx(2.1)
    assert len(spr.palette_images) == 3 and spr.rgba_images == []
    assert [p[:3] for p in spr.palette] == PALETTE
    for src, img in zip((a, b, c), spr.palette_images):
        rgba = np.asarray(img)
        assert rgba.shape[:2] == src.shape
        assert ((rgba[..., 3] == 0) == (src == 0)).all()          # index 0 is transparent
        nz = src != 0
        expect = np.array(PALETTE, dtype=np.uint8)[src]
        assert (rgba[..., :3][nz] == expect[nz]).all()


def test_spr_short_palette_is_padded():
    spr = read_spr(write_spr([_p_image(np.ones((2, 2)))], [(0, 0, 0), (1, 2, 3)]))
    assert len(spr.palette) == 256
    assert spr.palette[1][:3] == (1, 2, 3)


def test_act_round_trip():
    actions = [
        {"delay_ms": 100, "frames": [
            {"layers": [(3, -40, 0, False, 20, 30)], "event": -1},
            {"layers": [(-3, -41, 1, True, 21, 31), (5, -20, 2, False, 8, 9)], "event": 0},
        ]},
        {"delay_ms": 237.5, "frames": [{"layers": [], "event": 1}]},
    ]
    data = write_act(actions, ["atk", "step"])
    act = read_act(data)
    assert act.version == 2.5
    assert act.trailing_bytes == 0
    assert act.events == ["atk", "step"]
    assert len(act.actions) == 2
    assert act.actions[0].delay_ms == pytest.approx(100)
    assert act.actions[1].delay_ms == pytest.approx(237.5)
    f0, f1 = act.actions[0].frames
    l0 = f0.layers[0]
    assert (l0.x, l0.y, l0.sprite_index, l0.mirror, l0.width, l0.height) == (3, -40, 0, False, 20, 30)
    assert l0.scale_x == l0.scale_y == 1.0 and l0.color == (255, 255, 255, 255) and l0.sprite_type == 0
    assert f0.event_index == -1 and f1.event_index == 0
    assert [(l.x, l.y, l.sprite_index, l.mirror) for l in f1.layers] == [(-3, -41, 1, True), (5, -20, 2, False)]
    assert act.actions[1].frames[0].layers == [] and act.actions[1].frames[0].event_index == 1


def test_act_long_event_name_is_truncated():
    act = read_act(write_act([{"delay_ms": 100, "frames": []}], ["x" * 60]))
    assert act.events == ["x" * 39]


def test_bad_magic_raises():
    with pytest.raises(SprActError):
        read_spr(b"XX\x01\x02")
    with pytest.raises(SprActError):
        read_act(b"XX\x05\x02")
    with pytest.raises(SprActError):
        read_act(b"AC\x05\x02\x01\x00")          # truncated


def test_action_name():
    assert spr_act.action_name(0) == ("idle", "S")
    assert spr_act.action_name(8 * 2 + 6) == ("attack", "E")
    assert spr_act.action_name(8 * 12 + 3, "player") == ("cast", "NW")
    assert spr_act.action_name(8 * 40) == ("action40", "S")


def test_read_sample_blockman(sample_pair):
    act, spr = sample_pair
    assert act.trailing_bytes == 0
    assert len(act.actions) == 24 and len(act.actions) % 8 == 0
    assert "atk" in act.events
    assert len(spr.palette_images) > 0
    n = len(spr.palette_images)
    for a in act.actions:
        assert a.frames and a.delay_ms > 0
        for f in a.frames:
            for layer in f.layers:
                assert 0 <= layer.sprite_index < n
    assert any(img.getbbox() for img in spr.palette_images[:10])


def test_sample_blockman_rewrite_is_lossless(sample_pair):
    """Re-writing the sample with our writer and reading it back gives the same frames."""
    act, spr = sample_pair
    acts = [{"delay_ms": a.delay_ms,
             "frames": [{"layers": [(l.x, l.y, l.sprite_index, l.mirror, l.width, l.height) for l in f.layers],
                         "event": f.event_index} for f in a.frames]} for a in act.actions]
    again = read_act(write_act(acts, act.events))
    assert again.events == act.events
    for a, b in zip(act.actions, again.actions):
        assert a.delay_ms == pytest.approx(b.delay_ms, abs=1e-3)
        assert [[(l.x, l.y, l.sprite_index, l.mirror) for l in f.layers] for f in a.frames] == \
               [[(l.x, l.y, l.sprite_index, l.mirror) for l in f.layers] for f in b.frames]
