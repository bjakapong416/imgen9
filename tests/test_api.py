"""API smoke tests against main.app with every data folder in a temp dir.

core.config builds its frozen `settings` from environment variables at import time, so the env is
set first and main / core.config are (re)imported inside the fixture. Nothing here creates a
project through the upload route: that runs rembg background removal (a model download).
"""

from __future__ import annotations

from tests.conftest import names_no_game
import importlib
import json
import sys

import pytest

FRESH = ("main", "core.config")


@pytest.fixture(scope="module")
def api(tmp_path_factory):
    from fastapi.testclient import TestClient

    base = tmp_path_factory.mktemp("api")
    dirs = {"PROJECTS_DIR": base / "projects", "RENDERS_DIR": base / "renders", "CACHE_DIR": base / "cache"}
    with pytest.MonkeyPatch.context() as mp:
        for k, v in dirs.items():
            mp.setenv(k, str(v))
        mp.setenv("PRELOAD_MODEL", "0")
        mp.setenv("AUTO_LOCAL_3D", "0")
        for m in FRESH:
            sys.modules.pop(m, None)
        main = importlib.import_module("main")
        assert main.settings.projects_dir == dirs["PROJECTS_DIR"]
        with TestClient(main.app) as client:
            yield client, main, dirs
        for m in FRESH:                                   # later imports see the real settings again
            sys.modules.pop(m, None)


def test_index(api):
    client, _, _ = api
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert r.headers.get("cache-control") == "no-cache"


def test_health(api):
    client, _, _ = api
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_projects_empty(api):
    client, _, _ = api
    r = client.get("/api/projects")
    assert r.status_code == 200 and r.json() == {"items": []}


def test_characters_empty(api):
    client, _, dirs = api
    r = client.get("/api/characters")
    assert r.status_code == 200
    assert r.json()["items"] == [] and r.json()["root"] == str(dirs["RENDERS_DIR"])


def test_sprite_files(api):
    client, _, _ = api
    r = client.get("/api/sprites/files")
    assert r.status_code == 200
    body = r.json()
    assert body["items"] == [] and body["total"] == 0 and "available" in body


def test_download_unknown_sprite_404(api):
    """Only sprites built in renders/ exist: any other id is unknown."""
    client, _, _ = api
    for kind in ("zip", "web", "frame"):
        r = client.get("/api/sprites/download", params={"id": "notours", "kind": kind})
        assert r.status_code == 404


def test_demo_unknown_sprite_404(api):
    client, _, _ = api
    assert client.get("/api/sprites/demo/notours/demo.html").status_code == 404


def test_demo_unknown_own_sprite_404(api):
    client, _, _ = api
    assert client.get("/api/sprites/demo/own_x/demo.html").status_code == 404


def test_unknown_project_400(api):
    client, _, _ = api
    assert client.get("/api/projects/nope").status_code == 400
    assert client.get("/api/projects/..%2Fetc").status_code in (400, 404)


def test_lang_cookie_translates_server_text(api):
    client, _, _ = api
    en = client.get("/api/sprites/download", params={"id": "notours"}, headers={"Cookie": "lang=en"}).json()["detail"]
    th = client.get("/api/sprites/download", params={"id": "notours"}, headers={"Cookie": "lang=th"}).json()["detail"]
    acc = client.get("/api/sprites/download", params={"id": "notours"}, headers={"Accept-Language": "th-TH,th;q=0.9"}).json()["detail"]
    assert en == "Unknown sprite id."
    assert th != en and any("฀" <= ch <= "๿" for ch in th)
    assert acc == th


def test_own_sprite_demo_and_download(api):
    """A sprite in RENDERS_DIR gets an own_ id that can be downloaded and played as a web demo."""
    import shutil

    from tests.conftest import SAMPLE_ACT

    if not SAMPLE_ACT.exists():
        pytest.skip("renders/sample_blockman is missing")
    client, _, dirs = api
    built = dirs["RENDERS_DIR"] / "samples" / "sample_blockman" / "sprite"
    built.mkdir(parents=True)
    for f in SAMPLE_ACT.parent.iterdir():
        shutil.copy(f, built / f.name)
    try:
        items = client.get("/api/sprites/files").json()["items"]
        own = [i for i in items if i["id"].startswith("own_") and i["name"] == "sample_blockman"]
        assert own, items
        sid = own[0]["id"]
        demo = client.get(f"/api/sprites/demo/{sid}/demo.html")
        assert demo.status_code == 200 and "text/html" in demo.headers["content-type"]
        anim = client.get(f"/api/sprites/demo/{sid}/sample_blockman.anim.json")
        assert anim.status_code == 200 and anim.json()["name"] == "sample_blockman"
        assert client.get(f"/api/sprites/demo/{sid}/nothing.txt").status_code == 404
        frame = client.get("/api/sprites/download", params={"id": sid, "kind": "frame"})
        assert frame.status_code == 200 and frame.content[:4] == b"\x89PNG"
    finally:
        shutil.rmtree(dirs["RENDERS_DIR"] / "samples")


def test_project_lang_cookie(api):
    """A hand-written project (no upload, so no rembg): its server-made labels follow the cookie."""
    from PIL import Image

    client, _, dirs = api
    d = dirs["PROJECTS_DIR"] / "my_characters" / "demo_hero"
    (d / "views").mkdir(parents=True)
    Image.new("RGBA", (40, 80), (200, 50, 50, 255)).save(d / "cutout.png")
    project = {"format": "project/1", "slug": "demo_hero", "source_file": "x.png", "notes": {},
               "profile": {"name_th": "Hero", "name_en": "hero", "body_type": "humanoid",
                           "description_en": "a knight in silver armour"},
               "render": {"ppm": 64, "size": 256, "step": 2, "mirror": False}}
    (d / "project.json").write_text(json.dumps(project), encoding="utf-8")
    try:
        assert [p["slug"] for p in client.get("/api/projects").json()["items"]] == ["demo_hero"]
        en = client.get("/api/projects/demo_hero", headers={"Cookie": "lang=en"})
        assert en.status_code == 200, en.text
        th = client.get("/api/projects/demo_hero", headers={"Cookie": "lang=th"}).json()
        en = en.json()
        assert "a knight in silver armour" in en["sheets2d_prompt_all"]
        assert names_no_game(en["sheets2d_prompt_all"])
        assert en["sheets2d_body"]["options"]["chibi"] == "Chibi"
        assert en["sheets2d_body"]["options"] != th["sheets2d_body"]["options"]
        assert json.dumps(en["steps"]) != json.dumps(th["steps"])
        guide = client.get("/api/projects/demo_hero/pose-guide")
        assert guide.status_code == 200 and guide.content[:4] == b"\x89PNG"
    finally:
        import shutil

        shutil.rmtree(d)

