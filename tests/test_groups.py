"""Projects group characters: shared settings, moving, one zip for the whole project, file lookup."""

from __future__ import annotations

import io
import json
import zipfile

import pytest

from tests.conftest import draw_figure, make_strip


def _store(tmp_path):
    from core.config import settings
    from core.projects import ProjectStore
    return ProjectStore(tmp_path / "projects", tmp_path / "renders", settings)


def _character(tmp_path, slug, group="my_characters", desc="a young knight in silver armour"):
    d = tmp_path / "projects" / group / slug
    (d / "views").mkdir(parents=True)
    for f in ("cutout.png", "concept.png", "views/front.png"):
        draw_figure().save(d / f)
    (d / "project.json").write_text(json.dumps({
        "format": "project/1", "slug": slug, "created": "2026-01-01T00:00:00", "notes": {}, "render": {},
        "profile": {"name_th": slug.title(), "name_en": slug, "body_type": "humanoid", "description_en": desc,
                    "animations": [], "skills": [], "features": []}}), encoding="utf-8")
    return d


def test_default_project_exists_and_lists_its_characters(tmp_path):
    store = _store(tmp_path)
    _character(tmp_path, "hero")
    store.layout.forget()
    groups = store.list_groups()
    assert groups[0]["id"] == "my_characters" and groups[0]["characters"] == 1
    assert [c["group"] for c in store.list()] == ["my_characters"]
    assert store.get("hero")["group"]["id"] == "my_characters"


def test_project_settings_reach_every_prompt(tmp_path):
    store = _store(tmp_path)
    g = store.create_group("Enter Adventure")
    assert g["id"] == "enter_adventure" and g["name"] == "Enter Adventure"
    _character(tmp_path, "orc", group=g["id"])
    store.layout.forget()
    store.update_group(g["id"], {"style_en": "dark fantasy, hand-painted look", "body": "chibi", "detail": 2})
    p = store.get("orc")
    assert "Art style of this game: dark fantasy, hand-painted look" in p["sheets2d_prompt_all"]
    assert p["sheets2d_body"]["value"] == "chibi" and p["sheets2d_detail"]["value"] == 2
    store.update("orc", {"sprite2d": {"body": "slender"}})                     # a character can override
    assert store.get("orc")["sheets2d_body"]["value"] == "slender"
    with pytest.raises(Exception):
        store.update_group(g["id"], {"body": "giant"})


def test_move_and_delete_project(tmp_path):
    store = _store(tmp_path)
    g = store.create_group("Side game")
    _character(tmp_path, "hero")
    store.layout.forget()
    with pytest.raises(Exception):
        store.delete_group("my_characters")
    moved = store.move_character("hero", g["id"])
    assert moved["group"]["id"] == g["id"]
    assert (tmp_path / "projects" / g["id"] / "hero" / "project.json").exists()
    with pytest.raises(Exception):
        store.delete_group(g["id"])                     # not empty
    store.move_character("hero", "my_characters")
    assert store.delete_group(g["id"]) == {"deleted": g["id"]}


def test_whole_project_zip_and_render_lookup(tmp_path):
    store = _store(tmp_path)
    d = _character(tmp_path, "hero")
    store.layout.forget()
    with pytest.raises(Exception):
        store.export_group_zip("my_characters")         # nothing built yet
    (d / "sheets").mkdir()
    make_strip().save(d / "sheets" / "idle_front.png")
    store.build_2d("hero")
    assert (tmp_path / "renders" / "my_characters" / "hero" / "sprite" / "hero.act").exists()
    assert store.get("hero")["renders"]["sprite"]["sprite_id"].startswith("own_")
    data, name = store.export_group_zip("my_characters")
    names = zipfile.ZipFile(io.BytesIO(data)).namelist()
    assert name == "my_characters.zip" and "my_characters/hero/sprite/hero.act" in names
    assert store.layout.render_dir("hero") == tmp_path / "renders" / "my_characters" / "hero"
