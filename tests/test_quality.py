"""Quality tools: colour lock, the "same character" check, per-frame edits with pauses, game points."""

from __future__ import annotations

from tests.conftest import names_no_game
import json

import numpy as np
from PIL import Image

from tests.conftest import WALK_STANCES, draw_figure, make_strip
from core import colour_lock, sprite2d


def _mean_rgb(frames):
    px = np.concatenate([np.asarray(f)[np.asarray(f)[..., 3] > 0][:, :3] for f in frames]).astype(float)
    return px.mean(axis=0)


def test_colour_lock_pulls_a_drifted_row_back():
    ref = draw_figure(colour=(200, 60, 60))
    drifted = [draw_figure(colour=(225, 80, 70)) for _ in range(4)]       # same red, drawn lighter
    locked, stats = colour_lock.lock(drifted, colour_lock.reference_colours([ref]))
    before = np.abs(_mean_rgb(drifted) - _mean_rgb([ref])).sum()
    after = np.abs(_mean_rgb(locked) - _mean_rgb([ref])).sum()
    assert stats["clusters"] > 0 and after < before * 0.5


def test_colour_lock_leaves_unrelated_colours_alone():
    ref = draw_figure(colour=(200, 60, 60))
    other = [draw_figure(colour=(30, 200, 240)) for _ in range(3)]          # far from anything in ref
    locked, _ = colour_lock.lock(other, colour_lock.reference_colours([ref]))
    assert np.allclose(_mean_rgb(locked), _mean_rgb(other), atol=1)


def test_odd_frames_catches_a_frame_in_other_colours():
    frames = [draw_figure(colour=(200, 60, 60)) for _ in range(6)]
    frames[3] = draw_figure(colour=(40, 60, 220))
    odd = sprite2d.odd_frames(frames, "attack")
    assert odd["colour"] == [3]
    assert sprite2d.odd_frames(frames, "die")["colour"] == []                # falls show other sides on purpose


def test_odd_frames_catches_a_part_that_appears_in_a_walk():
    frames = [draw_figure(stance=s) for s in WALK_STANCES]
    big = draw_figure(stance=WALK_STANCES[2], width=80)
    arr = np.asarray(big).copy()
    arr[10:60, 60:78] = (255, 255, 0, 255)                                    # a prop next to the figure
    frames[2] = Image.fromarray(arr, "RGBA")
    assert 2 in sprite2d.odd_frames(frames, "walk")["shape"]


def test_apply_edits_order_flip_hold(tmp_path):
    frames = [draw_figure(colour=c) for c in [(200, 0, 0), (0, 200, 0), (0, 0, 200)]]
    edits = [{"src": 2, "hold": 2}, {"src": 0, "flip": True, "dx": 4}, {"src": 99}, {"src": 1, "hold": 9}]
    out, meta = sprite2d.apply_edits(frames, edits, tmp_path)
    assert len(out) == 3                                                       # src 99 dropped
    assert np.array_equal(np.asarray(out[0]), np.asarray(frames[2]))
    assert np.array_equal(np.asarray(out[1]), np.asarray(frames[0].transpose(Image.FLIP_LEFT_RIGHT)))
    assert [m["hold"] for m in meta] == [2, 1, sprite2d.MAX_HOLD]
    assert meta[1]["dx"] == 4
    same, meta = sprite2d.apply_edits(frames, None, tmp_path)
    assert same is frames and all(m["hold"] == 1 for m in meta)


def test_play_order_moves_the_hit_with_its_frame():
    order = sprite2d.play_order(6, [1, 2, 3, 1, 1, 1], "attack")
    assert len(order) == 9
    hits = [j for j, (_, e) in enumerate(order) if e == 0]
    assert hits == [6] and order[6][0] == 3                                   # frame 4 (index 3) after 1+2+3 frames


def _sheets(project, actions=("idle", "walk", "attack")):
    for a in actions:
        for v, c in (("front", (200, 60, 60)), ("back", (60, 60, 200))):
            stances = WALK_STANCES if a == "walk" else None
            f = sprite2d.sheet_path(project, a, v)
            f.parent.mkdir(parents=True, exist_ok=True)
            make_strip(6, colour=c, stances=stances).save(f)


def test_build_with_edits_and_game_points(tmp_path):
    project, renders = tmp_path / "p", tmp_path / "r"
    _sheets(project)
    rep = sprite2d.build(project, renders, "hero", 1.6,
                         frame_edits={"attack_front": [{"src": i, "hold": 2 if i == 3 else 1} for i in range(6)]})
    atk = next(a for a in rep["action_map"] if a["action"] == "attack")
    assert atk["frames"] == "7" and "atk" in atk["events"].values()
    game = json.loads((renders / "hero" / "sprite" / "game.json").read_text())
    assert game["hitbox"]["h"] > 0 and game["shadow"]["rx"] > 0
    assert set(game["cast_point"]) == set(sprite2d.ACT_DIRECTIONS)
    s, e = game["cast_point"]["S"], game["cast_point"]["E"]
    assert s[0] == -e[0] and s[1] == e[1]                                     # E is the mirrored front drawing


def test_build_with_colour_ref_reports_the_lock(tmp_path):
    project, renders = tmp_path / "p", tmp_path / "r"
    _sheets(project, ("idle",))
    rep = sprite2d.build(project, renders, "hero", 1.6, colour_ref=[draw_figure(colour=(190, 50, 50))])
    assert set(rep["colour_lock"]) == {"idle_front", "idle_back"}


def test_extra_actions_add_rows_prompts_and_guides():
    acts = sprite2d.actions_for(False, ["sit", "run", "bogus"])
    assert acts == ["idle", "walk", "attack", "damage", "die", "run", "sit"]
    assert len(sprite2d.all_slots(acts)) == 14
    text = sprite2d.prompt_all("a test hero", actions=acts)
    assert "RUN" in text and "SIT" in text and names_no_game(text)
    from core import pose_guide
    for a in sprite2d.EXTRA_ACTIONS:
        assert len(pose_guide.POSES[a]) == sprite2d.ACTIONS[a]["frames"]
        assert pose_guide.render([(a, "front"), (a, "back")]).height > 0


def test_happy_jump_is_not_flagged_for_height():
    frames = [draw_figure(h=h) for h in (70, 80, 92, 84, 72, 80)]
    rep = sprite2d.jitter_report(frames, "happy")
    assert not any("wobbles" in i for i in rep["issues"])


def test_side_view_takes_over_west_and_east(tmp_path):
    from core.spr_act import ACT_DIRECTIONS, read_act
    assert sprite2d.dir_source(["front", "back"]) == sprite2d.DIR_SOURCE
    ds = sprite2d.dir_source(sprite2d.views_for(True))
    assert ds["W"] == ("side", False) and ds["E"] == ("side", True) and ds["SW"] == ("front", False)
    assert len(sprite2d.all_slots(["idle", "walk"], sprite2d.views_for(True))) == 6
    project, renders = tmp_path / "p", tmp_path / "r"
    for v, c in (("front", (200, 60, 60)), ("side", (60, 200, 60)), ("back", (60, 60, 200))):
        f = sprite2d.sheet_path(project, "idle", v)
        f.parent.mkdir(parents=True, exist_ok=True)
        make_strip(6, colour=c).save(f)
    sprite2d.build(project, renders, "hero", 1.6, views=sprite2d.views_for(True))
    act = read_act((renders / "hero" / "sprite" / "hero.act").read_bytes())
    w, e, sw = (act.actions[ACT_DIRECTIONS.index(d)].frames[0].layers[0] for d in ("W", "E", "SW"))
    assert w.sprite_index == e.sprite_index != sw.sprite_index                 # W/E share the side drawing
    assert not w.mirror and e.mirror


def test_variants_recolour_only_the_chosen_hues(tmp_path):
    from core import variants as var
    rule = var.clean({"id": "v1", "hue_from": 340, "hue_to": 20, "shift": 120})   # reds -> greens
    assert var.recolour((200, 30, 30), rule)[1] > 150                             # red turned green
    assert var.recolour((30, 30, 200), rule) == (30, 30, 200)                     # blue untouched
    assert var.recolour((20, 20, 20), rule) == (20, 20, 20)                       # outline untouched
    project, renders = tmp_path / "p", tmp_path / "r"
    _sheets(project, ("idle",))
    rep = sprite2d.build(project, renders, "hero", 1.6, colour_variants=[{"id": "v1", "name": "Green", "shift": 120,
                                                                        "hue_from": 340, "hue_to": 20}])
    vdir = renders / "hero" / "sprite" / "variants"
    assert (vdir / "v1.spr").exists() and (vdir / "v1.act").read_bytes() == (renders / "hero" / "sprite" / "hero.act").read_bytes()
    assert rep["variants"] == ["v1"] and rep["swatches"]
    sprite2d.build(project, renders, "hero", 1.6, colour_variants=[])
    assert not (vdir / "v1.spr").exists()                                         # removed variants are cleaned up


def _hat():
    from PIL import ImageDraw
    im = Image.new("RGBA", (60, 50), (0, 0, 0, 0))
    ImageDraw.Draw(im).polygon([(30, 0), (5, 45), (55, 45)], fill=(120, 40, 170, 255))
    return im


def test_hat_sits_on_the_head_and_is_hidden_lying_down(tmp_path):
    from core.spr_act import ACT_DIRECTIONS, read_act
    fig = draw_figure(h=80)
    hx, hy, hw = sprite2d.head_point(fig, fig.width / 2, fig.height)
    assert abs(hx) < 3 and abs(hy + 80) <= 1 and hw > 5                        # top of an 80 px figure
    project, renders = tmp_path / "p", tmp_path / "r"
    _sheets(project, ("idle", "walk", "attack", "damage", "die"))
    sprite2d.build(project, renders, "hero", 1.6, hat={"id": "h1", "image": _hat(), "size": 1.0})
    built = renders / "hero" / "sprite"
    assert (built / "hero_hat.spr").exists() and (built / "hero_body.spr").exists()
    act = read_act((built / "hero.act").read_bytes())
    s = ACT_DIRECTIONS.index("S")
    assert len(act.actions[0 * 8 + s].frames[0].layers) == 2                   # idle: body + hat
    assert len(act.actions[4 * 8 + s].frames[0].layers) == 1                   # die: no hat
    sprite2d.build(project, renders, "hero", 1.6)
    assert not (built / "hero_hat.spr").exists()                                  # hat off: split files removed


def test_mounted_version_is_a_new_project(tmp_path):
    from core.config import settings
    from core.projects import ProjectStore
    store = ProjectStore(tmp_path / "projects", tmp_path / "renders", settings)
    d = tmp_path / "projects" / "my_characters" / "hero"
    (d / "views").mkdir(parents=True)
    draw_figure().save(d / "cutout.png")
    draw_figure().save(d / "concept.png")
    draw_figure().save(d / "views" / "front.png")
    (d / "project.json").write_text(json.dumps({
        "format": "project/1", "slug": "hero", "created": "2026-01-01T00:00:00", "notes": {}, "render": {},
        "profile": {"name_th": "Hero", "name_en": "hero", "body_type": "humanoid", "description_en": "a young knight in silver armour",
                    "animations": [], "skills": [], "features": []},
        "sprite2d": {"body": "chibi", "magic": True, "frame_edits": {"idle_front": [{"src": 0}]}}}), encoding="utf-8")
    m = store.make_mounted("hero", "a large brown riding bird")
    assert m["slug"] == "hero_mounted" and m["mounted_from"] == "hero"
    assert m["profile"]["description_en"].endswith("riding a large brown riding bird")
    q = json.loads((tmp_path / "projects" / "my_characters" / "hero_mounted" / "project.json").read_text(encoding="utf-8"))
    assert q["sprite2d"] == {"body": "chibi"}                                  # look kept, per-drawing edits dropped
    assert (tmp_path / "projects" / "my_characters" / "hero_mounted" / "views" / "front.png").exists()
