"""core/sprite2d.py: grid splitting, frame finding, sheet checks, prompts and the .spr/.act build."""

from __future__ import annotations

from tests.conftest import names_no_game
import json

import numpy as np
import pytest
from PIL import Image

from core import sprite2d
from core.spr_act import ACT_DIRECTIONS, read_act, read_spr
from tests.conftest import (PROP_COLOUR, ROW_COLOURS, WALK_STANCES, colour_count, dominant_colour, draw_figure,
                            make_grid, make_strip)

N_ROWS = len(sprite2d.all_slots())   # 5 base actions x 2 views


# ───────── all_slots ─────────

def test_all_slots_default_is_base_actions_front_then_back():
    slots = sprite2d.all_slots()
    assert len(slots) == 10
    assert slots[:2] == [("idle", "front"), ("idle", "back")]
    assert [a for a, _ in slots[::2]] == sprite2d.BASE_ACTIONS
    assert all(v == ("front" if i % 2 == 0 else "back") for i, (_, v) in enumerate(slots))


def test_all_slots_with_magic_adds_cast_rows():
    slots = sprite2d.all_slots(sprite2d.actions_for(magic=True))
    assert len(slots) == 12
    assert ("cast", "front") in slots and ("cast", "back") in slots
    assert ("cast", "front") not in sprite2d.all_slots(sprite2d.actions_for(magic=False))


# ───────── split_grid / split_all ─────────

def _check_rows(strips, n_cols):
    for r, s in enumerate(strips):
        assert dominant_colour(s) == ROW_COLOURS[r], f"row {r} got the wrong figures"
        assert len(sprite2d.frames_of(s)) == n_cols, f"row {r}"


def test_split_grid_even_10_rows():
    strips = sprite2d.split_grid(make_grid(10, 6), 10)
    assert len(strips) == 10
    _check_rows(strips, 6)


def test_split_grid_seven_columns():
    """AIs often draw 7 frames when asked for 6: only the row count is trusted."""
    strips = sprite2d.split_grid(make_grid(10, 7), 10)
    _check_rows(strips, 7)


def test_split_grid_tight_row_spacing():
    strips = sprite2d.split_grid(make_grid(10, 6, cell=(64, 86), fig_h=80), 10)
    _check_rows(strips, 6)


def test_split_grid_tall_prop_stays_with_its_figure():
    """A staff reaching up into the row above goes whole to its own figure's row."""
    sheet = make_grid(10, 6, prop_row=4, prop_len=40)
    strips = sprite2d.split_grid(sheet, 10)
    assert colour_count(strips[4], PROP_COLOUR) > 50
    assert colour_count(strips[3], PROP_COLOUR) == 0
    _check_rows(strips, 6)


def test_split_grid_empty_sheet_raises():
    with pytest.raises(ValueError):
        sprite2d.split_grid(Image.new("RGBA", (300, 300), (0, 0, 0, 0)), 10)


def test_split_grid_missing_row_raises():
    sheet = make_grid(10, 6)
    arr = np.asarray(sheet).copy()
    arr[:100, :, 3] = 0                      # wipe the first row
    with pytest.raises(ValueError):
        sprite2d.split_grid(Image.fromarray(arr, "RGBA"), 10)


def test_split_all_keys_and_order():
    out = sprite2d.split_all(make_grid(N_ROWS, 6))
    assert list(out) == sprite2d.all_slots()
    for r, key in enumerate(sprite2d.all_slots()):
        assert dominant_colour(out[key]) == ROW_COLOURS[r]


def test_split_all_magic_grid():
    actions = sprite2d.actions_for(magic=True)
    out = sprite2d.split_all(make_grid(12, 6), actions)
    assert len(out) == 12
    assert dominant_colour(out[("cast", "back")]) == ROW_COLOURS[7]


# ───────── frames_of / feet_anchor ─────────

def test_frames_of_returns_frames_left_to_right_with_boxes():
    s = make_strip(6, colour=(10, 200, 10))
    boxes: list = []
    frames = sprite2d.frames_of(s, boxes)
    assert len(frames) == 6 == len(boxes)
    xs = [b[0] for b in boxes]
    assert xs == sorted(xs)
    for f in frames:
        assert f.mode == "RGBA"
        assert 70 <= f.height <= 100


def test_frames_of_keeps_short_lying_frame():
    s = make_strip(6, heights=[80, 80, 80, 80, 80, 30])
    assert len(sprite2d.frames_of(s)) == 6


def test_feet_anchor_on_known_figure():
    canvas = Image.new("RGBA", (100, 120), (0, 0, 0, 0))
    fig = draw_figure(80, stance=(-10, 10))
    canvas.alpha_composite(fig, (20, 30))
    x, y, h = sprite2d.feet_anchor(canvas)
    assert y == 30 + 80 - 1 or y == 30 + 80            # bottom of the lowest opaque row
    assert abs(x - (20 + 24)) <= 2                    # between the two feet
    assert 75 <= h <= 80


def test_feet_anchor_empty_image():
    assert sprite2d.feet_anchor(Image.new("RGBA", (40, 50), (0, 0, 0, 0))) == (20, 50, 0)


# ───────── jitter_report ─────────

def _frames(**kw):
    return sprite2d.frames_of(make_strip(**kw))


def test_jitter_report_good_walk():
    rep = sprite2d.jitter_report(_frames(n=6, stances=WALK_STANCES), "walk")
    assert rep["frames"] == 6 and rep["expected"] == 6
    assert rep["height_spread_pct"] < 12
    assert rep["stride_var"] >= sprite2d.WALK_MIN_STRIDE_VAR
    assert rep["ok"], rep["issues"]


def test_jitter_report_walk_that_repeats_one_step():
    rep = sprite2d.jitter_report(_frames(n=6, stances=[(-10, 10)]), "walk")
    assert rep["stride_var"] < sprite2d.WALK_MIN_STRIDE_VAR
    assert not rep["ok"] and rep["issues"]


def test_jitter_report_frame_count_issue():
    rep = sprite2d.jitter_report(_frames(n=3), "idle")
    assert rep["frames"] == 3
    assert not rep["ok"]
    assert any("3" in s for s in rep["issues"])


def test_jitter_report_seven_frames_is_fine():
    assert sprite2d.jitter_report(_frames(n=7), "idle")["ok"]


def test_jitter_report_height_wobble():
    rep = sprite2d.jitter_report(_frames(n=6, heights=[80, 64, 80, 64, 80, 64]), "idle")
    assert rep["height_spread_pct"] >= 12
    assert not rep["ok"]


def test_jitter_report_die_may_change_height():
    rep = sprite2d.jitter_report(_frames(n=6, heights=[80, 80, 70, 60, 45, 30]), "die")
    assert rep["height_spread_pct"] < 12


def test_jitter_report_no_frames():
    assert sprite2d.jitter_report([], "idle") == {"frames": 0}


# ───────── prompts ─────────

DESC = "a young mage with short silver hair, a long blue coat and brown boots"


@pytest.mark.parametrize("style", [None, "thrust", "bow", "gun"])
def test_prompts_name_no_other_game_and_carry_description(style):
    texts = [sprite2d.prompt_all(DESC, style=style),
             sprite2d.prompt_all(DESC, style=style, actions=sprite2d.actions_for(True))]
    for a in sprite2d.ACTION_ORDER:
        texts.append(sprite2d.prompt_both(a, DESC, style=style))
        for v in sprite2d.VIEWS2D:
            texts.append(sprite2d.prompt(a, v, DESC, style=style))
    for body in sprite2d.BODY:
        texts.append(sprite2d.prompt_all(DESC, body=body))
    for t in texts:
        assert names_no_game(t)
        assert DESC in t


def test_prompt_all_lists_every_row():
    t = sprite2d.prompt_all(DESC)
    assert "10 rows x 6 columns" in t
    for r in range(1, 11):
        assert f"Row {r}:" in t
    assert "Row 11:" not in t
    assert "12 rows" in sprite2d.prompt_all(DESC, actions=sprite2d.actions_for(True))


def test_prompt_uses_weapon_style_attack():
    assert sprite2d.ATTACK_MOTION["bow"] in sprite2d.prompt("attack", "front", DESC, style="bow")
    assert sprite2d.ACTIONS["attack"]["motion"] in sprite2d.prompt("attack", "front", DESC, style="swing")


# ───────── build ─────────

def _write_sheets(project_dir, actions=None):
    sheets = project_dir / "sheets"
    sheets.mkdir(parents=True)
    for r, (action, view) in enumerate(sprite2d.all_slots(actions)):
        stances = WALK_STANCES if action == "walk" else None
        heights = [80, 80, 70, 55, 40, 30] if action == "die" else None
        make_strip(6, colour=ROW_COLOURS[r], stances=stances, heights=heights).save(
            sprite2d.sheet_path(project_dir, action, view))


def _check_mirroring(act, n_types):
    """Mirrored directions show the same drawing as their source, flipped around the feet."""
    pairs = [("W", "E"), ("SW", "SE"), ("NW", "N"), ("NW", "NE")]
    for t in range(n_types):
        for src, dst in pairs:
            a = act.actions[t * 8 + ACT_DIRECTIONS.index(src)]
            b = act.actions[t * 8 + ACT_DIRECTIONS.index(dst)]
            assert len(a.frames) == len(b.frames)
            for fa, fb in zip(a.frames, b.frames):
                assert len(fa.layers) == len(fb.layers)
                for la, lb in zip(fa.layers, fb.layers):
                    assert la.sprite_index == lb.sprite_index
                    assert la.mirror is False and lb.mirror is True
                    assert abs(la.x + lb.x) <= 1 and la.y == lb.y


def test_build_end_to_end(tmp_path):
    proj, renders = tmp_path / "proj", tmp_path / "renders"
    _write_sheets(proj)
    rep = sprite2d.build(proj, renders, "hero")
    built = renders / "hero" / "sprite"
    act = read_act((built / "hero.act").read_bytes())
    spr = read_spr((built / "hero.spr").read_bytes())
    n_types = len(sprite2d.BASE_ACTIONS)
    assert rep["types"] == n_types and rep["actions"] == n_types * 8
    assert len(act.actions) == n_types * 8
    assert act.trailing_bytes == 0
    assert act.events == ["atk", "step"]
    assert len(spr.palette_images) == rep["images"] > 0
    for a in act.actions:
        assert len(a.frames) == 6
        for f in a.frames:
            assert len(f.layers) == 1
            assert 0 <= f.layers[0].sprite_index < len(spr.palette_images)
    # attack carries one "atk" event, walk two footsteps
    attack = act.actions[sprite2d.BASE_ACTIONS.index("attack") * 8]
    assert [f.event_index for f in attack.frames].count(0) == 1
    walk = act.actions[sprite2d.BASE_ACTIONS.index("walk") * 8]
    assert [f.event_index for f in walk.frames].count(1) == 2
    assert abs(act.actions[0].delay_ms - sprite2d.ACTIONS["idle"]["ms"]) < 0.01
    _check_mirroring(act, n_types)
    saved = json.loads((built / "build.json").read_text(encoding="utf-8"))
    assert saved["types"] == n_types and saved["weapon"] is None
    assert not (built / "hero_body.spr").exists()
    assert saved["jitter"]["walk_front"]["frames"] == 6


def test_build_with_weapon_layer(tmp_path):
    from core import weapons

    proj, renders = tmp_path / "proj", tmp_path / "renders"
    _write_sheets(proj)
    weapon = {"id": "w1", "name": "Staff", "type": "staff", "image": weapons.sample("staff")}
    variant = {"id": "w2", "name": "Sword", "type": "sword", "image": weapons.sample("sword")}
    rep = sprite2d.build(proj, renders, "hero", weapon=weapon, variants=[variant])
    built = renders / "hero" / "sprite"
    assert rep["weapon"] == "w1"
    assert {w["id"] for w in rep["weapons"]} == {"w1", "w2"}
    for name in ("hero", "hero_body", "hero_weapon", "weapons/w1", "weapons/w2"):
        assert (built / f"{name}.spr").exists() and (built / f"{name}.act").exists(), name
    n_types = len(sprite2d.BASE_ACTIONS)
    full = read_act((built / "hero.act").read_bytes())
    body = read_act((built / "hero_body.act").read_bytes())
    wpn = read_act((built / "hero_weapon.act").read_bytes())
    for act in (full, body, wpn):
        assert len(act.actions) == n_types * 8
    assert all(len(f.layers) == 1 for a in body.actions for f in a.frames)
    assert any(len(f.layers) == 2 for a in full.actions for f in a.frames)
    # every frame of the full sprite = body layer + (weapon layer if any)
    for af, ab, aw in zip(full.actions, body.actions, wpn.actions):
        for ff, fb, fw in zip(af.frames, ab.frames, aw.frames):
            assert len(ff.layers) == len(fb.layers) + len(fw.layers)
    _check_mirroring(full, n_types)
    _check_mirroring(wpn, n_types)

    # Turning the weapon off again drops the split files.
    sprite2d.build(proj, renders, "hero")
    assert not (built / "hero_body.spr").exists()
    assert not (built / "weapons").exists()


def test_build_missing_back_view_falls_back_to_front(tmp_path):
    proj, renders = tmp_path / "proj", tmp_path / "renders"
    (proj / "sheets").mkdir(parents=True)
    make_strip(6).save(sprite2d.sheet_path(proj, "idle", "front"))
    rep = sprite2d.build(proj, renders, "solo")
    assert rep["types"] == 1
    act = read_act((renders / "solo" / "sprite" / "solo.act").read_bytes())
    assert len(act.actions) == 8
    assert all(len(a.frames) == 6 for a in act.actions)


def test_build_without_sheets_raises(tmp_path):
    with pytest.raises(ValueError):
        sprite2d.build(tmp_path / "empty", tmp_path / "renders", "none")


def test_detail_scales_pixels_per_metre(tmp_path):
    proj, renders = tmp_path / "proj", tmp_path / "renders"
    _write_sheets(proj)
    one = sprite2d.build(proj, renders, "hero")
    two = sprite2d.build(proj, renders, "hero", detail=2)
    assert two["detail"] == 2
    assert abs(two["height_px"] - 2 * one["height_px"]) <= 1
    assert abs(two["game"]["pixels_per_metre"] - 2 * one["game"]["pixels_per_metre"]) < 0.05
    assert abs(two["game"]["height_m"] - one["game"]["height_m"]) < 0.05    # same character, more pixels
