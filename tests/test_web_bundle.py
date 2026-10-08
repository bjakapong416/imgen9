"""core/web_bundle.py: atlas + JSON + animation data + demo page from the tracked sample sprite."""

from __future__ import annotations

import io
import json

import numpy as np
import pytest
from PIL import Image

from core import sprite_images, web_bundle
from tests.conftest import SAMPLE_ACT


@pytest.fixture(scope="module")
def bundle(sample_pair):
    act, spr = sample_pair
    return web_bundle.build("sample_blockman", act, spr, sprite_images.type_names_for(SAMPLE_ACT)), act


def test_bundle_files(bundle):
    files, _ = bundle
    for name in ("sample_blockman.anim.json", "demo.html", "README.txt"):
        assert name in files
    anim = json.loads(files["sample_blockman.anim.json"])
    for atlas in anim["atlas"]:
        base = atlas[:-len(".json")]
        assert atlas in files and f"{base}.png" in files and f"{base}.webp" in files
    assert files[anim["atlas"][0][:-5] + ".png"][:8] == b"\x89PNG\r\n\x1a\n"
    demo = files["demo.html"].decode("utf-8")
    assert "__NAME__" not in demo and "__ATLAS__" not in demo
    assert anim["atlas"][0] in demo


def test_atlas_frames_reference_real_pixels(bundle):
    files, _ = bundle
    anim = json.loads(files["sample_blockman.anim.json"])
    total = 0
    for atlas in anim["atlas"]:
        sheet = json.loads(files[atlas])
        img = Image.open(io.BytesIO(files[sheet["meta"]["image"]])).convert("RGBA")
        assert (img.width, img.height) == (sheet["meta"]["size"]["w"], sheet["meta"]["size"]["h"])
        alpha = np.asarray(img)[..., 3]
        for name, fr in sheet["frames"].items():
            r = fr["frame"]
            assert r["x"] >= 0 and r["y"] >= 0 and r["w"] > 0 and r["h"] > 0
            assert r["x"] + r["w"] <= img.width and r["y"] + r["h"] <= img.height, name
            assert fr["sourceSize"] == anim["canvas"]
            ss = fr["spriteSourceSize"]
            assert ss["x"] + ss["w"] <= anim["canvas"]["w"] and ss["y"] + ss["h"] <= anim["canvas"]["h"]
            assert fr["anchor"] == anim["anchor"]
            assert alpha[r["y"]:r["y"] + r["h"], r["x"]:r["x"] + r["w"]].any(), f"{name} is empty"
            total += 1
    assert total > 0


def test_every_animation_lists_existing_frames(bundle):
    files, act = bundle
    anim = json.loads(files["sample_blockman.anim.json"])
    names = set()
    n_anims = 0
    for atlas in anim["atlas"]:
        sheet = json.loads(files[atlas])
        names |= set(sheet["frames"])
        for key, frames in sheet["animations"].items():
            assert frames, key
            assert all(f in sheet["frames"] for f in frames), key
            n_anims += 1
    assert n_anims == sum(1 for a in act.actions if a.frames)
    for t, entry in anim["actions"].items():
        assert set(entry["directions"]) == set(anim["directions"])
        assert entry["frame_ms"] > 0
        for d, key in entry["directions"].items():
            assert all(f"{key}_{i:02d}" in names for i in range(entry["frames"])), key
    assert "atk" in {e for entry in anim["actions"].values() for e in entry["events"].values()}


def test_shelf_pack_stays_in_bounds():
    sizes = [(300, 200)] * 40 + [(50, 900), (2048, 10)]
    pos, pages = web_bundle._shelf_pack(sizes)
    assert len(pages) >= 2
    for (w, h), (p, x, y) in zip(sizes, pos):
        assert x + w <= web_bundle.ATLAS_MAX and y + h <= web_bundle.ATLAS_MAX
        assert x + w <= pages[p][0] and y + h <= pages[p][1]
